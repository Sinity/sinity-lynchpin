from datetime import date


def _stub_score(text):
    from lynchpin.analysis.text_sentiment import EMOTION_LABELS, SentimentScore

    others = (len(EMOTION_LABELS) - 1) or 1
    probs = {lbl: (0.7 if lbl == "joy" else 0.3 / others) for lbl in EMOTION_LABELS}
    return SentimentScore(
        sentiment=0.5, dominant_emotion="joy", emotion_probs=probs, word_count=len(text.split())
    )


def test_score_texts_uses_mocked_backend(monkeypatch):
    import lynchpin.analysis.text_sentiment as ts

    monkeypatch.setattr(ts, "_load_backends", lambda: (object(), object()))
    monkeypatch.setattr(ts, "_infer_batch", lambda texts, s, e: [_stub_score(t) for t in texts])

    out = ts.score_texts(["hello world", "another one here"])
    assert len(out) == 2
    assert out[0].sentiment == 0.5 and out[0].dominant_emotion == "joy"
    assert ts.score_texts([]) == []


def test_daily_mood_aggregates_and_missing_not_zero(monkeypatch):
    import lynchpin.analysis.text_sentiment as ts

    monkeypatch.setattr(ts, "score_texts", lambda texts, batch_size=32: [_stub_score(t) for t in texts])

    def corpus(start, end):
        yield date(2026, 5, 1), "good day today"
        yield date(2026, 5, 1), "still feeling great here"
        yield date(2026, 5, 3), "another entry written"

    days = ts.daily_mood(date(2026, 5, 1), date(2026, 5, 3), corpora=[("test", corpus)], min_words=1)
    by = {d.date: d for d in days}

    assert set(by) == {date(2026, 5, 1), date(2026, 5, 3)}  # 05-02 had no text → absent (missing != zero)
    assert by[date(2026, 5, 1)].message_count == 2
    assert by[date(2026, 5, 1)].mean_sentiment == 0.5
    assert "test" in by[date(2026, 5, 1)].sources


def test_score_texts_raises_when_backend_unavailable(monkeypatch):
    import lynchpin.analysis.text_sentiment as ts
    from lynchpin.core.errors import SourceUnavailableError

    def boom():
        raise SourceUnavailableError(source="text_sentiment", reason="transformers absent")

    monkeypatch.setattr(ts, "_load_backends", boom)
    import pytest

    with pytest.raises(SourceUnavailableError):
        ts.score_texts(["needs a model"])
