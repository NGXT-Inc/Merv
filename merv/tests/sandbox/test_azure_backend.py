"""Azure backend: resource-group lifecycle, network choreography, GPU catalog."""

from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from tests.sandbox.driver_conformance import (
    assert_catalog_envelope,
    assert_driver_surface,
)
from merv.brain.sandbox.adapters import sandbox_driver_descriptor
from merv.brain.sandbox.adapters.azure import (
    AzureCloudConfig,
    AzureSandboxBackend,
    AzureSandboxConfig,
    _gpu_label,
    to_agent_options,
)
from merv.brain.sandbox.models import (
    BackendUnavailableError,
    BackendValidationError,
    CapacityUnavailableError,
    SandboxRequest,
)


SKUS = [
    {
        "resourceType": "virtualMachines",
        "name": "Standard_NC24ads_A100_v4",
        "capabilities": [
            {"name": "vCPUs", "value": "24"},
            {"name": "GPUs", "value": "1"},
            {"name": "MemoryGB", "value": "220"},
        ],
        "restrictions": [],
    },
    {
        "resourceType": "virtualMachines",
        "name": "Standard_ND96asr_v4",
        "capabilities": [
            {"name": "vCPUs", "value": "96"},
            {"name": "GPUs", "value": "8"},
            {"name": "MemoryGB", "value": "900"},
        ],
        "restrictions": [
            {"reasonCode": "NotAvailableForSubscription", "type": "Location"}
        ],
    },
    {
        # CPU size: no GPUs capability, excluded from the menu.
        "resourceType": "virtualMachines",
        "name": "Standard_D8s_v5",
        "capabilities": [
            {"name": "vCPUs", "value": "8"},
            {"name": "MemoryGB", "value": "32"},
        ],
        "restrictions": [],
    },
    {
        # Disk SKU rows share the endpoint and must be ignored.
        "resourceType": "disks",
        "name": "Premium_LRS",
        "capabilities": [],
        "restrictions": [],
    },
]


def _cloud_config() -> AzureCloudConfig:
    return AzureCloudConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        subscription_id="sub",
    )


class FakeAzureClient:
    def __init__(self) -> None:
        self.config = _cloud_config()
        self.groups_created: list[str] = []
        self.groups_deleted: list[str] = []
        self.network_puts: list[tuple[str, str]] = []
        self.vm_puts: list[dict] = []
        self.vms: dict[str, dict] = {}
        self.get_calls = 0
        self.price = 3.67

    def list_gpu_skus(self, *, location):
        return SKUS

    def retail_price(self, *, sku_name, location):
        return self.price

    def put_resource_group(self, *, name, location):
        self.groups_created.append(name)

    def get_resource_group(self, name):
        return {"name": name}

    def delete_resource_group(self, name):
        if name not in self.groups_created:
            raise BackendUnavailableError("not found", status=404)
        self.groups_deleted.append(name)

    def put_network_resource(self, *, resource_group, kind, name, body):
        self.network_puts.append((kind, name))
        return self._network_resource(kind=kind, name=name, body=body)

    def get_network_resource(self, *, resource_group, kind, name):
        return self._network_resource(kind=kind, name=name, body={})

    def _network_resource(self, *, kind, name, body):
        resource = {
            "id": f"/subscriptions/sub/{kind}/{name}",
            "name": name,
            "properties": {"provisioningState": "Succeeded"},
        }
        if kind == "virtualNetworks":
            resource["properties"]["subnets"] = [
                {"id": f"/subscriptions/sub/{kind}/{name}/subnets/sandbox"}
            ]
        if kind == "publicIPAddresses":
            resource["properties"]["ipAddress"] = "20.42.10.20"
        return resource

    def put_vm(self, *, resource_group, name, body):
        self.vm_puts.append(body)
        self.vms[name] = {
            "name": name,
            "tags": {"merv-sandbox": "1"},
            "properties": {"provisioningState": "Creating"},
        }
        return self.vms[name]

    def get_vm(self, *, resource_group, name):
        vm = self.vms.get(name)
        if vm is None:
            raise BackendUnavailableError("not found", status=404)
        self.get_calls += 1
        if self.get_calls >= 2:
            vm = {**vm, "properties": {"provisioningState": "Succeeded"}}
        return vm

    def list_vms(self):
        return list(self.vms.values())


def _backend(client: FakeAzureClient) -> AzureSandboxBackend:
    config = AzureSandboxConfig(
        cloud=client.config,
        poll_timeout_seconds=5,
        poll_interval_seconds=0.001,
    )
    return AzureSandboxBackend(config=config, client=client)  # type: ignore[arg-type]


def _request(**overrides) -> SandboxRequest:
    fields = {
        "experiment_id": "exp_1",
        "project_id": "proj_1",
        "public_key": "ssh-ed25519 AAAA caller",
        "sandbox_uid": "uid123",
        "management_public_key": "ssh-ed25519 BBBB mgmt",
        "instance_type": "Standard_NC24ads_A100_v4",
    }
    fields.update(overrides)
    return SandboxRequest(**fields)


class AzureAcquireTest(unittest.TestCase):
    def test_acquire_builds_the_full_network_and_returns_public_ip(self) -> None:
        client = FakeAzureClient()
        backend = _backend(client)
        with patch.object(AzureSandboxBackend, "_wait_for_ssh"):
            provisioned = backend.acquire(request=_request())

        self.assertEqual(provisioned.sandbox_id, "rp-uid123")
        self.assertEqual(provisioned.ssh_host, "20.42.10.20")
        self.assertEqual(provisioned.ssh_user, "ubuntu")
        self.assertEqual(provisioned.region, "eastus")
        self.assertEqual(provisioned.gpu, "A100")
        self.assertEqual(provisioned.price_usd_per_hour, 3.67)
        self.assertEqual(client.groups_created, ["rp-uid123-rg"])
        self.assertEqual(
            [kind for kind, _ in client.network_puts],
            [
                "networkSecurityGroups",
                "virtualNetworks",
                "publicIPAddresses",
                "networkInterfaces",
            ],
        )
        body = client.vm_puts[0]
        os_profile = body["properties"]["osProfile"]
        decoded = base64.b64decode(os_profile["customData"]).decode("utf-8")
        self.assertIn("#!/usr/bin/env bash", decoded)
        self.assertEqual(os_profile["adminUsername"], "ubuntu")
        keys = os_profile["linuxConfiguration"]["ssh"]["publicKeys"]
        self.assertEqual(keys[0]["keyData"], "ssh-ed25519 AAAA caller")

    def test_acquire_requires_instance_type(self) -> None:
        backend = _backend(FakeAzureClient())
        with self.assertRaisesRegex(BackendValidationError, "instance_type"):
            backend.acquire(request=_request(instance_type=""))

    def test_acquire_restricted_size_raises_capacity_error(self) -> None:
        backend = _backend(FakeAzureClient())
        with self.assertRaises(CapacityUnavailableError):
            backend.acquire(request=_request(instance_type="Standard_ND96asr_v4"))

    def test_acquire_unknown_size_lists_gpu_sizes(self) -> None:
        backend = _backend(FakeAzureClient())
        with self.assertRaisesRegex(BackendValidationError, "Standard_NC24ads_A100_v4"):
            backend.acquire(request=_request(instance_type="Standard_NC6"))

    def test_acquire_failure_deletes_the_whole_resource_group(self) -> None:
        client = FakeAzureClient()
        backend = _backend(client)
        with patch.object(
            AzureSandboxBackend,
            "_wait_for_vm",
            side_effect=BackendUnavailableError("boom"),
        ):
            with self.assertRaises(BackendUnavailableError):
                backend.acquire(request=_request())
        self.assertEqual(client.groups_deleted, ["rp-uid123-rg"])


class AzureLifecycleTest(unittest.TestCase):
    def test_failed_vm_still_counts_as_alive(self) -> None:
        # A Failed/deallocated VM still holds billable disks in its group.
        client = FakeAzureClient()
        client.vms["rp-x"] = {
            "name": "rp-x",
            "properties": {"provisioningState": "Failed"},
        }
        client.get_calls = -100  # keep the stored state
        backend = _backend(client)
        self.assertTrue(backend.is_alive(sandbox_id="rp-x"))

    def test_deleted_vm_is_gone(self) -> None:
        backend = _backend(FakeAzureClient())
        self.assertFalse(backend.is_alive(sandbox_id="rp-missing"))

    def test_terminate_deletes_the_resource_group(self) -> None:
        client = FakeAzureClient()
        client.groups_created.append("rp-uid123-rg")
        backend = _backend(client)
        self.assertTrue(backend.terminate(sandbox_id="rp-uid123"))
        self.assertEqual(client.groups_deleted, ["rp-uid123-rg"])

    def test_terminate_treats_missing_group_as_success(self) -> None:
        backend = _backend(FakeAzureClient())
        self.assertTrue(backend.terminate(sandbox_id="rp-gone"))

    def test_find_sandbox_id_matches_tagged_vms_only(self) -> None:
        client = FakeAzureClient()
        client.vms["rp-uid123"] = {"name": "rp-uid123", "tags": {"merv-sandbox": "1"}}
        client.vms["rp-other"] = {"name": "rp-other", "tags": {}}
        backend = _backend(client)
        self.assertEqual(
            backend.find_sandbox_id(experiment_id="exp_1", sandbox_uid="uid123"),
            "rp-uid123",
        )
        self.assertIsNone(
            backend.find_sandbox_id(experiment_id="exp_1", sandbox_uid="other")
        )


class AzureCatalogTest(unittest.TestCase):
    def test_options_offer_only_deployable_gpu_sizes(self) -> None:
        options = to_agent_options(SKUS, location="eastus")
        self.assertEqual(
            [o["instance_type"] for o in options], ["Standard_NC24ads_A100_v4"]
        )
        option = options[0]
        self.assertEqual(option["gpu"], "A100")
        self.assertEqual(option["vcpus"], 24)
        self.assertEqual(option["memory_gib"], 220)

    def test_restricted_sizes_stay_visible_when_not_filtering(self) -> None:
        options = to_agent_options(SKUS, location="eastus", only_available=False)
        by_name = {o["instance_type"]: o for o in options}
        self.assertFalse(by_name["Standard_ND96asr_v4"]["available"])

    def test_gpu_labels_cover_named_and_family_encoded_sizes(self) -> None:
        self.assertEqual(_gpu_label("Standard_NC24ads_A100_v4"), "A100")
        self.assertEqual(_gpu_label("Standard_NV36ads_A10_v5"), "A10")
        self.assertEqual(_gpu_label("Standard_NC6s_v3"), "K80")
        self.assertEqual(_gpu_label("Standard_ND40rs_v2"), "V100")

    def test_surface_and_catalog_conformance(self) -> None:
        descriptor = sandbox_driver_descriptor("azure")
        backend = _backend(FakeAzureClient())
        assert_driver_surface(self, descriptor=descriptor, backend=backend)
        catalog = assert_catalog_envelope(self, descriptor=descriptor, backend=backend)
        self.assertIn("resource group", catalog["reason"])


if __name__ == "__main__":
    unittest.main()
