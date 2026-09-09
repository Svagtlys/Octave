"""Registry: name/import-string resolution, registration rules, fake conformance."""

import pytest

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterLoadError,
    AdapterRegistrationError,
    UnknownAdapterError,
)
from octave.inference.registry import AdapterRegistry, register
from tests.inference.conformance import InferenceAdapterConformanceSuite
from tests.inference.fakes import FakeAdapter


class NotAnAdapter:
    """Module-level decoy for the non-subclass import-string test."""


def _registry_with_fake() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("fake", FakeAdapter)
    return registry


def test_register_resolve_and_names() -> None:
    registry = _registry_with_fake()
    assert registry.resolve("fake") is FakeAdapter
    assert registry.names() == ["fake"]


def test_duplicate_registration_rejected() -> None:
    registry = _registry_with_fake()
    with pytest.raises(AdapterRegistrationError):
        registry.register("fake", FakeAdapter)


def test_unknown_name_error_lists_known() -> None:
    registry = _registry_with_fake()
    with pytest.raises(UnknownAdapterError) as excinfo:
        registry.resolve("nope")
    assert "fake" in str(excinfo.value)


def test_import_string_resolves_plugin_class() -> None:
    registry = AdapterRegistry()
    assert registry.resolve("tests.inference.fakes:FakeAdapter") is FakeAdapter


def test_import_string_bad_module_raises_load_error() -> None:
    registry = AdapterRegistry()
    with pytest.raises(AdapterLoadError):
        registry.resolve("no.such.module:Cls")


def test_import_string_non_subclass_raises_load_error() -> None:
    registry = AdapterRegistry()
    with pytest.raises(AdapterLoadError):
        registry.resolve("tests.inference.test_registry:NotAnAdapter")


def test_create_instantiates_from_config() -> None:
    registry = _registry_with_fake()
    adapter = registry.create(
        AdapterConfig(adapter="fake", base_url="http://fake.test/v1")
    )
    assert isinstance(adapter, FakeAdapter)


def test_decorator_registers_into_given_registry() -> None:
    registry = AdapterRegistry()

    @register("decorated", registry=registry)
    class Decorated(FakeAdapter):
        """Adapter registered via decorator."""

    assert registry.resolve("decorated") is Decorated


class TestFakeAdapterConformance(InferenceAdapterConformanceSuite):
    """A plugin-shaped adapter satisfies the contract — the bar for third parties."""

    @pytest.fixture
    def adapter(self) -> InferenceAdapter:
        return FakeAdapter(
            AdapterConfig(
                adapter="fake",
                base_url="http://fake.test/v1",
                default_model="fake-model",
            )
        )
