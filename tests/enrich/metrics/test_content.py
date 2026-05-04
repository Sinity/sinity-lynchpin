from lynchpin.enrich.metrics.content import content_profile, topic_drift

def test_content_profile_empty():
    result = content_profile([])
    assert result.total_hours == 0
    assert result.by_activity == {}

def test_topic_drift_empty():
    result = topic_drift([])
    assert result.dates == []
    assert result.change_points == []
