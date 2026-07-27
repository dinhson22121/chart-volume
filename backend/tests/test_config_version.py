from dataclasses import dataclass

from app.services import config_version


@dataclass(frozen=True)
class _FakeConfig:
    a: float = 1.0
    b: int = 2


def test_same_strategy_and_values_yield_same_version():
    v1 = config_version.compute("wyckoff", _FakeConfig(a=1.0, b=2))
    v2 = config_version.compute("wyckoff", _FakeConfig(a=1.0, b=2))
    assert v1 == v2


def test_changing_a_field_changes_the_version():
    v1 = config_version.compute("wyckoff", _FakeConfig(a=1.0, b=2))
    v2 = config_version.compute("wyckoff", _FakeConfig(a=1.5, b=2))
    assert v1 != v2


def test_same_values_different_strategy_yield_different_version():
    v1 = config_version.compute("wyckoff", _FakeConfig())
    v2 = config_version.compute("smc", _FakeConfig())
    assert v1 != v2


def test_version_is_prefixed_with_strategy_name():
    v = config_version.compute("wyckoff", _FakeConfig())
    assert v.startswith("wyckoff:")
