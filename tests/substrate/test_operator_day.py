from datetime import date, timedelta


def test_promote_operator_day_round_trip(tmp_path):
    """OperatorDay rows materialize into the substrate; NULL (missing) is
    preserved distinct from a real zero, and sources_present round-trips."""
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows

    db = tmp_path / "sub.duckdb"
    rows = [
        OperatorDay(
            date=date(2026, 5, 1),
            aw_deep_work_min=120.0,
            git_commits=5,
            spotify_hours=2.5,
            hrv_rmssd=42.0,
            web_visits=80,
            web_social_visits=30,
            sources_present=frozenset({"activitywatch", "git", "spotify"}),
        ),
        OperatorDay(
            date=date(2026, 5, 2),
            aw_deep_work_min=0.0,
            git_commits=0,
            spotify_hours=None,  # absent — must stay NULL, not 0
            sources_present=frozenset({"git"}),
        ),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_operator_day_rows(conn, refresh_id="r1", rows=rows) == 2
        got = conn.execute(
            "SELECT date, aw_deep_work_min, git_commits, spotify_hours, hrv_rmssd, "
            "web_social_visits, sources_present FROM operator_day "
            "WHERE refresh_id='r1' ORDER BY date"
        ).fetchall()

    assert got[0][0] == date(2026, 5, 1)
    assert got[0][1] == 120.0
    assert got[0][2] == 5
    assert got[0][3] == 2.5
    assert got[0][4] == 42.0
    assert got[0][5] == 30
    assert sorted(got[0][6]) == ["activitywatch", "git", "spotify"]
    # missing != zero: an absent spotify day stays NULL, not coerced to 0.0
    assert got[1][3] is None
    assert sorted(got[1][6]) == ["git"]


def test_promote_operator_day_idempotent(tmp_path):
    """Re-promoting the same refresh_id replaces rather than duplicates."""
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows

    db = tmp_path / "sub.duckdb"
    row = [OperatorDay(date=date(2026, 5, 1), git_commits=3)]
    with connect(db) as conn:
        apply_schema(conn)
        promote_operator_day_rows(conn, refresh_id="r1", rows=row)
        promote_operator_day_rows(conn, refresh_id="r1", rows=row)
        row = conn.execute(
            "SELECT COUNT(*) FROM operator_day WHERE refresh_id='r1'"
        ).fetchone()
        count = row[0] if row else 0
    assert count == 1


def test_promote_operator_day_new_columns_round_trip(tmp_path):
    """New expansion columns round-trip: 0/None defaults hold, set values persist."""
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows

    db = tmp_path / "sub.duckdb"
    rows = [
        OperatorDay(
            date=date(2026, 6, 1),
            substance_mg_by_name={"substance_a": 40.0, "substance_b": 500.0, "substance_c": 2.0},
            substance_unique_count=3,
            stress_min=20.0,
            stress_max=85.0,
            web_unique_domains=42,
            polylogue_messages=150,
            weather_temp_mean=18.5,
            mood_dominant_emotion="joy",
            keylog_sessions=5,
            spo2_pct=97.5,
            skin_temp_c=36.1,
            sources_present=frozenset({"substance", "health", "polylogue"}),
        ),
        OperatorDay(
            date=date(2026, 6, 2),
            # All new optional fields absent — must stay NULL, not 0
            sources_present=frozenset({"git"}),
        ),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_operator_day_rows(conn, refresh_id="r1", rows=rows) == 2
        got = conn.execute(
            "SELECT date, substance_mg_by_name, substance_unique_count, stress_min, stress_max, "
            "web_unique_domains, polylogue_messages, weather_temp_mean, mood_dominant_emotion, "
            "keylog_sessions, spo2_pct, skin_temp_c "
            "FROM operator_day WHERE refresh_id='r1' ORDER BY date"
        ).fetchall()

    r1 = got[0]
    assert r1[0] == date(2026, 6, 1)
    import json as _json
    assert _json.loads(r1[1]) == {"substance_a": 40.0, "substance_b": 500.0, "substance_c": 2.0}  # substance_mg_by_name
    assert r1[2] == 3          # substance_unique_count
    assert r1[3] == 20.0       # stress_min
    assert r1[4] == 85.0       # stress_max
    assert r1[5] == 42         # web_unique_domains
    assert r1[6] == 150        # polylogue_messages
    assert abs(r1[7] - 18.5) < 0.001  # weather_temp_mean
    assert r1[8] == "joy"      # mood_dominant_emotion
    assert r1[9] == 5          # keylog_sessions
    assert abs(r1[10] - 97.5) < 0.001  # spo2_pct
    assert abs(r1[11] - 36.1) < 0.001  # skin_temp_c

    r2 = got[1]
    # Optional new columns must be NULL, not 0, when absent
    assert r2[3] is None       # stress_min
    assert r2[4] is None       # stress_max
    assert r2[7] is None       # weather_temp_mean
    assert r2[8] is None       # mood_dominant_emotion
    assert r2[10] is None      # spo2_pct
    assert r2[11] is None      # skin_temp_c
    # Non-optional (DEFAULT 0) columns stay 0
    assert r2[2] == 0          # substance_unique_count
    assert r2[5] == 0          # web_unique_domains


def test_load_operator_day_rows_filters_and_narrows(tmp_path):
    """load_operator_day_rows: date filtering and column narrowing work correctly."""
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import load_operator_day_rows, promote_operator_day_rows

    db = tmp_path / "sub.duckdb"
    base = date(2026, 6, 1)
    all_rows = [
        OperatorDay(
            date=base + timedelta(days=i),
            git_commits=i + 1,
            stress_min=float(i * 5) if i > 0 else None,
            sources_present=frozenset({"git"}),
        )
        for i in range(5)
    ]
    with connect(db) as conn:
        apply_schema(conn)
        promote_operator_day_rows(conn, refresh_id="r1", rows=all_rows)

        # No filters: returns all 5 rows
        got_all = load_operator_day_rows(conn, refresh_id="r1")
        assert len(got_all) == 5
        assert got_all[0]["date"] == base
        assert got_all[0]["git_commits"] == 1

        # Date range filter
        got_range = load_operator_day_rows(
            conn, refresh_id="r1",
            start=date(2026, 6, 2), end=date(2026, 6, 3)
        )
        assert len(got_range) == 2
        assert got_range[0]["date"] == date(2026, 6, 2)
        assert got_range[1]["date"] == date(2026, 6, 3)

        # Column narrowing
        got_narrow = load_operator_day_rows(
            conn, refresh_id="r1",
            columns=["date", "git_commits", "stress_min"]
        )
        assert len(got_narrow) == 5
        assert set(got_narrow[0].keys()) == {"date", "git_commits", "stress_min"}
        assert got_narrow[0]["stress_min"] is None  # first row has None

        # Invalid column raises ValueError
        import pytest
        with pytest.raises(ValueError, match="unknown operator_day columns"):
            load_operator_day_rows(conn, refresh_id="r1", columns=["not_a_column"])


def test_promote_operator_day_source_status_enables_best_refresh_id(tmp_path):
    """After promoting rows + recording source status, best_materialized_refresh_id finds the refresh_id."""
    from datetime import date
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.analysis.active.substrate_promote_status import record_source_status
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows
    from lynchpin.substrate.snapshots import best_materialized_refresh_id

    db = tmp_path / "sub.duckdb"
    rows = [OperatorDay(date=date(2026, 5, 1), git_commits=3, sources_present=frozenset({"git"}))]
    with connect(db) as conn:
        apply_schema(conn)
        n = promote_operator_day_rows(conn, refresh_id="op-test", rows=rows)
        assert n == 1
        record_source_status(
            conn,
            refresh_id="op-test",
            source="operator_day",
            status="ok",
            reason=None,
            row_count=n,
            window_start=date(2026, 5, 1),
            window_end=date(2026, 5, 1),
        )
        found = best_materialized_refresh_id(conn, "operator_day", caller="test")

    assert found == "op-test"
