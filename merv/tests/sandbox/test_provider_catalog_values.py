from __future__ import annotations

import unittest

from merv.brain.sandbox.execution.backends._values import (
    _float_or_none,
    find_option,
    price_sort_key,
)
from merv.brain.sandbox.execution.backends.digitalocean.catalog import (
    find_option as digitalocean_find_option,
    to_agent_options as digitalocean_options,
)
from merv.brain.sandbox.execution.backends.hyperstack.catalog import (
    find_option as hyperstack_find_option,
    to_agent_options as hyperstack_options,
)
from merv.brain.sandbox.execution.backends.lambda_labs.catalog import (
    summarize_instance_types as lambda_summary,
    to_agent_options as lambda_options,
)
from merv.brain.sandbox.execution.backends.tensordock.catalog import (
    to_agent_options as tensordock_options,
)
from merv.brain.sandbox.execution.backends.thunder_compute.catalog import (
    summarize_specs as thunder_summary,
)
from merv.brain.sandbox.execution.backends.verda.catalog import (
    find_option as verda_find_option,
    to_agent_options as verda_options,
)
from merv.brain.sandbox.execution.backends.voltage_park.catalog import (
    find_option as voltage_park_find_option,
    to_agent_options as voltage_park_options,
)


class ProviderCatalogValueTest(unittest.TestCase):
    def test_catalogs_reexport_one_normalized_option_lookup(self) -> None:
        implementations = (
            digitalocean_find_option,
            hyperstack_find_option,
            verda_find_option,
            voltage_park_find_option,
        )
        self.assertTrue(
            all(implementation is find_option for implementation in implementations)
        )
        options = [{"instance_type": "  H100-SXM  "}]
        self.assertIs(find_option(options, instance_type="h100-sxm"), options[0])
        self.assertIsNone(find_option(options, instance_type="missing"))

    def test_a_missing_or_malformed_price_is_unknown_not_free(self) -> None:
        for value in (None, "", "n/a", {}, [], float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(_float_or_none(value))
        # A provider's own explicit zero is a real quote and stays known.
        self.assertEqual(_float_or_none(0), 0.0)
        self.assertEqual(_float_or_none("0.00"), 0.0)
        self.assertEqual(_float_or_none("2.44"), 2.44)

    def test_unpriced_options_sort_last_not_first(self) -> None:
        options = [
            {"instance_type": "unpriced", "price_usd_per_hour": None},
            {"instance_type": "free", "price_usd_per_hour": 0.0},
            {"instance_type": "dear", "price_usd_per_hour": 9.0},
        ]
        ordered = [o["instance_type"] for o in sorted(options, key=price_sort_key)]
        self.assertEqual(ordered, ["free", "dear", "unpriced"])


class ProviderPriceFailClosedTest(unittest.TestCase):
    """Every real adapter keeps an unquoted SKU UNPRICED (audit SAN-04).

    A price coerced to ``0.0`` reads to the admission path as "the provider
    quoted free", so a tenant with a USD budget procures the machine and the
    spend never lands anywhere a ceiling can see it. Each case below is one
    provider's real payload shape with its price field missing or garbled.
    """

    def test_digitalocean_size_without_a_price_hourly(self) -> None:
        sizes = [
            {
                "slug": "gpu-h100x1-80gb",
                "vcpus": 20,
                "memory": 245760,
                "disk": 720,
                "regions": ["nyc2"],
                "available": True,
                "gpu_info": {"count": 1, "model": "nvidia_h100"},
            },
            {
                "slug": "gpu-h100x8-640gb",
                "vcpus": 160,
                "memory": 1966080,
                "disk": 2046,
                "price_hourly": "not-a-number",
                "regions": ["nyc2"],
                "available": True,
                "gpu_info": {"count": 8, "model": "nvidia_h100"},
            },
        ]
        prices = {
            option["instance_type"]: option["price_usd_per_hour"]
            for option in digitalocean_options(sizes)
        }
        self.assertIsNone(prices["gpu-h100x1-80gb"])
        self.assertIsNone(prices["gpu-h100x8-640gb"])

    def test_verda_instance_type_without_a_price_per_hour(self) -> None:
        options = verda_options(
            [
                {
                    "instance_type": "1H100.80S.30V",
                    "model": "H100",
                    "gpu": {"number_of_gpus": 1},
                    "cpu": {"number_of_cores": 30},
                    "memory": {"size_in_gigabytes": 120},
                }
            ],
            [{"location_code": "FIN-01", "availabilities": ["1H100.80S.30V"]}],
        )
        self.assertIsNone(options[0]["price_usd_per_hour"])

    def test_hyperstack_flavor_absent_from_the_pricebook(self) -> None:
        groups = [
            {
                "gpu": "A100-80G-PCIe",
                "region_name": "CANADA-1",
                "flavors": [
                    {
                        "name": "n3-A100x1",
                        "region_name": "CANADA-1",
                        "gpu": "A100-80G-PCIe",
                        "gpu_count": 1,
                        "stock_available": True,
                    }
                ],
            }
        ]
        self.assertIsNone(
            hyperstack_options(groups, [])[0]["price_usd_per_hour"]
        )
        # Present in the pricebook at an explicit zero: a known price.
        self.assertEqual(
            hyperstack_options(groups, [{"name": "n3-A100x1", "value": "0"}])[0][
                "price_usd_per_hour"
            ],
            0.0,
        )

    def test_lambda_instance_type_without_price_cents(self) -> None:
        raw = {
            "gpu_1x_a10": {
                "instance_type": {
                    "name": "gpu_1x_a10",
                    "gpu_description": "A10",
                    "specs": {"vcpus": 30, "memory_gib": 200, "storage_gib": 1400, "gpus": 1},
                },
                "regions_with_capacity_available": [{"name": "us-west-1"}],
            }
        }
        summary = lambda_summary(raw)
        self.assertIsNone(summary["instance_types"][0]["price_usd_per_hour"])
        self.assertIsNone(summary["instance_types"][0]["price_cents_per_hour"])
        self.assertIsNone(lambda_options(summary)[0]["price_usd_per_hour"])

    def test_tensordock_gpu_without_an_hourly_rate(self) -> None:
        locations = [
            {
                "id": "loc-1",
                "city": "Austin",
                "country": "United States",
                "gpus": [
                    {
                        "v0Name": "h100-sxm5-80gb",
                        "displayName": "H100 SXM5 80GB",
                        "max_count": 1,
                        "resources": {
                            "max_vcpus": 128,
                            "max_ram_gb": 300,
                            "max_storage_gb": 1000,
                        },
                        # Add-on rates alone are not a machine price.
                        "pricing": {"per_vcpu_hr": 0.003, "per_gb_ram_hr": 0.002},
                        "network_features": {"dedicated_ip_available": True},
                    }
                ],
            }
        ]
        self.assertIsNone(tensordock_options(locations)[0]["price_usd_per_hour"])

    def test_voltage_park_preset_missing_either_rate(self) -> None:
        def _locations(**rates: str) -> list[dict]:
            return [
                {
                    "id": "loc-1",
                    "available_presets": [
                        {
                            "id": "preset-1",
                            "resources": {
                                "gpus": {"h100-sxm5-80gb": {"count": 1}},
                                "vcpu_count": 16,
                                "ram_gb": 128,
                                "storage_gb": 500,
                            },
                            "operating_system": "Ubuntu 22.04 LTS",
                            "available_vms": 3,
                            **rates,
                        }
                    ],
                }
            ]

        self.assertIsNone(
            voltage_park_options(_locations(compute_rate_hourly="2.5"))[0][
                "price_usd_per_hour"
            ]
        )
        self.assertIsNone(
            voltage_park_options(_locations(storage_rate_hourly="0.05"))[0][
                "price_usd_per_hour"
            ]
        )
        self.assertEqual(
            voltage_park_options(
                _locations(compute_rate_hourly="2.50", storage_rate_hourly="0.05")
            )[0]["price_usd_per_hour"],
            2.55,
        )

    def test_thunder_spec_with_no_published_price(self) -> None:
        specs = {
            "a100xl_x1_prototyping": {
                "displayName": "NVIDIA A100 (80GB)",
                "gpuCount": 1,
                "mode": "prototyping",
                "vcpuOptions": [4],
                "ramPerVCPUGiB": 8,
                "storageGB": {"min": 100, "max": 500},
            }
        }
        summary = thunder_summary(specs, pricing={}, template="base")
        self.assertIsNone(summary["instance_types"][0]["price_usd_per_hour"])
        garbled = thunder_summary(
            specs, pricing={"a100xl_x1_prototyping": "free!"}, template="base"
        )
        self.assertIsNone(garbled["instance_types"][0]["price_usd_per_hour"])
        priced = thunder_summary(
            specs, pricing={"a100xl_x1_prototyping": 0.78}, template="base"
        )
        self.assertEqual(priced["instance_types"][0]["price_usd_per_hour"], 0.78)


if __name__ == "__main__":
    unittest.main()
