from app.strategies import registry


def test_wyckoff_is_registered_and_default():
    assert registry.DEFAULT_STRATEGY == "wyckoff"
    assert registry.is_known("wyckoff")


def test_get_strategy_returns_wyckoff_module():
    from app import wyckoff

    assert registry.get_strategy("wyckoff") is wyckoff


def test_get_strategy_falls_back_to_default_for_unknown_key():
    from app import wyckoff

    assert registry.get_strategy("does-not-exist") is wyckoff


def test_list_strategies_includes_wyckoff_with_label():
    strategies = registry.list_strategies()
    keys = [s["key"] for s in strategies]
    assert "wyckoff" in keys
    wyckoff_entry = next(s for s in strategies if s["key"] == "wyckoff")
    assert "label" in wyckoff_entry and wyckoff_entry["label"]


def test_sonicr_is_registered():
    assert registry.is_known("sonicr")


def test_get_strategy_returns_sonicr_module():
    from app import sonicr

    assert registry.get_strategy("sonicr") is sonicr


def test_list_strategies_includes_sonicr_with_label():
    strategies = registry.list_strategies()
    sonicr_entry = next(s for s in strategies if s["key"] == "sonicr")
    assert "label" in sonicr_entry and sonicr_entry["label"]
