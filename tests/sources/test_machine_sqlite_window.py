import sqlite3

import pytest

from lynchpin.sources.machine_sqlite import window_select


@pytest.mark.parametrize('index_kind', ['normal', 'missing', 'partial', 'wrong'])
@pytest.mark.parametrize('nullable', [False, True])
def test_window_select_preserves_rows_and_uses_bounded_index(index_kind, nullable):
    conn = sqlite3.connect(':memory:')
    nullability = '' if nullable else 'NOT NULL'
    conn.execute(f'CREATE TABLE sample(id INTEGER PRIMARY KEY, label TEXT {nullability}, observed_at TEXT)')
    if index_kind != 'missing':
        columns = 'observed_at,label' if index_kind == 'wrong' else 'label,observed_at'
        predicate = ' WHERE label IS NOT NULL' if index_kind == 'partial' else ''
        conn.execute(f'CREATE INDEX sample_label_time ON sample({columns}){predicate}')
    conn.executemany('INSERT INTO sample VALUES(?,?,?)', (
        (i, str(i % 10), str(i // 10).zfill(4)) for i in range(10000)
    ))
    if nullable:
        conn.execute("INSERT INTO sample VALUES(10000,NULL,'0999')")
    # Equal timestamps are separate native records.
    conn.execute("INSERT INTO sample VALUES(10001,'9','0999')")
    where = ['observed_at>=?', 'observed_at<?']
    sql = window_select(conn, table='sample', columns=['id', 'label', 'observed_at'],
                        where=where, order='observed_at,label,id', prefix='label',
                        index='sample_label_time')
    expected = conn.execute('SELECT * FROM sample WHERE observed_at>=? AND observed_at<? '
                            'ORDER BY observed_at,label,id', ('0990', '1000')).fetchall()
    assert conn.execute(sql, ('0990', '1000')).fetchall() == expected
    optimized = index_kind == 'normal' and not nullable
    assert ('WITH RECURSIVE' in sql) == optimized
    if optimized:
        plan = [row[3] for row in conn.execute('EXPLAIN QUERY PLAN ' + sql, ('0990', '1000'))]
        assert any('label=? AND observed_at>? AND observed_at<?' in line for line in plan)
        assert not any(line.startswith('SCAN sample') for line in plan)
    full = window_select(conn, table='sample', columns=['id', 'label', 'observed_at'],
                         where=[], order='observed_at,label,id', prefix='label',
                         index='sample_label_time')
    assert 'WITH RECURSIVE' not in full
    assert conn.execute(full).fetchall() == conn.execute(
        'SELECT * FROM sample ORDER BY observed_at,label,id').fetchall()


def test_window_select_empty_indexed_source():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE sample(label TEXT NOT NULL, observed_at TEXT); '
                      'CREATE INDEX sample_label_time ON sample(label,observed_at);')
    sql = window_select(conn, table='sample', columns=['label', 'observed_at'],
                        where=['observed_at>=?'], order='observed_at,label',
                        prefix='label', index='sample_label_time')
    assert conn.execute(sql, ('0990',)).fetchall() == []


def test_readonly_connection_preserves_uri_punctuation_in_filename(tmp_path):
    import sqlite3
    from lynchpin.sources.machine_sqlite import connect_readonly
    actual = tmp_path / "capture?detail#part.sqlite"
    decoy = tmp_path / "capture"
    for path, value in [(actual, 2), (decoy, 1)]:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE evidence (value INTEGER)")
            conn.execute("INSERT INTO evidence VALUES (?)", [value])
    with connect_readonly(actual) as conn:
        assert conn.execute("SELECT value FROM evidence").fetchone() == (2,)
        try:
            conn.execute("DELETE FROM evidence")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc)
        else:
            raise AssertionError("readonly source permitted mutation")


def test_latest_timestamp_seeks_each_nonnullable_index_prefix(tmp_path):
    import sqlite3
    from lynchpin.sources.machine_sqlite import latest_observed_at
    with sqlite3.connect(tmp_path / "fixture.sqlite") as conn:
        conn.execute("CREATE TABLE sample (host TEXT NOT NULL, observed_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX host_time ON sample(host, observed_at)")
        conn.executemany("INSERT INTO sample VALUES (?,?)", [("a","2026-01-01T00:00:00Z"),("b","2026-01-03T00:00:00Z"),("a","2026-01-02T00:00:00Z")])
        statements = []
        conn.set_trace_callback(statements.append)
        assert latest_observed_at(conn,"sample") == "2026-01-03T00:00:00Z"
        sql = next(statement for statement in statements if statement.startswith("WITH RECURSIVE"))
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        assert any("SEARCH sample" in row and "host=?" in row for row in plan)
        assert not any("SCAN sample" in row for row in plan)


def test_latest_timestamp_keeps_nullable_prefix_records(tmp_path):
    import sqlite3
    from lynchpin.sources.machine_sqlite import latest_observed_at
    with sqlite3.connect(tmp_path / "fixture.sqlite") as conn:
        conn.execute("CREATE TABLE sample (host TEXT, observed_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX host_time ON sample(host, observed_at)")
        conn.executemany("INSERT INTO sample VALUES (?,?)", [("a","2026-01-01T00:00:00Z"),(None,"2026-01-03T00:00:00Z")])
        assert latest_observed_at(conn,"sample") == "2026-01-03T00:00:00Z"
