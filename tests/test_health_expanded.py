"""Tests for expanded health module — all new loaders and aggregates."""

from datetime import date

from lynchpin.sources.health import (
    heart_rate_measurements,
    spo2_measurements,
    weight_measurements,
    skin_temperature,
    floors_climbed,
    mood_entries,
    snoring_records,
    respiratory_rate,
    daily_stress,
    daily_heart_rate,
    daily_health_summary,
    hrv_measurements,
    HeartRateMeasurement,
    SpO2Measurement,
    WeightMeasurement,
    SkinTemperature,
    FloorClimbed,
    MoodEntry,
    SnoringRecord,
    RespiratoryMeasurement,
    DailyStressSummary,
    DailyHeartRateSummary,
    DailyHealthSummary,
    HRVMeasurement,
)


def test_heart_rate_returns_list():
    result = heart_rate_measurements()
    assert isinstance(result, list)
    assert len(result) > 0  # 23K+ records
    assert isinstance(result[0], HeartRateMeasurement)
    assert result[0].heart_rate > 0


def test_heart_rate_date_filter():
    result = heart_rate_measurements(start=date(2022, 9, 1), end=date(2022, 9, 30))
    assert isinstance(result, list)
    assert len(result) > 0
    for m in result:
        assert date(2022, 9, 1) <= m.timestamp.date() <= date(2022, 9, 30)


def test_spo2_returns_list():
    result = spo2_measurements()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], SpO2Measurement)
    assert 50 <= result[0].spo2 <= 100


def test_weight_returns_list():
    result = weight_measurements()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], WeightMeasurement)
    assert result[0].weight_kg > 0


def test_skin_temperature_returns_list():
    result = skin_temperature()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], SkinTemperature)
    assert 20 < result[0].temperature < 45


def test_floors_climbed_returns_list():
    result = floors_climbed()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], FloorClimbed)
    assert result[0].floors > 0


def test_mood_entries_returns_list():
    result = mood_entries()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], MoodEntry)


def test_snoring_records_returns_list():
    result = snoring_records()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], SnoringRecord)


def test_respiratory_rate_returns_list():
    result = respiratory_rate()
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], RespiratoryMeasurement)
    assert result[0].avg_rate > 0


def test_hrv_has_fields():
    result = hrv_measurements()
    assert isinstance(result, list)
    assert len(result) > 0
    m = result[0]
    assert isinstance(m, HRVMeasurement)
    assert m.sdnn_avg is not None
    assert m.rmssd_avg is not None


def test_daily_stress_aggregates():
    result = daily_stress(start=date(2022, 9, 1), end=date(2022, 9, 30))
    assert isinstance(result, list)
    if result:
        s = result[0]
        assert isinstance(s, DailyStressSummary)
        assert s.avg_score > 0
        assert s.measurement_count > 0
        assert s.min_score <= s.avg_score <= s.max_score


def test_daily_heart_rate_aggregates():
    result = daily_heart_rate(start=date(2022, 9, 1), end=date(2022, 9, 30))
    assert isinstance(result, list)
    if result:
        h = result[0]
        assert isinstance(h, DailyHeartRateSummary)
        assert h.avg_hr > 0
        assert h.min_hr <= h.avg_hr <= h.max_hr
        assert h.resting_hr <= h.avg_hr


def test_daily_health_summary():
    result = daily_health_summary(start=date(2025, 6, 1), end=date(2025, 6, 30))
    assert isinstance(result, list)
    if result:
        s = result[0]
        assert isinstance(s, DailyHealthSummary)
        assert hasattr(s, "steps")
        assert hasattr(s, "stress_avg")
        assert hasattr(s, "heart_rate_avg")


def test_daily_health_summary_has_diverse_fields():
    """Verify that across a wide range, at least some fields are populated."""
    result = daily_health_summary(start=date(2022, 9, 1), end=date(2023, 12, 31))
    assert len(result) > 0
    has_steps = any(s.steps is not None for s in result)
    has_hr = any(s.heart_rate_avg is not None for s in result)
    has_stress = any(s.stress_avg is not None for s in result)
    assert has_steps or has_hr or has_stress, "Expected at least some populated fields"
