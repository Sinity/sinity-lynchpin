from __future__ import annotations

from lynchpin.core.calls import realize_iterable, safe_source_call


def test_realize_iterable_materializes_one_shot_iterator() -> None:
    values = (item for item in [1, 2, 3])

    assert realize_iterable(values) == [1, 2, 3]


def test_safe_source_call_preserves_explicit_none_default() -> None:
    def fail() -> list[int]:
        raise ValueError("boom")

    assert safe_source_call(fail, default=None) is None


def test_safe_source_call_reports_error_and_uses_empty_list_by_default() -> None:
    errors: list[str] = []

    def fail() -> list[int]:
        raise ValueError("boom")

    assert safe_source_call(fail, on_error=lambda exc: errors.append(str(exc))) == []
    assert errors == ["boom"]
