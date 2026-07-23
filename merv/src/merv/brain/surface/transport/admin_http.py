"""Control-admin HTTP routes kept out of the general UI route factory.

These are GLOBAL operator surfaces (tenant-wide cleanup, arbitrary tenant
counters). They are operator-only in hosted mode: the request gateway's
membership boundary gates every ``/api/admin`` path on MERV_ADMIN_TOKEN before
these handlers run, so LOCAL_PRINCIPAL keeps access while a hosted caller —
even a JWT owner — must present the operator token (INV-11 FIX 1).
"""

from __future__ import annotations

from typing import Any


def register_admin_routes(
    http: Any,
    *,
    cleanup: Any | None,
    tenant_counters: Any | None,
) -> None:
    if cleanup is None:
        return

    @http.post("/api/admin/cleanup")
    def admin_cleanup() -> dict[str, Any]:
        return {"cleaned": cleanup.run_all().as_dict()}

    @http.get("/api/admin/tenants/{tenant_id}/counters")
    def admin_tenant_counters(tenant_id: str) -> dict[str, Any]:
        assert tenant_counters is not None
        return tenant_counters(tenant_id=tenant_id)
