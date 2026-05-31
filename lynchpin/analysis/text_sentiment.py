"""Per-day sentiment / emotion mood signal derived from the operator's own text.

The operator has substantial written corpora (reddit comments, wykop posts,
messenger messages, SMS, polylogue chat transcripts) but no measured mood
signal. This module extracts a daily sentiment score and emotion distribution
from that text, providing a ``MoodDay`` per day for downstream correlation
against sleep, HRV, deep-work, substance, and focus data.

DESIGN
------
* ``score_texts(texts)`` is the single inference entry point. It is pluggable —
  the actual model call is isolated in ``_infer_batch()`` so tests can monkeypatch
  without loading real models.
* Local HuggingFace models are preferred (RTX 3080 available); transformers /
  torch are lazy-imported so importing this module never triggers a slow GPU
  initialisation. If the packages are absent, ``_load_backends()`` raises
  ``SourceUnavailableError`` with install guidance.
* Models are module-level cached (loaded once per process).
* ``daily_mood(start, end)`` aggregates over a pluggable corpus of text sources.
  Only days with actual text are emitted (missing != zero).

CAVEATS
-------
* Sentiment of the operator's own writing is a noisy proxy for ground-truth
  mood. Selection bias is significant: days with no written text contribute
  nothing; communication style adapts to audience.
* The twitter-roberta model was trained on English tweets; multilingual text
  (the operator writes Polish on Wykop) will score less reliably.
* Emotion labels are English-centric; seven basic emotions are reported.
  Probabilities should be treated as rough indicators, not clinical assessments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator, Optional, Sequence

from ..core.errors import SourceUnavailableError
from ..core.primitives import logical_date

logger = logging.getLogger(__name__)

# ── Model identifiers ─────────────────────────────────────────────────────────

#: HuggingFace model card for sentiment polarity [-1, +1].
SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"

#: HuggingFace model card for emotion classification (7 labels).
EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"

#: All emotion labels in canonical order.
EMOTION_LABELS: tuple[str, ...] = (
    "anger",
    "disgust",
    "fear",
    "joy",
    "neutral",
    "sadness",
    "surprise",
)

# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SentimentScore:
    """Sentiment + emotion probabilities for one text.

    ``sentiment`` is in [-1, 1] (−1 = most negative, +1 = most positive).
    ``emotion_probs`` maps each of the seven basic emotion labels to its
    probability (sums to 1.0 within the emotion model's softmax).
    ``dominant_emotion`` is the argmax label.
    ``word_count`` is the pre-tokenisation whitespace-split word count.
    """

    sentiment: float  # [-1, 1]
    dominant_emotion: str
    emotion_probs: dict[str, float]
    word_count: int


@dataclass(frozen=True)
class MoodDay:
    """Aggregated mood signal for one logical calendar day.

    Only days with at least one scored text are emitted (missing != zero).
    ``mean_sentiment`` and ``dominant_emotion`` summarise the day.
    ``sources`` records which text corpora contributed.
    """

    date: date
    mean_sentiment: float   # mean across all scored texts for this day
    dominant_emotion: str   # argmax of mean emotion prob distribution
    message_count: int      # number of individual texts scored
    total_words: int        # total word count across all texts
    emotion_means: dict[str, float]  # mean prob per emotion label
    sources: frozenset[str] = field(default_factory=frozenset)


# ── Model cache (module-level, loaded at most once per process) ───────────────

_sentiment_pipeline: Optional[object] = None
_emotion_pipeline: Optional[object] = None


def _load_backends() -> tuple[object, object]:
    """Load (and cache) the HF sentiment and emotion pipelines.

    Lazy-imports ``transformers`` and ``torch``. If they are unavailable,
    raises ``SourceUnavailableError`` with install guidance. The models are
    cached after first load so subsequent calls are cheap.

    Returns (sentiment_pipeline, emotion_pipeline).
    """
    global _sentiment_pipeline, _emotion_pipeline
    if _sentiment_pipeline is not None and _emotion_pipeline is not None:
        return _sentiment_pipeline, _emotion_pipeline

    try:
        from transformers import pipeline  # type: ignore
    except ImportError as exc:
        raise SourceUnavailableError(
            source="text_sentiment",
            path=None,
            reason=(
                "transformers package is not available. Install with: "
                "pip install transformers torch  "
                "(or: nix shell nixpkgs#python3Packages.transformers "
                "nixpkgs#python3Packages.torch)"
            ),
        ) from exc

    try:
        import torch  # type: ignore

        device = 0 if torch.cuda.is_available() else -1
    except ImportError:
        device = -1  # CPU fallback

    logger.info("text_sentiment: loading sentiment model %s (device=%s)", SENTIMENT_MODEL, device)
    _sentiment_pipeline = pipeline(
        "sentiment-analysis",
        model=SENTIMENT_MODEL,
        top_k=None,
        device=device,
        truncation=True,
        max_length=512,
    )

    logger.info("text_sentiment: loading emotion model %s (device=%s)", EMOTION_MODEL, device)
    _emotion_pipeline = pipeline(
        "text-classification",
        model=EMOTION_MODEL,
        top_k=None,
        device=device,
        truncation=True,
        max_length=512,
    )
    return _sentiment_pipeline, _emotion_pipeline


def _infer_batch(
    texts: list[str],
    sentiment_pipeline: object,
    emotion_pipeline: object,
) -> list[SentimentScore]:
    """Run sentiment + emotion inference on a batch of texts.

    Isolated from ``score_texts`` so tests can monkeypatch this function
    with a deterministic stub without loading real models.
    """
    results: list[SentimentScore] = []

    # cardiffnlp model produces labels: "positive", "negative", "neutral"
    # with a softmax over the three. Map to [-1, +1]:
    # polarity = prob(positive) - prob(negative)
    sent_outputs = sentiment_pipeline(texts)  # type: ignore[operator]
    emo_outputs = emotion_pipeline(texts)    # type: ignore[operator]

    for text, s_out, e_out in zip(texts, sent_outputs, emo_outputs):
        # s_out: [{"label": "positive", "score": 0.8}, ...]
        sentiment_map: dict[str, float] = {item["label"].lower(): item["score"] for item in s_out}
        polarity = sentiment_map.get("positive", 0.0) - sentiment_map.get("negative", 0.0)
        polarity = max(-1.0, min(1.0, polarity))

        # e_out: [{"label": "joy", "score": 0.7}, ...]
        emo_map: dict[str, float] = {item["label"].lower(): item["score"] for item in e_out}
        # Fill any missing labels with 0 and normalise
        probs = {lbl: emo_map.get(lbl, 0.0) for lbl in EMOTION_LABELS}
        total = sum(probs.values()) or 1.0
        probs = {k: v / total for k, v in probs.items()}
        dominant = max(probs, key=probs.__getitem__)

        results.append(
            SentimentScore(
                sentiment=round(polarity, 4),
                dominant_emotion=dominant,
                emotion_probs=probs,
                word_count=len(text.split()),
            )
        )
    return results


def score_texts(
    texts: Sequence[str],
    *,
    batch_size: int = 32,
) -> list[SentimentScore]:
    """Score a list of texts for sentiment polarity and emotion distribution.

    Args:
        texts: Raw text strings. Empty strings are scored with sentiment=0,
            dominant_emotion="neutral", and all-neutral emotion probs.
        batch_size: Inference batch size. Larger values use more GPU memory.

    Returns:
        One ``SentimentScore`` per input text, in the same order.

    Raises:
        ``SourceUnavailableError``: when transformers/torch are not installed.
    """
    if not texts:
        return []

    sent_pipe, emo_pipe = _load_backends()

    all_scores: list[SentimentScore] = []
    text_list = list(texts)

    # Score non-empty texts in batches; handle empty texts inline.
    batch_indices: list[int] = []
    batch_texts: list[str] = []
    placeholder_scores: dict[int, SentimentScore] = {}

    _neutral_emotion: dict[str, float] = {lbl: 0.0 for lbl in EMOTION_LABELS}
    _neutral_emotion["neutral"] = 1.0

    for i, t in enumerate(text_list):
        stripped = t.strip()
        if not stripped:
            placeholder_scores[i] = SentimentScore(
                sentiment=0.0,
                dominant_emotion="neutral",
                emotion_probs=dict(_neutral_emotion),
                word_count=0,
            )
        else:
            batch_indices.append(i)
            batch_texts.append(stripped)

    # Run batched inference on non-empty texts
    batch_results: list[SentimentScore] = []
    for start in range(0, len(batch_texts), batch_size):
        chunk = batch_texts[start : start + batch_size]
        batch_results.extend(_infer_batch(chunk, sent_pipe, emo_pipe))

    # Reconstruct in original order
    result_iter = iter(batch_results)
    for i in range(len(text_list)):
        if i in placeholder_scores:
            all_scores.append(placeholder_scores[i])
        else:
            all_scores.append(next(result_iter))

    return all_scores


# ── Text corpus pluggability ──────────────────────────────────────────────────

# A corpus is a callable that yields (logical_date, text) tuples for a date range.
CorpusFn = Callable[[date, date], Iterator[tuple[date, str]]]


def _reddit_corpus(start: date, end: date) -> Iterator[tuple[date, str]]:
    """Own text from reddit comments (strips quoted blockquotes)."""
    from ..sources.reddit import iter_comments

    for comment in iter_comments():
        if comment.created is None:
            continue
        d = logical_date(comment.created)
        if d < start or d > end:
            continue
        own_text, _ = comment.split_quoted()
        own_text = own_text.strip()
        if own_text:
            yield d, own_text


def _wykop_corpus(start: date, end: date) -> Iterator[tuple[date, str]]:
    """Own text from wykop link-comments, entries, and entry-comments."""
    from ..sources.exports_wykop import (
        iter_wykop_entries,
        iter_wykop_entry_comments,
        iter_wykop_link_comments,
    )

    for item in iter_wykop_link_comments():
        if item.created_at is None:
            continue
        d = logical_date(item.created_at)
        if d < start or d > end:
            continue
        if item.content and item.content.strip():
            yield d, item.content.strip()

    for we in iter_wykop_entries():
        if we.created_at is None:
            continue
        d = logical_date(we.created_at)
        if d < start or d > end:
            continue
        if we.content and we.content.strip():
            yield d, we.content.strip()

    for wec in iter_wykop_entry_comments():
        if wec.created_at is None:
            continue
        d = logical_date(wec.created_at)
        if d < start or d > end:
            continue
        if wec.content and wec.content.strip():
            yield d, wec.content.strip()


def _messenger_corpus(start: date, end: date) -> Iterator[tuple[date, str]]:
    """Sent messages from Facebook Messenger (sender='Sinity' filter)."""
    from ..sources.exports_messenger import iter_fbmessenger_messages

    for msg in iter_fbmessenger_messages():
        if msg.timestamp is None:
            continue
        # Only operator's own outgoing messages; filter by sender heuristic.
        # The 'sender' field in MessengerMessage holds the display name.
        sender = getattr(msg, "sender", "") or ""
        if sender.lower() not in ("sinity", ""):
            continue
        d = logical_date(msg.timestamp)
        if d < start or d > end:
            continue
        text = (msg.text or "").strip()
        if text:
            yield d, text


def _sms_corpus(start: date, end: date) -> Iterator[tuple[date, str]]:
    """Sent SMS messages (msg_type == 'sent')."""
    from ..sources.sms import iter_messages

    for msg in iter_messages():
        if not msg.is_sent:
            continue
        d = logical_date(msg.date)
        if d < start or d > end:
            continue
        body = (msg.body or "").strip()
        if body:
            yield d, body


# Default corpus registry — ordered by coverage/richness.
# Each entry: (source_label, corpus_fn)
DEFAULT_CORPORA: list[tuple[str, CorpusFn]] = [
    ("reddit", _reddit_corpus),
    ("wykop", _wykop_corpus),
    ("messenger", _messenger_corpus),
    ("sms", _sms_corpus),
]


# ── Daily aggregation ─────────────────────────────────────────────────────────


def daily_mood(
    start: date,
    end: date,
    *,
    corpora: Optional[list[tuple[str, CorpusFn]]] = None,
    batch_size: int = 32,
    min_words: int = 3,
) -> list[MoodDay]:
    """Aggregate per-day mood signal from the operator's own text corpora.

    Args:
        start, end: Inclusive logical date range.
        corpora: Pluggable list of (label, corpus_fn) to draw text from.
            Defaults to ``DEFAULT_CORPORA`` (reddit + wykop + messenger + sms).
        batch_size: Inference batch size passed to ``score_texts``.
        min_words: Texts shorter than this word count are skipped (noise gate).

    Returns:
        One ``MoodDay`` per logical day that had at least one scored text.
        Days with no text are absent (missing != zero).

    Raises:
        ``SourceUnavailableError``: when transformers/torch are not installed.
    """
    if corpora is None:
        corpora = DEFAULT_CORPORA

    # Gather all (day, source, text) tuples across every corpus.
    gathered: list[tuple[date, str, str]] = []
    for label, fn in corpora:
        try:
            for d, text in fn(start, end):
                if len(text.split()) >= min_words:
                    gathered.append((d, label, text))
        except Exception:
            logger.warning("text_sentiment: corpus '%s' failed, skipping", label, exc_info=True)

    if not gathered:
        return []

    # Score all texts in one pass for efficiency.
    texts = [t for _, _, t in gathered]
    scores = score_texts(texts, batch_size=batch_size)

    # Aggregate by day.
    from collections import defaultdict

    day_scores: dict[date, list[SentimentScore]] = defaultdict(list)
    day_sources: dict[date, set[str]] = defaultdict(set)
    day_words: dict[date, int] = defaultdict(int)

    for (d, label, _), score in zip(gathered, scores):
        day_scores[d].append(score)
        day_sources[d].add(label)
        day_words[d] += score.word_count

    result: list[MoodDay] = []
    for d in sorted(day_scores):
        day_s = day_scores[d]
        n = len(day_s)
        mean_sent = sum(s.sentiment for s in day_s) / n

        # Mean emotion probability distribution.
        emo_means: dict[str, float] = {}
        for lbl in EMOTION_LABELS:
            emo_means[lbl] = sum(s.emotion_probs.get(lbl, 0.0) for s in day_s) / n

        dominant = max(emo_means, key=emo_means.__getitem__)

        result.append(
            MoodDay(
                date=d,
                mean_sentiment=round(mean_sent, 4),
                dominant_emotion=dominant,
                message_count=n,
                total_words=day_words[d],
                emotion_means={k: round(v, 4) for k, v in emo_means.items()},
                sources=frozenset(day_sources[d]),
            )
        )

    return result


__all__ = [
    "EMOTION_LABELS",
    "EMOTION_MODEL",
    "SENTIMENT_MODEL",
    "CorpusFn",
    "DEFAULT_CORPORA",
    "MoodDay",
    "SentimentScore",
    "daily_mood",
    "score_texts",
]
