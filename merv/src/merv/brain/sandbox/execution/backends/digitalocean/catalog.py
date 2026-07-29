# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Shape DigitalOcean sizes into the agent selection menu.

Only sizes carrying ``gpu_info`` are offered — this backend exists for GPU
droplets. GPU sizes are hidden until DigitalOcean unlocks them for the
account, so an empty menu usually means "request GPU access in the console",
not "sold out".
"""

from __future__ import annotations

from typing import Any

from .._values import _float_or_none, _int_or_zero, _norm, find_option, price_sort_key


def to_agent_options(
    sizes: list[dict[str, Any]],
    *,
    gpu: str | None = None,
    region: str | None = None,
    only_available: bool = True,
) -> list[dict[str, Any]]:
    gpu_filter = _norm(gpu)
    region_filter = _norm(region)
    options: list[dict[str, Any]] = []
    for size in sizes:
        gpu_info = size.get("gpu_info")
        if not isinstance(gpu_info, dict):
            continue
        slug = str(size.get("slug") or "")
        model = _gpu_label(str(gpu_info.get("model") or ""))
        regions = [str(r) for r in size.get("regions", []) or []]
        available = bool(size.get("available")) and bool(regions)
        if only_available and not available:
            continue
        if region_filter and region_filter not in {r.lower() for r in regions}:
            continue
        if gpu_filter and gpu_filter not in _norm(model) and gpu_filter not in _norm(slug):
            continue
        options.append(
            {
                "instance_type": slug,
                "gpu": model,
                "gpu_description": str(size.get("description") or model),
                "gpu_count": _int_or_zero(gpu_info.get("count")),
                "vcpus": _int_or_zero(size.get("vcpus")),
                # DigitalOcean reports droplet memory in MiB.
                "memory_gib": _int_or_zero(size.get("memory")) // 1024,
                "storage_gib": _int_or_zero(size.get("disk")),
                # None, never 0.0: an unpriced SKU must stay unknown so the
                # cost policy fails closed instead of billing "free" hardware.
                "price_usd_per_hour": _float_or_none(size.get("price_hourly")),
                "regions": regions,
                "available": available,
            }
        )
    options.sort(key=price_sort_key)
    return options


def _gpu_label(model: str) -> str:
    """Short GPU label, e.g. 'H100' from 'nvidia_h100'."""
    return model.split("_")[-1].upper() if model else ""
