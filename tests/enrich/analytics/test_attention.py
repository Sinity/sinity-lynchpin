from lynchpin.enrich.metrics.attention import attention_transition_matrix, sustainability_curve

def test_attention_transition_empty():
    result = attention_transition_matrix([])
    assert result.transition_matrix == {}
    assert result.entropy_rate == 0

def test_sustainability_empty():
    result = sustainability_curve([])
    assert result.median_sustain_min == 0
