"""Payload construction for the project velocity dashboard."""

from __future__ import annotations

from collections.abc import Mapping
import datetime as dt
from typing import Any, Dict

from ...core.projects import ProjectProfile
from .velocity_analysis import AGGREGATE_PROJECT, ProjectStats


def build_velocity_dashboard_payload(
    all_stats: Dict[str, ProjectStats],
    project_specs: Mapping[str, ProjectProfile],
    *,
    generated_at: str | None = None,
) -> dict[str, Any] | None:
    # Collect all dates
    all_dates = set()
    for p in all_stats.values():
        all_dates.update(p.daily.keys())
    sorted_dates = sorted(list(all_dates))

    if not sorted_dates:
        return None

    # Build datasets per project per category
    js_projects = {}

    for name, stats in all_stats.items():
        spec = project_specs[name]
        categories = list(spec.categories)
        colors = spec.colors

        project_data = {
            "categories": {},
            "categoryList": categories,
            "colors": colors,
            "events": {},
            "activity": {"commits": [], "churn": [], "net": []},
            "files": [],
            "modules": [],
            "owners": [],
            "authors": [],
            "cochange": {"nodes": [], "edges": []},
            "tags": [],
        }

        # Initialize cumulative counters per category
        cumulative = {cat: 0 for cat in categories}

        for cat in categories:
            project_data["categories"][cat] = {"growth": [], "churn": [], "net": []}

        for d in sorted_dates:
            day_stats = stats.daily.get(d)
            day_churn = 0
            day_net = 0

            for cat in categories:
                if day_stats and cat in day_stats.by_category:
                    cat_data = day_stats.by_category[cat]
                    cumulative[cat] += cat_data.net
                    if cumulative[cat] < 0:
                        cumulative[cat] = 0
                    cat_churn = cat_data.added + cat_data.removed
                    project_data["categories"][cat]["churn"].append(cat_churn)
                    project_data["categories"][cat]["net"].append(cat_data.net)
                    day_churn += cat_churn
                    day_net += cat_data.net
                else:
                    project_data["categories"][cat]["churn"].append(0)
                    project_data["categories"][cat]["net"].append(0)

                project_data["categories"][cat]["growth"].append(cumulative[cat])

            project_data["activity"]["commits"].append(
                len(day_stats.commits) if day_stats and day_stats.commits else 0
            )
            project_data["activity"]["churn"].append(day_churn)
            project_data["activity"]["net"].append(day_net)

            # Events for inspector
            if day_stats and day_stats.commits:
                ev_list = []
                for c in day_stats.commits:
                    cat_breakdown = {
                        k: {"a": v.added, "r": v.removed}
                        for k, v in c.by_category.items()
                    }
                    ev_list.append(
                        {
                            "h": c.hash,
                            "a": c.author,
                            "m": c.message,
                            "+": c.added,
                            "-": c.removed,
                            "t": c.timestamp,
                            "p": c.parents,
                            "cats": cat_breakdown,
                            "f": c.top_files,
                            "fc": c.files_count,
                        }
                    )
                ev_list.sort(key=lambda x: x["+"] + x["-"], reverse=True)
                project_data["events"][d] = ev_list

        file_rows = []
        for filename, fstats in stats.file_stats.items():
            churn = fstats.added + fstats.removed
            net = fstats.added - fstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            file_rows.append(
                {
                    "name": filename,
                    "churn": churn,
                    "net": net,
                    "loc": loc,
                    "volatility": round(volatility, 3),
                }
            )
        file_rows.sort(key=lambda row: row["churn"], reverse=True)
        project_data["files"] = file_rows[:200]

        module_rows = []
        for module, mstats in stats.module_stats.items():
            churn = mstats.added + mstats.removed
            net = mstats.added - mstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            module_rows.append(
                {
                    "name": module,
                    "churn": churn,
                    "net": net,
                    "loc": loc,
                    "volatility": round(volatility, 3),
                }
            )
        module_rows.sort(key=lambda row: row["churn"], reverse=True)
        project_data["modules"] = module_rows[:200]

        owners = []
        for module, authors in stats.module_authors.items():
            total = sum(authors.values())
            if total == 0:
                continue
            top_author = max(authors.items(), key=lambda item: item[1])
            churn = 0
            if module in stats.module_stats:
                mstats = stats.module_stats[module]
                churn = mstats.added + mstats.removed
            owners.append(
                {
                    "module": module,
                    "author": top_author[0],
                    "share": round(top_author[1] / total, 3),
                    "commits": total,
                    "churn": churn,
                }
            )
        owners.sort(key=lambda row: (row["share"], row["churn"]), reverse=True)
        project_data["owners"] = owners[:200]

        author_rows = []
        author_totals: Dict[str, dict] = {}
        for daily in stats.daily.values():
            for event in daily.commits:
                row = author_totals.setdefault(
                    event.author,
                    {
                        "name": event.author,
                        "commits": 0,
                        "churn": 0,
                        "net": 0,
                        "mergeCommits": 0,
                        "lastSeen": event.timestamp or daily.date,
                    },
                )
                row["commits"] += 1
                row["churn"] += event.added + event.removed
                row["net"] += event.added - event.removed
                row["mergeCommits"] += 1 if event.parents > 1 else 0
                if event.timestamp and event.timestamp > row["lastSeen"]:
                    row["lastSeen"] = event.timestamp
        for row in author_totals.values():
            author_rows.append(row)
        author_rows.sort(key=lambda row: (row["churn"], row["commits"]), reverse=True)
        project_data["authors"] = author_rows[:200]

        top_modules = [row["name"] for row in module_rows[:20]]
        module_weights = {row["name"]: row["churn"] for row in module_rows}
        nodes = []
        for module_name in top_modules:
            weight = module_weights.get(module_name, 0)
            nodes.append(
                {
                    "name": module_name,
                    "value": weight,
                    "symbolSize": max(8, min(40, 8 + (weight**0.5))),
                }
            )
        edges = []
        for (left, right), weight in stats.cochange.items():
            if left in top_modules and right in top_modules:
                edges.append(
                    {
                        "source": left,
                        "target": right,
                        "value": weight,
                        "lineStyle": {"width": max(1, min(6, weight / 2))},
                    }
                )
        edges.sort(key=lambda row: row["value"], reverse=True)
        project_data["cochange"] = {"nodes": nodes, "edges": edges[:60]}
        project_data["tags"] = stats.tags

        js_projects[name] = project_data

    # Compute summary stats for each project
    project_summaries = {}
    for name, stats in all_stats.items():
        spec = project_specs[name]
        categories = list(spec.categories)
        total_loc = (
            sum(
                js_projects[name]["categories"][cat]["growth"][-1] for cat in categories
            )
            if sorted_dates
            else 0
        )
        total_commits = sum(len(day.commits) for day in stats.daily.values())
        active_days = len([d for d in stats.daily.values() if d.commits])

        # Recent 30-day velocity
        recent_net = 0
        recent_churn = 0
        for cat in categories:
            growth = js_projects[name]["categories"][cat]["growth"]
            churn = js_projects[name]["categories"][cat]["churn"]
            if len(growth) >= 30:
                recent_net += growth[-1] - growth[-30]
                recent_churn += sum(churn[-30:])
            elif len(growth) > 0:
                recent_net += growth[-1]
                recent_churn += sum(churn)

        project_summaries[name] = {
            "totalLoc": total_loc,
            "totalCommits": total_commits,
            "activeDays": active_days,
            "recentNet": recent_net,
            "recentChurn": recent_churn,
            "firstDate": min(stats.daily.keys()) if stats.daily else None,
            "lastDate": max(stats.daily.keys()) if stats.daily else None,
            "authorCount": len(
                {event.author for day in stats.daily.values() for event in day.commits}
            ),
            "tagCount": len(stats.tags),
        }

    dashboard_payload = {
        "dates": sorted_dates,
        "projectData": js_projects,
        "projectSummaries": project_summaries,
        "aggregateProject": AGGREGATE_PROJECT,
        "generatedAt": generated_at
        or dt.datetime.now().replace(microsecond=0).isoformat(),
    }

    return dashboard_payload
