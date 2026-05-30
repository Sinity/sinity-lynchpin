from datetime import date, timedelta


def _seed(db, rows, refresh_id="r1"):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows

    with connect(db) as conn:
        apply_schema(conn)
        promote_operator_day_rows(conn, refresh_id=refresh_id, rows=rows)


def test_operator_day_correlation_planted_signal(tmp_path, monkeypatch):
    import lynchpin.substrate.connection as duck_conn
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.mcp.tools.signals import (
        operator_day_correlation,
        operator_day_metrics,
    )

    db = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: db)

    base = date(2026, 1, 1)
    rows = [
        OperatorDay(
            date=base + timedelta(days=i),
            spotify_hours=float(i % 5),
            aw_deep_work_min=float((i % 5) * 30),  # tracks spotify_hours exactly
            git_commits=i % 3,
            sources_present=frozenset({"spotify", "activitywatch", "git"}),
        )
        for i in range(40)
    ]
    _seed(db, rows)

    assert "spotify_hours" in operator_day_metrics()

    out = operator_day_correlation("spotify_hours", "aw_deep_work_min", max_lag_days=2)
    assert out["days_materialized"] == 40
    lag0 = next(item for item in out["lags"] if item["lag_days"] == 0)
    assert lag0["r"] > 0.9  # strong planted same-day correlation
    assert lag0["significant"] is True
    assert lag0["n"] == 40


def test_operator_day_correlation_missing_not_zero(tmp_path, monkeypatch):
    """A metric that is NULL on every day yields no usable pairs — it is not
    silently correlated as a column of zeros."""
    import lynchpin.substrate.connection as duck_conn
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.mcp.tools.signals import operator_day_correlation

    db = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: db)

    base = date(2026, 1, 1)
    rows = [
        OperatorDay(
            date=base + timedelta(days=i),
            git_commits=i,  # stress_mean left as None on every row
            sources_present=frozenset({"git"}),
        )
        for i in range(20)
    ]
    _seed(db, rows)

    out = operator_day_correlation("stress_mean", "git_commits")
    assert out["lags"] == []  # stress_mean NULL everywhere → no pairs, not zeros


def test_operator_day_correlation_unknown_metric(tmp_path, monkeypatch):
    import lynchpin.substrate.connection as duck_conn
    from lynchpin.mcp.tools.signals import operator_day_correlation

    monkeypatch.setattr(duck_conn, "substrate_path", lambda: tmp_path / "x.duckdb")
    out = operator_day_correlation("bogus_metric", "git_commits")
    assert "error" in out
