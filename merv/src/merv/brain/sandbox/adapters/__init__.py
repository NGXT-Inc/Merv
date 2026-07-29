# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Lazy provider selection and multi-provider routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Mapping, cast
from ...kernel.env import env_value
from ..models import (
    BackendCapabilities,
    BackendUnavailableError,
    BackendValidationError,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxBackend,
    SandboxBackendBase,
    SandboxRequest,
    SandboxTarget,
    TranscriptTail,
)


# Lazy adapter registry

ActivityHook = Callable[[str, dict[str, Any]], None]
SandboxDriverFactory = Callable[..., SandboxBackend]
DEFAULT_SANDBOX_DRIVER = "lambda_labs"


def _normalized_name(value: str) -> str:
    return value.strip().lower()


@dataclass(frozen=True, slots=True)
class SandboxDriverDescriptor:
    """One lazily imported provider implementation."""

    name: str
    factory_ref: str
    aliases: tuple[str, ...] = ()

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


def _driver(
    name: str, aliases: tuple[str, ...] = ()
) -> SandboxDriverDescriptor:
    return SandboxDriverDescriptor(
        name=name,
        factory_ref=f"{__name__}.{name}:build_{name}_sandbox_backend",
        aliases=aliases,
    )


SANDBOX_DRIVER_DESCRIPTORS = (
    _driver("lambda_labs", ("lambda", "lambdalabs")),
    _driver("thunder_compute", ("thunder", "thundercompute")),
    _driver("modal"),
    _driver("hyperstack"),
    _driver("digitalocean"),
    _driver("verda", ("datacrunch",)),
    _driver("voltage_park", ("voltagepark",)),
    _driver("tensordock"),
)
_DESCRIPTORS_BY_NAME = {
    descriptor.name: descriptor for descriptor in SANDBOX_DRIVER_DESCRIPTORS
}
SANDBOX_DRIVER_ALIASES = {
    alias: descriptor.name
    for descriptor in SANDBOX_DRIVER_DESCRIPTORS
    for alias in descriptor.aliases
}


def canonical_sandbox_driver_name(name: str) -> str:
    normalized = _normalized_name(name)
    return SANDBOX_DRIVER_ALIASES.get(normalized, normalized)


def sandbox_driver_descriptor(name: str) -> SandboxDriverDescriptor:
    canonical = canonical_sandbox_driver_name(name)
    try:
        return _DESCRIPTORS_BY_NAME[canonical]
    except KeyError as exc:
        raise BackendUnavailableError(
            f"unknown execution backend: {canonical}"
        ) from exc


def build_sandbox_driver(
    *,
    name: str,
    repo_root: Path,
    activity: ActivityHook | None = None,
) -> SandboxBackend:
    descriptor = sandbox_driver_descriptor(name)
    backend = descriptor.load_factory()(repo_root=repo_root, activity=activity)
    if not isinstance(backend, SandboxBackend):
        raise BackendValidationError(
            f"sandbox driver {descriptor.name} does not implement SandboxBackend"
        )
    if backend.capabilities.name != descriptor.name:
        raise BackendValidationError(
            f"sandbox driver {descriptor.name} built backend named "
            f"{backend.capabilities.name}"
        )
    return backend


def sandbox_driver_inventory() -> tuple[SandboxDriverDescriptor, ...]:
    """Return every driver without importing its implementation."""
    return SANDBOX_DRIVER_DESCRIPTORS


# Multi-provider router

class MultiplexingSandboxBackend(SandboxBackendBase):
    """Route one provider-neutral port across several provider backends."""

    def __init__(
        self,
        *,
        backends: dict[str, SandboxBackend],
        default: str,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        if not backends:
            raise BackendValidationError("multiplexer requires at least one backend")
        if default not in backends:
            raise BackendValidationError(f"default backend is not configured: {default}")
        self.backends = dict(backends)
        self.default = default
        self._aliases = dict(aliases or {})
        # Preserve default-provider metadata; billing protection is the union.
        base = self.backends[default].capabilities
        self.capabilities = BackendCapabilities(
            name=base.name,
            enforce_expiry=any(
                backend.capabilities.enforce_expiry for backend in self.backends.values()
            ),
            lifetime_extension_supported=base.lifetime_extension_supported,
            requires_hardware_selection=base.requires_hardware_selection,
            configurable_resources=base.configurable_resources,
        )

    # ---------- routing ----------

    def _resolve_provider(self, provider: str | None) -> str:
        requested = (provider or "").strip().lower()
        name = self._aliases.get(requested, requested)
        if not name:
            return self.default
        if name not in self.backends:
            configured = ", ".join(sorted(self.backends))
            raise BackendValidationError(
                f"unknown sandbox provider: {provider}. Configured providers: {configured}."
            )
        return name

    def _decode(self, sandbox_id: str) -> tuple[SandboxBackend, str]:
        """Decode an owned ID; unknown prefixes are unavailable, not absent."""
        prefix, sep, native = sandbox_id.partition(":")
        if not sep:
            return self.backends[self.default], sandbox_id
        backend = self.backends.get(prefix)
        if backend is None:
            raise BackendUnavailableError(
                f"sandbox id {sandbox_id!r} belongs to provider {prefix!r}, "
                "which is not configured in MERV_EXECUTION_BACKENDS"
            )
        return backend, native

    def _encode(self, provider: str, native_id: str) -> str:
        return f"{provider}:{native_id}" if native_id else native_id

    def qualified_sandbox_id(self, *, sandbox_id: str, provider: str = "") -> str:
        """Qualify legacy native IDs using the durable row owner."""
        sandbox_id = str(sandbox_id or "")
        prefix, sep, _native = sandbox_id.partition(":")
        if sep:
            if prefix not in self.backends:
                raise BackendUnavailableError(
                    f"sandbox id {sandbox_id!r} belongs to provider {prefix!r}, "
                    "which is not configured in MERV_EXECUTION_BACKENDS"
                )
            return sandbox_id  # already carries its owner
        recorded = str(provider or "").strip().lower()
        name = self._aliases.get(recorded, recorded)
        if not sandbox_id or not name:
            return sandbox_id
        if name not in self.backends:
            raise BackendUnavailableError(
                f"sandbox id {sandbox_id!r} belongs to provider {name!r}, "
                "which is not configured in MERV_EXECUTION_BACKENDS"
            )
        return self._encode(name, sandbox_id)

    # ---------- capabilities ----------

    def capabilities_for(self, *, provider: str | None = None) -> BackendCapabilities:
        return self.backends[self._resolve_provider(provider)].capabilities

    # ---------- provisioning ----------

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        name = self._resolve_provider(request.provider)
        backend = self.backends[name]

        def prefixed_on_created(sandbox_id: str, sandbox_name: str) -> None:
            # Persist ownership together with the early native ID.
            if on_created is not None:
                on_created(self._encode(name, sandbox_id), sandbox_name)

        provisioned = backend.acquire(
            request=request,
            on_phase=on_phase,
            on_created=prefixed_on_created if on_created is not None else None,
        )
        return replace(
            provisioned, sandbox_id=self._encode(name, provisioned.sandbox_id)
        )

    # ---------- id-addressed operations ----------

    def is_alive(self, *, sandbox_id: str) -> bool:
        backend, native = self._decode(sandbox_id)
        return backend.is_alive(sandbox_id=native)

    def terminate(self, *, sandbox_id: str) -> bool:
        backend, native = self._decode(sandbox_id)
        return backend.terminate(sandbox_id=native)

    def read_transcript(
        self,
        *,
        target: SandboxTarget,
        tail: int | None = None,
    ) -> TranscriptTail:
        backend, native = self._decode(target.sandbox_id)
        return backend.read_transcript(
            target=replace(target, sandbox_id=native),
            tail=tail,
        )

    def sample_metrics(
        self,
        *,
        target: SandboxTarget,
    ) -> dict[str, Any] | None:
        backend, native = self._decode(target.sandbox_id)
        return backend.sample_metrics(target=replace(target, sandbox_id=native))

    def read_runs(
        self,
        *,
        target: SandboxTarget,
    ) -> list[dict[str, Any]] | None:
        backend, native = self._decode(target.sandbox_id)
        return backend.read_runs(target=replace(target, sandbox_id=native))

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        backend, native = self._decode(sandbox_id)
        return backend.refresh_ssh_endpoint(sandbox_id=native)

    def write_secrets(
        self,
        *,
        target: SandboxTarget,
        secrets: Mapping[str, str],
    ) -> bool:
        backend, native = self._decode(target.sandbox_id)
        return backend.write_secrets(
            target=replace(target, sandbox_id=native),
            secrets=secrets,
        )

    # ---------- fleet-wide operations ----------

    def _lookup_targets(self, *, provider: str) -> dict[str, SandboxBackend]:
        recorded = str(provider or "").strip().lower()
        name = self._aliases.get(recorded, recorded)
        if not name:
            return self.backends
        if name not in self.backends:
            raise BackendUnavailableError(
                f"sandbox belongs to provider {name!r}, which is not configured "
                "in MERV_EXECUTION_BACKENDS"
            )
        return {name: self.backends[name]}

    def find_sandbox_id(
        self, *, experiment_id: str, sandbox_uid: str = "", provider: str = ""
    ) -> str | None:
        """Ask only the recorded owner; ownerless legacy rows may fan out.

        Deterministic names can collide across providers. Absence is returned
        only if every eligible provider answered.
        """
        unreachable: Exception | None = None
        for name, backend in self._lookup_targets(provider=provider).items():
            try:
                found = backend.find_sandbox_id(
                    experiment_id=experiment_id, sandbox_uid=sandbox_uid
                )
            except Exception as exc:  # noqa: BLE001 — try the others before giving up
                unreachable = exc
                continue
            if found:
                return self._encode(name, str(found))
        if unreachable is not None:
            raise unreachable
        return None

    def hardware_catalog(
        self, *, gpu: str | None = None, region: str | None = None
    ) -> dict[str, Any] | None:
        """Merge catalogs and tag each requestable option with its provider."""
        merged: list[dict[str, Any]] = []
        regions: set[str] = set()
        catalogs: dict[str, dict[str, Any]] = {}
        for name, backend in self.backends.items():
            try:
                catalog = backend.hardware_catalog(gpu=gpu, region=region)
            except Exception:  # noqa: BLE001 — one provider outage must not empty the menu
                continue
            if not catalog:
                continue
            catalogs[name] = catalog
            regions.update(str(r) for r in catalog.get("regions", []) or [])
            for option in catalog.get("options", []) or []:
                merged.append({**option, "provider": name})
        if not catalogs:
            return None
        merged.sort(
            key=lambda o: (
                # Unknown price sorts last.
                o.get("price_usd_per_hour") is None,
                float(o.get("price_usd_per_hour") or 0.0),
                str(o.get("instance_type") or ""),
            )
        )
        base = catalogs.get(self.default, {})
        return {
            "provider": self.capabilities.name,
            "providers": sorted(catalogs),
            "selection_required": any(
                bool(c.get("selection_required")) for c in catalogs.values()
            ),
            "select_with": base.get("select_with") or "instance_type",
            "reason": (
                "Several compute providers are configured; each options[] entry "
                "carries the provider that serves it. Pass that provider (and "
                "its instance_type) back on sandbox.request."
            ),
            "regions": sorted(regions),
            "count": len(merged),
            "options": merged,
        }

    def sandbox_environment(self) -> dict:
        tokens: list[str] = []
        notes: list[str] = []
        for backend in self.backends.values():
            try:
                env = backend.sandbox_environment()
            except Exception:  # noqa: BLE001
                continue
            tokens.extend(t for t in env.get("available_tokens", []) if t not in tokens)
            notes.extend(n for n in env.get("notes", []) if n not in notes)
        return {"available_tokens": tokens, "notes": notes}

    def sandbox_secrets(self, *, hf_token: str = "") -> dict[str, str]:
        merged: dict[str, str] = {}
        for backend in self.backends.values():
            try:
                merged.update(backend.sandbox_secrets(hf_token=hf_token))
            except Exception:  # noqa: BLE001
                continue
        return merged

    def health(self) -> dict:
        reports = {}
        for name, backend in self.backends.items():
            try:
                reports[name] = backend.health()
            except Exception as exc:  # noqa: BLE001
                reports[name] = {"ok": False, "error": str(exc)}
        failing = sorted(
            name for name, report in reports.items() if not report.get("ok")
        )
        result: dict[str, Any] = {
            "ok": not failing,
            "backend": self.capabilities.name,
            "backends": reports,
        }
        if failing:
            result["error"] = "; ".join(
                f"{name}: {reports[name].get('error') or 'unhealthy'}" for name in failing
            )
        return result

    def shutdown(self) -> None:
        for backend in self.backends.values():
            try:
                backend.shutdown()
            except Exception:  # noqa: BLE001
                continue


# Composition factory

BACKEND_ALIASES = SANDBOX_DRIVER_ALIASES


def _canonical_backend_name(name: str) -> str:
    return canonical_sandbox_driver_name(name)


def _build_named_backend(
    *,
    name: str,
    repo_root: Path,
    activity: ActivityHook | None = None,
) -> SandboxBackend:
    return build_sandbox_driver(
        name=name,
        repo_root=repo_root,
        activity=activity,
    )


def build_sandbox_backend(
    *,
    repo_root: Path,
    name: str | None = None,
    activity: ActivityHook | None = None,
) -> SandboxBackend:
    """Select and construct the configured sandbox backend(s).

    Backend name comes from (in order): `name=` arg,
    `MERV_EXECUTION_BACKEND` env (legacy `RESEARCH_PLUGIN_EXECUTION_BACKEND`),
    or "lambda_labs" by default. `MERV_EXECUTION_BACKENDS` (comma-separated,
    legacy `RESEARCH_PLUGIN_EXECUTION_BACKENDS`) configures several providers
    at once behind one MultiplexingSandboxBackend; the single-name env then
    selects the default among them. One configured backend keeps today's
    direct, prefix-free path.
    """
    if name is not None:
        return _build_named_backend(
            name=_canonical_backend_name(name), repo_root=repo_root, activity=activity
        )
    configured = list(
        dict.fromkeys(  # de-dupe, keep configured order
            _canonical_backend_name(part)
            for part in (env_value("MERV_EXECUTION_BACKENDS") or "").split(",")
            if part.strip()
        )
    )
    single = _canonical_backend_name(env_value("MERV_EXECUTION_BACKEND") or "")
    if len(configured) <= 1:
        return _build_named_backend(
            name=configured[0] if configured else (single or DEFAULT_SANDBOX_DRIVER),
            repo_root=repo_root,
            activity=activity,
        )
    backends = {
        backend_name: _build_named_backend(
            name=backend_name, repo_root=repo_root, activity=activity
        )
        for backend_name in configured
    }
    default = single if single in backends else configured[0]
    return MultiplexingSandboxBackend(
        backends=backends, default=default, aliases=BACKEND_ALIASES
    )
