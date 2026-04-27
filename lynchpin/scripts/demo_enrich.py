"""Demonstrate semantic classification on real AW data.

Shows: raw titles → rules-based classification → LLM classification (for ambiguous ones)
       → queryable structured data.

Usage:
    python -m lynchpin.scripts.demo_enrich [--llm] [--limit N]
"""

import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..enrich.classify import (
    classify_semantic, classify_batch, _classify_rules,
    init_db, cache_stats, hash_title,
)


def load_unique_titles(aw_db_path: str, days: int = 3) -> list[tuple[str, str, int]]:
    """Load unique (app, title) pairs with occurrence counts from AW."""
    conn = sqlite3.connect(aw_db_path)
    since_ns = int((time.time() - days * 86400) * 1e9)
    rows = conn.execute("""
        SELECT json_extract(data, '$.app') as a,
               json_extract(data, '$.title') as t,
               count(*) as n
        FROM events
        WHERE json_extract(data, '$.title') IS NOT NULL
          AND starttime > ?
        GROUP BY a, t
        ORDER BY n DESC
    """, (since_ns,)).fetchall()
    conn.close()
    return [(a or "?", t or "", n) for a, t, n in rows]


def demo_rules_only(titles: list[tuple[str, str, int]], limit: int = 40):
    """Show rules-based classification on the most frequent titles."""
    print("=== Rules-based classification (most frequent titles) ===\n")
    print(f"{'count':>5}  {'app':20s} │ {'activity':20s} │ {'subject':20s} │ attn │ conf │ LLM? │ title")
    print("─" * 120)

    for app, title, count in titles[:limit]:
        span = _classify_rules(app, title)
        title_short = title[:55] + "…" if len(title) > 55 else title
        print(f"{count:5d}  {app:20s} │ {span.activity:20s} │ {str(span.subject or ''):20s} │ "
              f"{span.attention_level:5s} │ {span.confidence:.2f} │ "
              f"{'Y' if span.needs_llm else ' '}    │ {title_short}")


def demo_compression(titles: list[tuple[str, str, int]], limit: int = 40):
    """Show the compression: raw titles → structured fields."""
    print("\n=== Compression demo ===\n")

    raw_chars = 0
    struct_chars = 0
    needs_llm = 0
    activities: Counter = Counter()
    subjects: Counter = Counter()

    for app, title, count in titles[:limit]:
        span = _classify_rules(app, title)
        raw_chars += len(title) * count
        struct_chars += (len(span.activity) + len(str(span.subject or ''))
                         + len(span.attention_level) + len(span.content_type)) * count
        activities[span.activity] += count
        if span.subject:
            subjects[span.subject] += count
        if span.needs_llm:
            needs_llm += 1

    ratio = raw_chars / max(struct_chars, 1)
    print(f"Raw title chars (weighted by frequency): {raw_chars:,}")
    print(f"Structured field chars:                 {struct_chars:,}")
    print(f"Compression ratio:                      {ratio:.1f}x")
    print(f"Titles needing LLM:                     {needs_llm}/{limit} ({needs_llm*100/limit:.0f}%)")
    print()
    print("Top activities:")
    for act, n in activities.most_common(10):
        pct = n * 100 / sum(activities.values())
        print(f"  {act:25s} {n:6d} ({pct:5.1f}%)")
    print()
    print("Top subjects:")
    for subj, n in subjects.most_common(10):
        pct = n * 100 / sum(subjects.values())
        print(f"  {subj:25s} {n:6d} ({pct:5.1f}%)")


def demo_llm(samples: list[tuple[str, str]]):
    """Run LLM classification on a few ambiguous samples."""
    print("\n=== LLM classification (ambiguous titles) ===\n")

    for app, title in samples:
        span = _classify_rules(app, title)
        if not span.needs_llm:
            continue
        print(f"App: {app}")
        print(f"Title: {title}")
        print(f"  Rules: activity={span.activity} subject={span.subject} "
              f"attention={span.attention_level}")
        print(f"  Calling LLM...")
        try:
            llm_span = classify_semantic(app, title, force_llm=True)
            print(f"  LLM:   activity={llm_span.activity} subject={llm_span.subject} "
                  f"attention={llm_span.attention_level}")
            print(f"         content_type={llm_span.content_type} "
                  f"is_productive={llm_span.is_productive}")
            print(f"         rationale={llm_span.rationale}")
        except Exception as e:
            print(f"  LLM ERROR: {e}")
        print()


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--llm", action="store_true", help="Run LLM classification on ambiguous titles")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--days", type=int, default=3)
    args = p.parse_args()

    aw_db = "/home/sinity/.local/share/activitywatch/aw-server-rust/sqlite.db"

    print(f"Loading unique titles from last {args.days} days...")
    titles = load_unique_titles(aw_db, days=args.days)
    print(f"  {len(titles)} unique (app, title) pairs\n")

    demo_rules_only(titles, limit=args.limit)
    demo_compression(titles, limit=args.limit)

    if args.llm:
        init_db()
        # Pick the ambiguous ones
        ambiguous = []
        for app, title, _ in titles:
            span = _classify_rules(app, title)
            if span.needs_llm:
                ambiguous.append((app, title))
        print(f"\n{len(ambiguous)} titles need LLM classification.")
        if ambiguous:
            demo_llm(ambiguous[:10])

    print(f"\nCache stats: {cache_stats()}")


if __name__ == "__main__":
    main()
