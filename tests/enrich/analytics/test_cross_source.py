from lynchpin.enrich.metrics.cross_source import sleep_productivity_link

def test_sleep_link_empty():
    result = sleep_productivity_link([], [])
    assert result.name == "sleep->deep_work"
    assert result.correlation == 0.0
