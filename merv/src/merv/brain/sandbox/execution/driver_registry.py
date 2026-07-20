"""Lazy registration and runtime inventory for sandbox provider drivers.

Descriptors contain only metadata and an import string. Importing this module
does not import provider implementations, resolve credentials, or require an
optional provider SDK. A provider is loaded only when composition selects it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, cast

from ..sandbox_backend import (
    BackendUnavailableError,
    BackendValidationError,
    SandboxBackend,
    SandboxDriver,
    SandboxManagementTransport,
)


ActivityHook = Callable[[str, dict[str, Any]], None]
DEFAULT_SANDBOX_DRIVER = "lambda_labs"
DRIVER_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z")


class SandboxDriverFactory(Protocol):
    """Uniform lazy-construction seam used by every registered provider.

    Factories return the flattened ``SandboxBackend`` compatibility facade
    while existing services still consume it. The smaller ``SandboxDriver``
    and its management transport remain the implementation boundary underneath.
    """

    def __call__(
        self,
        *,
        repo_root: Path,
        activity: ActivityHook | None = None,
    ) -> SandboxBackend: ...


class SandboxDriverKind(str, Enum):
    """Provider procurement model; deliberately not a VM-only taxonomy."""

    VM = "vm"
    MANAGED_CONTAINER = "managed_container"
    IN_MEMORY_TEST = "in_memory_test"


class ManagementTransportKind(str, Enum):
    """How the brain performs operational reads/writes after provisioning."""

    MANAGEMENT_SSH = "management_ssh"
    PROVIDER_EXEC = "provider_exec"
    IN_MEMORY = "in_memory"


def _normalized_name(value: str) -> str:
    return value.strip().lower()


@dataclass(frozen=True, slots=True)
class SandboxDriverDescriptor:
    """Registration record for one lazily loaded provider implementation."""

    name: str
    factory_ref: str
    kind: SandboxDriverKind
    management_transport_kind: ManagementTransportKind
    aliases: tuple[str, ...] = ()
    test_only: bool = False

    def __post_init__(self) -> None:
        canonical = _normalized_name(self.name)
        if canonical != self.name or not DRIVER_NAME_PATTERN.fullmatch(canonical):
            raise BackendValidationError(
                "sandbox driver name must match [a-z][a-z0-9_]*: "
                f"{self.name!r}"
            )
        module_name, separator, attribute = self.factory_ref.partition(":")
        if not separator or not module_name or not attribute or ":" in attribute:
            raise BackendValidationError(
                "sandbox driver factory_ref must be 'module.path:callable'"
            )
        normalized_aliases = tuple(_normalized_name(alias) for alias in self.aliases)
        if any(not alias for alias in normalized_aliases):
            raise BackendValidationError(
                f"sandbox driver {self.name} has an empty alias"
            )
        if any(
            not DRIVER_NAME_PATTERN.fullmatch(alias) for alias in normalized_aliases
        ):
            raise BackendValidationError(
                f"sandbox driver {self.name} has an invalid alias"
            )
        if self.name in normalized_aliases or len(set(normalized_aliases)) != len(
            normalized_aliases
        ):
            raise BackendValidationError(
                f"sandbox driver {self.name} has duplicate aliases"
            )
        if normalized_aliases != self.aliases:
            raise BackendValidationError(
                f"sandbox driver {self.name} aliases must be canonical lowercase"
            )

    def load_factory(self) -> SandboxDriverFactory:
        """Import the selected driver factory, leaving all others untouched."""
        module_name, _, attribute = self.factory_ref.partition(":")
        try:
            module = import_module(module_name)
            factory = getattr(module, attribute)
        except (AttributeError, ImportError) as exc:
            raise BackendUnavailableError(
                f"could not load sandbox driver {self.name}: {exc}"
            ) from exc
        if not callable(factory):
            raise BackendUnavailableError(
                f"sandbox driver factory is not callable: {self.factory_ref}"
            )
        return cast(SandboxDriverFactory, factory)


class SandboxDriverRegistry:
    """Explicit provider registration, alias resolution, and lazy construction."""

    def __init__(self) -> None:
        self._descriptors: dict[str, SandboxDriverDescriptor] = {}
        self._aliases: dict[str, str] = {}

    def register(self, descriptor: SandboxDriverDescriptor) -> None:
        names = (descriptor.name, *descriptor.aliases)
        conflicts = [
            name
            for name in names
            if name in self._descriptors or name in self._aliases
        ]
        if conflicts:
            raise BackendValidationError(
                "sandbox driver name or alias is already registered: "
                + ", ".join(conflicts)
            )
        self._descriptors[descriptor.name] = descriptor
        self._aliases.update(
            {alias: descriptor.name for alias in descriptor.aliases}
        )

    def canonical_name(self, name: str) -> str:
        normalized = _normalized_name(name)
        return self._aliases.get(normalized, normalized)

    def descriptor(self, name: str) -> SandboxDriverDescriptor:
        canonical = self.canonical_name(name)
        try:
            return self._descriptors[canonical]
        except KeyError as exc:
            raise BackendUnavailableError(
                f"unknown execution backend: {canonical}"
            ) from exc

    def build(
        self,
        *,
        name: str,
        repo_root: Path,
        activity: ActivityHook | None = None,
    ) -> SandboxBackend:
        descriptor = self.descriptor(name)
        backend = descriptor.load_factory()(
            repo_root=repo_root,
            activity=activity,
        )
        if not isinstance(backend, SandboxBackend) or not isinstance(
            backend, SandboxDriver
        ) or not isinstance(
            backend.management_transport, SandboxManagementTransport
        ):
            raise BackendValidationError(
                f"sandbox driver {descriptor.name} does not implement the "
                "SandboxBackend compatibility contract"
            )
        if backend.capabilities.name != descriptor.name:
            raise BackendValidationError(
                f"sandbox driver {descriptor.name} built backend named "
                f"{backend.capabilities.name}"
            )
        return backend

    def descriptors(self) -> tuple[SandboxDriverDescriptor, ...]:
        """Stable runtime inventory, in registration order."""
        return tuple(self._descriptors.values())

    @property
    def aliases(self) -> Mapping[str, str]:
        # A live read-only view: aliases registered after module import must be
        # visible to a later multiplexer build as well as to canonicalization.
        return MappingProxyType(self._aliases)


SANDBOX_DRIVER_REGISTRY = SandboxDriverRegistry()


def register_sandbox_driver(descriptor: SandboxDriverDescriptor) -> None:
    """Register a provider with the process-wide composition inventory."""
    SANDBOX_DRIVER_REGISTRY.register(descriptor)


def sandbox_driver_inventory() -> tuple[SandboxDriverDescriptor, ...]:
    """Return every registered driver without importing its implementation."""
    return SANDBOX_DRIVER_REGISTRY.descriptors()


def _vm_driver_descriptor(
    *, name: str, factory_ref: str, aliases: tuple[str, ...] = ()
) -> SandboxDriverDescriptor:
    return SandboxDriverDescriptor(
        name=name,
        factory_ref=factory_ref,
        aliases=aliases,
        kind=SandboxDriverKind.VM,
        management_transport_kind=ManagementTransportKind.MANAGEMENT_SSH,
    )


def _register_builtin_drivers() -> None:
    descriptors = (
        _vm_driver_descriptor(
            name="lambda_labs",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.lambda_labs:"
                "build_lambda_labs_sandbox_backend"
            ),
            aliases=("lambda", "lambdalabs"),
        ),
        _vm_driver_descriptor(
            name="thunder_compute",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.thunder_compute:"
                "build_thunder_compute_sandbox_backend"
            ),
            aliases=("thunder", "thundercompute"),
        ),
        SandboxDriverDescriptor(
            name="modal",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.modal:"
                "build_modal_sandbox_backend"
            ),
            kind=SandboxDriverKind.MANAGED_CONTAINER,
            management_transport_kind=ManagementTransportKind.PROVIDER_EXEC,
        ),
        _vm_driver_descriptor(
            name="hyperstack",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.hyperstack:"
                "build_hyperstack_sandbox_backend"
            ),
        ),
        _vm_driver_descriptor(
            name="digitalocean",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.digitalocean:"
                "build_digitalocean_sandbox_backend"
            ),
        ),
        _vm_driver_descriptor(
            name="verda",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.verda:"
                "build_verda_sandbox_backend"
            ),
            aliases=("datacrunch",),
        ),
        _vm_driver_descriptor(
            name="voltage_park",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.voltage_park:"
                "build_voltage_park_sandbox_backend"
            ),
            aliases=("voltagepark",),
        ),
        _vm_driver_descriptor(
            name="tensordock",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.tensordock:"
                "build_tensordock_sandbox_backend"
            ),
        ),
        SandboxDriverDescriptor(
            name="fake",
            factory_ref=(
                "merv.brain.sandbox.execution.backends.fake:"
                "build_fake_sandbox_backend"
            ),
            kind=SandboxDriverKind.IN_MEMORY_TEST,
            management_transport_kind=ManagementTransportKind.IN_MEMORY,
            test_only=True,
        ),
    )
    for descriptor in descriptors:
        register_sandbox_driver(descriptor)


_register_builtin_drivers()


__all__ = [
    "ActivityHook",
    "DEFAULT_SANDBOX_DRIVER",
    "ManagementTransportKind",
    "SANDBOX_DRIVER_REGISTRY",
    "SandboxDriverDescriptor",
    "SandboxDriverFactory",
    "SandboxDriverKind",
    "SandboxDriverRegistry",
    "register_sandbox_driver",
    "sandbox_driver_inventory",
]
