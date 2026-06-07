"""Compute-provider discovery helpers."""

from __future__ import annotations

from typing import Any, Protocol

from ..execution.backends.lambda_labs import LambdaCloudClient


class LambdaInstanceTypeClient(Protocol):
    def list_instance_types(self) -> dict[str, Any]: ...


class ComputeService:
    def __init__(self, *, lambda_client: LambdaInstanceTypeClient | None = None) -> None:
        self.lambda_client = lambda_client

    def lambda_available_gpus(
        self,
        *,
        region: str | None = None,
        gpu: str | None = None,
        instance_type: str | None = None,
        min_gpus: int | None = None,
        only_available: bool = True,
    ) -> dict[str, Any]:
        client = self.lambda_client or LambdaCloudClient()
        raw_types = client.list_instance_types()

        region_filter = _norm(region)
        gpu_filter = _norm(gpu)
        instance_type_filter = _norm(instance_type)
        min_gpu_count = int(min_gpus) if min_gpus is not None else None

        entries: list[dict[str, Any]] = []
        for name, item in sorted(raw_types.items()):
            if not isinstance(item, dict):
                continue
            instance = item.get("instance_type") or {}
            if not isinstance(instance, dict):
                continue
            entry_name = str(instance.get("name") or name)
            specs = instance.get("specs") if isinstance(instance.get("specs"), dict) else {}
            regions = [
                {
                    "name": str(region_item.get("name") or ""),
                    "description": str(region_item.get("description") or ""),
                }
                for region_item in item.get("regions_with_capacity_available", [])
                if isinstance(region_item, dict) and region_item.get("name")
            ]
            region_names = {str(item["name"]).lower() for item in regions}
            gpu_description = str(instance.get("gpu_description") or "")
            gpus = _int_or_zero(specs.get("gpus"))
            available = bool(regions)

            if only_available and not available:
                continue
            if region_filter and region_filter not in region_names:
                continue
            if gpu_filter and gpu_filter not in _norm(gpu_description) and gpu_filter not in _norm(entry_name):
                continue
            if instance_type_filter and instance_type_filter != _norm(entry_name):
                continue
            if min_gpu_count is not None and gpus < min_gpu_count:
                continue

            price_cents = _int_or_zero(instance.get("price_cents_per_hour"))
            entries.append(
                {
                    "name": entry_name,
                    "description": str(instance.get("description") or ""),
                    "gpu_description": gpu_description,
                    "price_cents_per_hour": price_cents,
                    "price_usd_per_hour": price_cents / 100.0,
                    "specs": {
                        "vcpus": _int_or_zero(specs.get("vcpus")),
                        "memory_gib": _int_or_zero(specs.get("memory_gib")),
                        "storage_gib": _int_or_zero(specs.get("storage_gib")),
                        "gpus": gpus,
                    },
                    "regions_with_capacity_available": regions,
                    "available": available,
                }
            )

        all_regions = sorted(
            {
                region_item["name"]
                for entry in entries
                for region_item in entry["regions_with_capacity_available"]
            }
        )
        return {
            "provider": "lambda_labs",
            "filters": {
                "region": region,
                "gpu": gpu,
                "instance_type": instance_type,
                "min_gpus": min_gpus,
                "only_available": only_available,
            },
            "count": len(entries),
            "regions": all_regions,
            "instance_types": entries,
        }


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
