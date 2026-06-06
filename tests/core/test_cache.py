from __future__ import annotations

import logging

from lynchpin.core import cache


def test_persistent_cache_uses_quiet_default_logger(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_cachew(**kwargs):
        captured.update(kwargs)

        def decorate(func):
            return func

        return decorate

    monkeypatch.setattr(cache, "_cachew", fake_cachew)

    @cache.persistent_cache("fixture")
    def cached() -> int:
        return 1

    assert cached() == 1
    logger = captured["logger"]
    assert isinstance(logger, logging.Logger)
    assert logger.name == "lynchpin.cachew.fixture"
    assert logger.level == logging.WARNING
    assert logger.propagate is False


def test_persistent_cache_preserves_explicit_logger(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_cachew(**kwargs):
        captured.update(kwargs)

        def decorate(func):
            return func

        return decorate

    explicit = logging.getLogger("fixture.explicit")
    monkeypatch.setattr(cache, "_cachew", fake_cachew)

    @cache.persistent_cache("fixture", logger=explicit)
    def cached() -> int:
        return 1

    assert cached() == 1
    assert captured["logger"] is explicit
