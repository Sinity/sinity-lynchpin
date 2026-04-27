"""Content diet profiling — what you consume, from where, about what.

Uses v2 literal_parse and semantic fields to build a complete picture
of content consumption: domains, articles, videos, social platforms,
technologies, repos, commands, NSFW categories.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)

def _db(): return duckdb.connect(DB_PATH, read_only=True)


@dataclass(frozen=True)
class ContentDiet:
    start: date
    end: date
    total_hours: float
    productive_hours: float

    # By activity
    activity_hours: list[tuple[str, float]]

    # Domains
    top_domains: list[tuple[str, float]]     # (domain, hours)
    top_articles: list[tuple[str, str, str]]  # (title, topic, author)

    # Video
    top_videos: list[tuple[str, str]]        # (title, topic)
    top_channels: list[tuple[str, int]]      # (channel, count)

    # Social
    social_platforms: dict[str, float]       # platform → hours

    # Technology / code
    top_technologies: list[tuple[str, int]]
    top_repos: list[tuple[str, int]]
    top_file_paths: list[tuple[str, int]]

    # NSFW
    nsfw_hours: float
    nsfw_categories: list[tuple[str, float]]

    # Summary for chat
    brief: str


def content_diet(start: date, end: date) -> ContentDiet:
    """Full content diet for a date range."""
    db = _db()

    # Totals
    totals = db.execute("""
        SELECT sum("time"."duration_s")/3600,
               sum(CASE WHEN semantic.is_productive = true THEN "time"."duration_s" ELSE 0 END)/3600
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
    """, [start.isoformat(), end.isoformat()]).fetchone()
    total_h = totals[0] or 0
    prod_h = totals[1] or 0

    # Activity hours
    act_rows = db.execute("""
        SELECT semantic.activity, sum("time"."duration_s")/3600 as h
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY 1 ORDER BY h DESC LIMIT 12
    """, [start.isoformat(), end.isoformat()]).fetchall()
    activity_hours = [(r[0], r[1]) for r in act_rows]

    # Top domains (from literal_parse.domains array — take first element)
    domains_raw = db.execute("""
        SELECT literal_parse.domains[1] as domain,
               sum("time"."duration_s")/3600 as h
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.domains IS NOT NULL
          AND len(literal_parse.domains) > 0
        GROUP BY domain ORDER BY h DESC LIMIT 15
    """, [start.isoformat(), end.isoformat()]).fetchall()
    top_domains = [(r[0], r[1]) for r in domains_raw if r[0]]

    # Top articles
    articles = db.execute("""
        SELECT literal_parse.article.title, literal_parse.article.topic_hint,
               semantic.context_sentence
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.article.title IS NOT NULL
        LIMIT 30
    """, [start.isoformat(), end.isoformat()]).fetchall()
    top_articles = [(r[0] or "", r[1] or "", r[2] or "") for r in articles[:15]]

    # Top videos
    videos = db.execute("""
        SELECT literal_parse.video.title, literal_parse.video.topic_hint
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.video.title IS NOT NULL
        LIMIT 30
    """, [start.isoformat(), end.isoformat()]).fetchall()
    top_videos = [(r[0] or "", r[1] or "") for r in videos[:15]]

    # Technologies
    techs = db.execute("""
        SELECT literal_parse.technologies
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.technologies IS NOT NULL
    """, [start.isoformat(), end.isoformat()]).fetchall()
    tech_counter = Counter()
    for (t_list,) in techs:
        if t_list:
            for t in t_list:
                tech_counter[t] += 1
    top_technologies = tech_counter.most_common(15)

    # Repos
    repos = db.execute("""
        SELECT literal_parse.repo_refs
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.repo_refs IS NOT NULL
    """, [start.isoformat(), end.isoformat()]).fetchall()
    repo_counter = Counter()
    for (r_list,) in repos:
        if r_list:
            for r in r_list:
                repo_counter[r] += 1
    top_repos = repo_counter.most_common(10)

    # File paths
    fps = db.execute("""
        SELECT literal_parse.file_paths
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.file_paths IS NOT NULL
    """, [start.isoformat(), end.isoformat()]).fetchall()
    fp_counter = Counter()
    for (fp_list,) in fps:
        if fp_list:
            for fp in fp_list:
                fp_counter[fp] += 1
    top_file_paths = [(fp, n) for fp, n in fp_counter.most_common(15)]

    # Social platforms
    social = db.execute("""
        SELECT literal_parse.social.platform, count(*) n
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.social.platform IS NOT NULL
        GROUP BY 1 ORDER BY n DESC
    """, [start.isoformat(), end.isoformat()]).fetchall()
    social_platforms = {r[0]: r[1] for r in social if r[0]}

    # NSFW
    nsfw = db.execute("""
        SELECT sum("time"."duration_s")/3600
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND semantic.topic_category = 'nsfw'
    """, [start.isoformat(), end.isoformat()]).fetchone()
    nsfw_h = nsfw[0] or 0

    nsfw_cats = db.execute("""
        SELECT literal_parse.adult.category, sum("time"."duration_s")/3600 as h
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
          AND literal_parse.adult.category IS NOT NULL
        GROUP BY 1 ORDER BY h DESC
    """, [start.isoformat(), end.isoformat()]).fetchall()
    nsfw_categories = [(r[0], r[1]) for r in nsfw_cats if r[0]]

    db.close()

    # Build brief
    parts = [f"Content diet {start} → {end}: {total_h:.1f}h ({prod_h:.1f}h productive)."]
    if top_domains:
        parts.append(f"Top domains: {', '.join(d for d,_ in top_domains[:5])}.")
    if top_technologies:
        parts.append(f"Top tech: {', '.join(t for t,_ in top_technologies[:5])}.")
    if nsfw_h > 0:
        parts.append(f"NSFW: {nsfw_h:.1f}h.")
    if top_articles:
        parts.append(f"{len(top_articles)} articles read.")
    if top_videos:
        parts.append(f"{len(top_videos)} videos watched.")

    return ContentDiet(
        start=start, end=end, total_hours=total_h, productive_hours=prod_h,
        activity_hours=activity_hours, top_domains=top_domains,
        top_articles=top_articles, top_videos=top_videos,
        top_channels=[], social_platforms=social_platforms,
        top_technologies=top_technologies, top_repos=top_repos,
        top_file_paths=top_file_paths,
        nsfw_hours=nsfw_h, nsfw_categories=nsfw_categories,
        brief=". ".join(parts),
    )


def week_diet(d: date | None = None) -> ContentDiet:
    """Content diet for the week containing d."""
    if d is None: d = date.today()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    return content_diet(mon, sun)
