"""Per-project compute-provider connections (Sandboxes → Configure).

The write path behind the provider setup wizard: collect a cloud's
credentials once (or adopt the deployment's shared platform credentials —
Lambda Labs by default), confirm them with a real API call, then flip the
agent-facing enable switch. Secret values are WRITE-ONLY — the overview
reports which keys are set (and non-secret values, so forms can re-render),
never a secret itself; the raw JSON is read back only internally. The enable
switch exists only on a set-up provider: ``set_enabled(True)`` refuses
otherwise. A provider with no row is untouched default state: env-configured
providers stay usable until a row explicitly disables them, which
``ensure_provider_allowed`` (the SandboxEngine admission hook) enforces at
request time; the per-provider daily USD cap is enforced separately by quota
admission reading the same rows.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ..kernel.env import env_value
from ..kernel.state.store import BaseStateStore
from ..kernel.utils import ValidationError, now_iso


class FleetResolver(Protocol):
    """``() -> (fleet_names, default)`` — composition injects the adapters-
    layer resolver so this facade never imports bootstrap construction code."""

    def __call__(self) -> tuple[list[str], str]: ...


class CredentialCheck(Protocol):
    """One provider's access probe: returns a human detail line on success,
    raises kernel ``ValidationError`` with the reason on any failure."""

    def __call__(self, values: Mapping[str, str]) -> str: ...


class ProviderFieldSpec(Protocol):
    """One connection-form input, as the adapters catalog declares it."""

    key: str
    label: str
    help: str
    secret: bool
    required: bool
    placeholder: str
    multiline: bool


class ProviderCatalogSpec(Protocol):
    """One connectable provider from the adapters catalog. Injected by
    composition (like ``FleetResolver``) so this facade stays free of
    provider knowledge — the catalog is data, never dispatch."""

    name: str
    label: str
    console_url: str
    note: str
    platform_default: bool
    fields: tuple[ProviderFieldSpec, ...]

    def required_fields(self) -> tuple[ProviderFieldSpec, ...]: ...

    def field(self, key: str) -> ProviderFieldSpec | None: ...

    def env_configured(self) -> bool: ...

    def env_values(self) -> dict[str, str]: ...


# Generous caps so a hostile body cannot store unbounded blobs; the largest
# real value is a GCP service-account JSON (~2.5 KB).
_MAX_SECRET_CHARS = 8000
_MAX_VALUE_CHARS = 500

_PLATFORM_LABEL = "RapidReview credentials"
_MODES = ("", "own", "platform")


class SandboxProviderSettings:
    """Read the provider overview; save/verify credentials; gate the switch."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        fleet: FleetResolver,
        catalog: Sequence[ProviderCatalogSpec],
        checks: Mapping[str, CredentialCheck] | None = None,
    ) -> None:
        self._store = store
        self._fleet = fleet
        self._catalog = tuple(catalog)
        self._by_name = {spec.name: spec for spec in self._catalog}
        self._checks = dict(checks or {})

    # ---------- reads ----------

    def overview(self, *, project_id: str) -> dict[str, Any]:
        rows = self._rows(project_id=project_id)
        fleet, default = self._fleet()
        fleet_names = fleet or [default]
        providers = [
            self._entry(
                spec=spec,
                row=rows.get(spec.name),
                fleet_names=fleet_names,
                default=default,
            )
            for spec in self._catalog
        ]
        return {
            "providers": providers,
            "fleet": {"names": fleet_names, "default": default},
        }

    # ---------- writes ----------

    def set_credentials(
        self,
        *,
        project_id: str,
        provider: str,
        values: Mapping[str, Any] | None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Merge submitted field values into the saved credentials (empty
        string clears a field; omitted fields keep their stored value) and/or
        record the credential mode. Any credential change un-verifies the
        connection until the next successful ``verify``."""
        spec = self._require_spec(provider)
        submitted = dict(values or {})
        if mode is not None:
            mode = str(mode).strip().lower()
            if mode not in _MODES:
                raise ValidationError(
                    f"unknown credential mode {mode!r}; use 'own' or 'platform'"
                )
            if mode == "platform" and not self._platform_available(spec):
                raise ValidationError(
                    f"{spec.label} has no {_PLATFORM_LABEL} on this deployment "
                    "— supply your own credentials instead"
                )
        if not submitted and mode is None:
            raise ValidationError("no fields were submitted")
        # A provider's FIRST connection write creates its row switched off:
        # enabling is the wizard's explicit final act, never a side effect.
        # "First" means no prior connection — a cap-only row (daily limit set
        # before the wizard ran) counts as unconnected, or the invariant
        # would leak through that path. (No-row env-fleet providers keep
        # their enabled-by-default state until someone connects them.)
        existing = self._rows(project_id=project_id).get(spec.name)
        first_write = existing is None or (
            not self._parse_row_credentials(existing)
            and not str(existing.get("credential_mode") or "")
        )
        # Switching modes changes which credentials a provision would use,
        # so it un-verifies exactly like a value write does.
        stored_mode = (
            str(existing.get("credential_mode") or "") if existing else ""
        )
        mode_changed = mode is not None and mode != stored_mode
        credentials_json: str | None = None
        if submitted:
            saved = self._saved(project_id=project_id, provider=spec.name)
            for key, raw in submitted.items():
                field = spec.field(str(key))
                if field is None:
                    known = ", ".join(f.key for f in spec.fields)
                    raise ValidationError(
                        f"unknown field {key!r} for provider {spec.name}; "
                        f"known fields: {known}"
                    )
                value = str(raw or "").strip()
                cap = _MAX_SECRET_CHARS if field.multiline else _MAX_VALUE_CHARS
                if len(value) > cap:
                    raise ValidationError(
                        f"{field.key} is too long (max {cap} characters)"
                    )
                if value:
                    saved[field.key] = value
                else:
                    saved.pop(field.key, None)
            credentials_json = json.dumps(saved, sort_keys=True)
            if mode is None:
                mode = "own"  # supplying values IS choosing your own creds
        self._store.upsert_sandbox_provider_settings(
            project_id=project_id,
            provider=spec.name,
            credentials_json=credentials_json,
            credential_mode=mode,
            enabled=False if first_write else None,
            verified_at="" if mode_changed else None,
        )
        return self._refreshed_entry(project_id=project_id, spec=spec)

    def set_enabled(
        self, *, project_id: str, provider: str, enabled: bool
    ) -> dict[str, Any]:
        """Flip the agent-facing switch. Enabling requires a completed setup
        (own credentials saved, platform credentials adopted, or environment
        configuration); disabling is always allowed."""
        spec = self._require_spec(provider)
        if enabled:
            row = self._rows(project_id=project_id).get(spec.name)
            if not self._setup_complete(spec=spec, row=row):
                raise ValidationError(
                    f"{spec.label} is not set up yet — complete the connection "
                    "wizard before enabling it for agents"
                )
        self._store.upsert_sandbox_provider_settings(
            project_id=project_id, provider=spec.name, enabled=bool(enabled)
        )
        return self._refreshed_entry(project_id=project_id, spec=spec)

    def set_daily_limit(
        self, *, project_id: str, provider: str, daily_usd_limit: float | None
    ) -> dict[str, Any]:
        spec = self._require_spec(provider)
        if daily_usd_limit is not None:
            try:
                daily_usd_limit = float(daily_usd_limit)
            except (TypeError, ValueError) as exc:
                raise ValidationError("daily_usd_limit must be a number") from exc
            # NaN compares false against spend (uncapping the provider) and
            # breaks JSON serialization of the stored row — refuse non-finite.
            if not math.isfinite(daily_usd_limit):
                raise ValidationError("daily_usd_limit must be a finite number")
            if daily_usd_limit < 0:
                raise ValidationError("daily_usd_limit cannot be negative")
        self._store.set_sandbox_provider_daily_limit(
            project_id=project_id,
            provider=spec.name,
            daily_usd_limit=daily_usd_limit,
        )
        return self._refreshed_entry(project_id=project_id, spec=spec)

    # ---------- verification ----------

    def verify(self, *, project_id: str, provider: str) -> dict[str, Any]:
        """Confirm the connection's effective credentials with one real
        provider API call. Success stamps ``verified_at``; failure reports the
        reason without changing state. Never returns credential values."""
        spec = self._require_spec(provider)
        check = self._checks.get(spec.name)
        if check is None:
            raise ValidationError(
                f"no credential check is implemented for {spec.name}"
            )
        row = self._rows(project_id=project_id).get(spec.name)
        values = self._effective_values(
            spec=spec, row=row, project_id=project_id
        )
        try:
            detail = check(values)
        except ValidationError as exc:
            return {
                "ok": False,
                "detail": str(exc),
                "provider": self._refreshed_entry(project_id=project_id, spec=spec),
            }
        self._store.upsert_sandbox_provider_settings(
            project_id=project_id, provider=spec.name, verified_at=now_iso()
        )
        return {
            "ok": True,
            "detail": detail,
            "provider": self._refreshed_entry(project_id=project_id, spec=spec),
        }

    # ---------- engine admission hook ----------

    def ensure_provider_allowed(self, *, project_id: str, provider: str) -> None:
        """SandboxEngine request-time gate: a row that switched a provider off
        blocks procurement on it. No row (or an unknown/managed provider name,
        e.g. modal) stays allowed — the switch only ever narrows."""
        for row in self._store.list_sandbox_provider_settings(
            project_id=project_id
        ):
            if row["provider"] == provider and not row["enabled"]:
                raise ValidationError(
                    f"sandbox provider {provider} is disabled for this project "
                    "— enable it under Sandboxes → Configure or request a "
                    "different provider"
                )

    # ---------- internals ----------

    def _rows(self, *, project_id: str) -> dict[str, dict[str, Any]]:
        return {
            row["provider"]: row
            for row in self._store.list_sandbox_provider_settings(
                project_id=project_id
            )
        }

    def _require_spec(self, provider: str) -> ProviderCatalogSpec:
        spec = self._by_name.get(str(provider or "").strip().lower())
        if spec is None:
            known = ", ".join(s.name for s in self._catalog)
            raise ValidationError(
                f"unknown provider {provider!r}; connectable providers: {known}"
            )
        return spec

    def _platform_available(self, spec: ProviderCatalogSpec) -> bool:
        """Platform credentials exist when the provider is in the deployment's
        platform set AND the deployment environment actually carries them."""
        configured = env_value("MERV_PLATFORM_PROVIDERS")
        if configured is not None:
            names = {
                part.strip().lower()
                for part in configured.split(",")
                if part.strip()
            }
            in_set = spec.name in names
        else:
            in_set = spec.platform_default
        return in_set and spec.env_configured()

    def _saved(self, *, project_id: str, provider: str) -> dict[str, str]:
        raw = self._store.sandbox_provider_credentials(
            project_id=project_id, provider=provider
        )
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items() if str(v or "")}

    @staticmethod
    def _parse_row_credentials(row: dict[str, Any] | None) -> dict[str, str]:
        if row is None:
            return {}
        try:
            parsed = json.loads(row["credentials"])
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items() if str(v or "")}

    def _own_connected(
        self, *, spec: ProviderCatalogSpec, saved: Mapping[str, str]
    ) -> bool:
        return all(f.key in saved for f in spec.required_fields())

    def _setup_complete(
        self, *, spec: ProviderCatalogSpec, row: dict[str, Any] | None
    ) -> bool:
        saved = self._parse_row_credentials(row)
        mode = str(row["credential_mode"] or "") if row else ""
        if mode == "platform":
            return self._platform_available(spec)
        if self._own_connected(spec=spec, saved=saved):
            return True
        # No explicit choice and nothing saved: environment configuration
        # (a self-hosted operator's env vars) counts as set up.
        return mode == "" and spec.env_configured()

    def _effective_values(
        self,
        *,
        spec: ProviderCatalogSpec,
        row: dict[str, Any] | None,
        project_id: str,
    ) -> dict[str, str]:
        """The credentials a provision would actually use: saved values in
        'own' mode, the deployment environment in 'platform' (or env-only)
        setups."""
        saved = self._parse_row_credentials(row)
        mode = str(row["credential_mode"] or "") if row else ""
        if mode == "platform":
            return spec.env_values()
        if saved:
            return saved
        return spec.env_values()

    def _refreshed_entry(
        self, *, project_id: str, spec: ProviderCatalogSpec
    ) -> dict[str, Any]:
        fleet, default = self._fleet()
        return self._entry(
            spec=spec,
            row=self._rows(project_id=project_id).get(spec.name),
            fleet_names=fleet or [default],
            default=default,
        )

    def _entry(
        self,
        *,
        spec: ProviderCatalogSpec,
        row: dict[str, Any] | None,
        fleet_names: list[str],
        default: str,
    ) -> dict[str, Any]:
        saved = self._parse_row_credentials(row)
        fields = [
            {
                "key": field.key,
                "label": field.label,
                "help": field.help,
                "secret": field.secret,
                "required": field.required,
                "placeholder": field.placeholder,
                "multiline": field.multiline,
                "set": field.key in saved,
                # Non-secret saved values re-render in the form; secrets never
                # leave the server — "set" is all the UI learns.
                "value": "" if field.secret else saved.get(field.key, ""),
            }
            for field in spec.fields
        ]
        own_connected = self._own_connected(spec=spec, saved=saved)
        env_configured = spec.env_configured()
        platform_available = self._platform_available(spec)
        mode = str(row["credential_mode"] or "") if row else ""
        setup_complete = self._setup_complete(spec=spec, row=row)
        return {
            "provider": spec.name,
            "label": spec.label,
            "console_url": spec.console_url,
            "note": spec.note,
            "fields": fields,
            "enabled": bool(row["enabled"]) if row is not None else True,
            "connected": own_connected,
            "env_configured": env_configured,
            "platform_available": platform_available,
            "platform_label": _PLATFORM_LABEL if platform_available else "",
            "credential_mode": mode,
            "setup_complete": setup_complete,
            "credential_source": (
                "platform"
                if (mode == "platform" and platform_available)
                else "saved"
                if own_connected
                else ("env" if env_configured else None)
            ),
            "daily_usd_limit": row["daily_usd_limit"] if row else None,
            "verified_at": str(row["verified_at"] or "") if row else "",
            "in_env_fleet": spec.name in fleet_names,
            "fleet_default": spec.name == default,
            "updated_at": str(row["updated_at"]) if row is not None else "",
        }


__all__ = ["SandboxProviderSettings"]
