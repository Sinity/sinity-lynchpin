from lynchpin.enrichment.features import ALL_FEATURES, get_features


def test_all_features_have_metadata():
    for f in ALL_FEATURES:
        assert f.name, f"empty name"
        assert f.description, f"empty description for {f.name}"
        assert f.category in ("attention", "activity", "temporal", "context", "content", "ai")
        assert f.granularity in ("hourly", "daily", "weekly")


def test_get_features_filter():
    attention = get_features("attention")
    assert all(f.category == "attention" for f in attention)
    assert len(attention) >= 5


def test_get_features_all():
    assert len(get_features()) >= 20


def test_feature_functions_exist():
    # Verify all compute functions are callable
    for f in ALL_FEATURES:
        result = f.compute([], None)
        assert result is None or isinstance(result, (float, int, list))


from datetime import date
from lynchpin.enrichment.features import FeatureStore, FeatureMatrix


def test_feature_store_empty():
    store = FeatureStore(ALL_FEATURES[:3])
    matrix = store.compute_all([], date.today(), date.today())
    assert len(matrix.dates) == 0
    assert len(matrix.feature_names) == 3


def test_feature_matrix_to_dicts():
    m = FeatureMatrix(
        dates=[date(2026, 1, 1)],
        feature_names=["a", "b"],
        values=[[1.0, None]],
        missing=[1],
    )
    dicts = m.to_dicts()
    assert dicts == [{"a": 1.0, "b": None}]


def test_train_test_split():
    m = FeatureMatrix(
        dates=[date(2026, 1, i) for i in range(1, 11)],
        feature_names=["x"],
        values=[[float(i)] for i in range(10)],
        missing=[],
    )
    store = FeatureStore(ALL_FEATURES[:1])
    train, test = store.train_test_split_by_time(m, test_days=3)
    assert len(train.dates) == 7
    assert len(test.dates) == 3
    assert test.dates[0] == date(2026, 1, 8)
