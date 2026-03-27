from __future__ import annotations

from datetime import date

import lynchpin.retrospective.enrichment as enrichment_module
import lynchpin.retrospective.narrative as narrative_module
from lynchpin.context.bundles import EvidenceBundle, EvidenceQuery
from lynchpin.context.trust import SurfaceFreshness, TrustLevel
from lynchpin.periods import parse_period


def test_build_day_enrichment_accepts_string_scale(monkeypatch) -> None:
    period = parse_period("day", "2026-03-16")
    assert period is not None

    bundle = EvidenceBundle(
        period=period,
        generated_at="2026-03-16T00:00:00+00:00",
        freshness=[
            SurfaceFreshness(
                surface="processed_delivery_telemetry",
                date_column="date",
                max_value="2026-03-16",
                row_count=1,
                days_stale=0,
                level=TrustLevel.fresh,
            ),
        ],
        queries=[
            EvidenceQuery(
                query_id="delivery_telemetry",
                title="Delivery Telemetry",
                sql="SELECT 1",
                params=[date(2026, 3, 16), date(2026, 3, 16)],
                rows=[
                    {
                        "date": "2026-03-16",
                        "active_hours": 1.5,
                        "total_commits": 2,
                        "command_count": 3,
                        "chat_sessions": 1,
                        "chat_engaged_minutes": 4.0,
                        "repos_json": '["/realm/project/sinity-lynchpin"]',
                        "ai_models_json": '["gpt-5"]',
                    },
                ],
            ),
        ],
        notes=[],
        bundle_ref=None,
    )

    monkeypatch.setattr(enrichment_module, "build_period_evidence_bundle", lambda *args, **kwargs: bundle)
    monkeypatch.setattr(enrichment_module, "format_activity_spans", lambda start, end: "")
    monkeypatch.setattr(enrichment_module, "format_shell_commands", lambda start, end: "")
    monkeypatch.setattr(enrichment_module, "format_git_commits", lambda start, end: "")
    monkeypatch.setattr(enrichment_module, "format_sleep_data", lambda start, end: "")
    monkeypatch.setattr(narrative_module, "load_narratives", lambda kind, keys: {})

    text = enrichment_module.build_day_enrichment("base prompt", "day", "2026-03-16", materialize_bundle=False)

    assert "## Evidence bundle" in text
    assert "## Evidence summary" in text
    assert "processed_delivery_telemetry" in text
