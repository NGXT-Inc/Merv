from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from merv.brain.sandbox.adapters import build_sandbox_backend
from merv.brain.sandbox.adapters.lambda_labs import (
    summarize_instance_types,
)
from merv.brain.sandbox.adapters.lambda_labs import LambdaCloudConfig
from merv.brain.sandbox.adapters.lambda_labs import (
    LambdaLabsSandboxBackend,
    build_user_data,
    _sandbox_name,
)
from merv.brain.sandbox.adapters.lambda_labs import LambdaSandboxConfig
from merv.brain.sandbox.remote.vm_bootstrap import REC_SCRIPT
from merv.brain.sandbox.models import BackendValidationError, SandboxRequest


INSTANCE_TYPES = {
    "gpu_1x_a10": {
        "instance_type": {
            "name": "gpu_1x_a10",
            "description": "1x A10",
            "gpu_description": "A10",
            "price_cents_per_hour": 75,
            "specs": {"vcpus": 30, "memory_gib": 200, "storage_gib": 1400, "gpus": 1},
        },
        "regions_with_capacity_available": [
            {"name": "us-west-1", "description": "California, USA"},
        ],
    },
    "gpu_8x_h100_sxm5": {
        "instance_type": {
            "name": "gpu_8x_h100_sxm5",
            "description": "8x H100",
            "gpu_description": "H100 (80 GB SXM5)",
            "price_cents_per_hour": 3592,
            "specs": {
                "vcpus": 208,
                "memory_gib": 1800,
                "storage_gib": 24780,
                "gpus": 8,
            },
        },
        "regions_with_capacity_available": [
            {"name": "us-east-1", "description": "Virginia, USA"},
        ],
    },
    "gpu_8x_a100": {
        "instance_type": {
            "name": "gpu_8x_a100",
            "description": "8x A100",
            "gpu_description": "A100",
            "price_cents_per_hour": 1592,
            "specs": {"vcpus": 124, "memory_gib": 1800, "storage_gib": 6144, "gpus": 8},
        },
        "regions_with_capacity_available": [],
    },
}


class FakeLambdaSandboxClient:
    def __init__(self) -> None:
        self.launches: list[dict] = []
        self.keys: list[dict] = []
        self.deleted_keys: list[str] = []
        self.terminated: list[list[str]] = []
        self.get_calls = 0

    def list_instance_types(self):
        return INSTANCE_TYPES

    def add_ssh_key(self, *, name: str, public_key: str):
        key = {"id": "key_1", "name": name, "public_key": public_key}
        self.keys.append(key)
        return key

    def launch_instance(self, **kwargs):
        self.launches.append(kwargs)
        return "inst_1"

    def get_instance(self, instance_id: str):
        self.get_calls += 1
        if self.get_calls == 1:
            return {
                "id": instance_id,
                "name": "rp-exp1",
                "status": "booting",
                "ssh_key_names": ["rp-exp1-key"],
            }
        return {
            "id": instance_id,
            "name": "rp-exp1",
            "status": "active",
            "ip": "198.51.100.2",
            "ssh_key_names": ["rp-exp1-key"],
        }

    def terminate_instances(self, instance_ids: list[str]):
        self.terminated.append(instance_ids)
        return [{"id": instance_ids[0], "status": "terminating"}]

    def list_ssh_keys(self):
        return self.keys

    def delete_ssh_key(self, key_id: str):
        self.deleted_keys.append(key_id)


@contextmanager
def fake_socket_connection(*_args, **_kwargs):
    yield object()


class LambdaAvailabilityTest(unittest.TestCase):
    def test_filters_current_capacity_by_region_gpu_and_min_gpu_count(self) -> None:
        result = summarize_instance_types(
            INSTANCE_TYPES,
            region="us-east-1",
            gpu="h100",
            min_gpus=8,
        )

        self.assertEqual(result["provider"], "lambda_labs")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["regions"], ["us-east-1"])
        row = result["instance_types"][0]
        self.assertEqual(row["name"], "gpu_8x_h100_sxm5")
        self.assertEqual(row["specs"]["gpus"], 8)
        self.assertEqual(row["price_usd_per_hour"], 35.92)

    def test_can_include_instance_types_with_no_capacity(self) -> None:
        result = summarize_instance_types(
            INSTANCE_TYPES,
            instance_type="gpu_8x_a100",
            only_available=False,
        )

        self.assertEqual(result["count"], 1)
        self.assertFalse(result["instance_types"][0]["available"])
        self.assertEqual(
            result["instance_types"][0]["regions_with_capacity_available"], []
        )

    def test_env_config_accepts_research_plugin_api_key(self) -> None:
        with patch.dict(
            os.environ, {"RESEARCH_PLUGIN_LAMBDA_API_KEY": "test-key"}, clear=True
        ):
            config = LambdaCloudConfig.from_env()

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.base_url, "https://cloud.lambda.ai/api/v1")

    def test_env_config_accepts_lambda_labs_api_key_alias(self) -> None:
        with patch.dict(os.environ, {"LAMBDA_LABS_API_KEY": "alias-key"}, clear=True):
            config = LambdaCloudConfig.from_env()

        self.assertEqual(config.api_key, "alias-key")

    def test_env_config_loads_lambda_env_file_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("LAMBDA_LABS_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"RESEARCH_PLUGIN_LAMBDA_ENV_FILE": str(env_file)},
                clear=True,
            ):
                config = LambdaCloudConfig.from_env()

        self.assertEqual(config.api_key, "file-key")

    def test_env_config_loads_legacy_modal_env_file_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("LAMBDA_LABS_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"RESEARCH_PLUGIN_MODAL_ENV_FILE": str(env_file)},
                clear=True,
            ):
                config = LambdaCloudConfig.from_env()

        self.assertEqual(config.api_key, "file-key")

    def test_env_config_requires_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(BackendValidationError):
                LambdaCloudConfig.from_env()

    def test_lambda_backend_launches_vm_with_agent_tooling_bootstrap(self) -> None:
        client = FakeLambdaSandboxClient()
        config = LambdaSandboxConfig(
            cloud=LambdaCloudConfig(api_key="test-key"),
            region_name="us-east-1",
            instance_type_name="gpu_8x_h100_sxm5",
            poll_interval_seconds=0.001,
            poll_timeout_seconds=1,
        )
        backend = LambdaLabsSandboxBackend(config=config, client=client)  # type: ignore[arg-type]
        request = SandboxRequest(
            experiment_id="exp1",
            project_id="proj1",
            public_key="ssh-ed25519 AAAA test",
            gpu="H100",
            time_limit=600,
        )

        with patch("socket.create_connection", fake_socket_connection):
            provisioned = backend.acquire(request=request)

        self.assertEqual(provisioned.sandbox_id, "inst_1")
        self.assertEqual(provisioned.ssh_host, "198.51.100.2")
        self.assertEqual(provisioned.ssh_port, 22)
        self.assertEqual(provisioned.ssh_user, "ubuntu")
        self.assertEqual(provisioned.workdir, "/workspace/exp1")
        self.assertEqual(provisioned.sync_dir, "/workspace/exp1")
        self.assertEqual(provisioned.unsynced_dir, "/workspace/data")
        self.assertEqual(provisioned.volume_name, "")
        self.assertEqual(client.keys[0]["name"], "rp-exp1-key")
        launch = client.launches[0]
        self.assertEqual(launch["region_name"], "us-east-1")
        self.assertEqual(launch["instance_type_name"], "gpu_8x_h100_sxm5")
        self.assertEqual(launch["ssh_key_name"], "rp-exp1-key")
        self.assertEqual(launch["name"], "rp-exp1")
        user_data = launch["user_data"]
        self.assertIn("apt-get install -y --no-install-recommends", user_data)
        self.assertIn("ripgrep", user_data)
        self.assertIn("fd-find", user_data)
        self.assertIn("jq", user_data)
        self.assertIn("MERV_EXPERIMENT_DIR=/workspace/exp1", user_data)
        self.assertIn("RP_SANDBOX_DATA_DIR=/workspace/data", user_data)
        self.assertIn("artifacts_to_keep", user_data)
        self.assertIn("chown -R ubuntu:ubuntu", user_data)
        self.assertIn("ForceCommand /opt/merv/rec.sh", user_data)
        # The temporarily removed tracking product must not leak back into a
        # newly provisioned user's environment through the VM bootstrap.
        self.assertNotIn("mlflow", user_data.lower())
        self.assertNotIn("tensorboard", user_data.lower())
        self.assertIn("install_with_uv_or_pip torch torchvision torchaudio", user_data)
        self.assertNotIn("start_dashboards.sh", user_data)

    def test_lambda_sandbox_name_is_lambda_hostname_safe(self) -> None:
        self.assertEqual(
            _sandbox_name("bench-gpu_1x_h100_sxm5-1780797943"),
            "rp-bench-gpu-1x-h100-sxm5-1780797943",
        )

    def test_lambda_backend_rejects_unavailable_configured_capacity(self) -> None:
        client = FakeLambdaSandboxClient()
        config = LambdaSandboxConfig(
            cloud=LambdaCloudConfig(api_key="test-key"),
            region_name="us-west-1",
            instance_type_name="gpu_8x_h100_sxm5",
            poll_interval_seconds=0.001,
            poll_timeout_seconds=1,
        )
        backend = LambdaLabsSandboxBackend(config=config, client=client)  # type: ignore[arg-type]

        with self.assertRaises(Exception) as ctx:
            backend.acquire(
                request=SandboxRequest(
                    experiment_id="exp1",
                    project_id="proj1",
                    public_key="ssh-ed25519 AAAA test",
                    gpu="H100",
                )
            )

        self.assertIn("no current capacity", str(ctx.exception))
        self.assertEqual(client.keys, [])
        self.assertEqual(client.launches, [])

    def test_lambda_user_data_contains_remote_workdir_and_data_dir(self) -> None:
        user_data = build_user_data(
            public_key="ssh-ed25519 AAAA test",
            experiment_id="exp1",
            workdir="/workspace/exp1",
            sessions_dir="/workspace/.merv_sessions/exp1",
            sandbox_data_dir="/workspace/data",
        )

        self.assertIn("RP_WORKDIR=/workspace/exp1", user_data)
        self.assertIn("MERV_EXPERIMENT_DIR=/workspace/exp1", user_data)
        self.assertIn("RP_SANDBOX_DATA_DIR=/workspace/data", user_data)
        self.assertIn("RP_SESSION_DIR=/workspace/.merv_sessions/exp1", user_data)
        self.assertNotIn("MLFLOW_TRACKING_URI", user_data)
        self.assertNotIn("MLFLOW_EXPERIMENT_NAME", user_data)
        self.assertNotIn("export MLFLOW_TRACKING_URI", REC_SCRIPT)
        self.assertNotIn("start_dashboards.sh", user_data)
        self.assertNotIn("RP_TB_LOGDIR", user_data)

    def test_build_sandbox_backend_accepts_lambda_labs_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RESEARCH_PLUGIN_EXECUTION_BACKEND": "lambda_labs",
                "LAMBDA_LABS_API_KEY": "test-key",
                "RESEARCH_PLUGIN_LAMBDA_REGION": "us-east-1",
                "RESEARCH_PLUGIN_LAMBDA_INSTANCE_TYPE": "gpu_8x_h100_sxm5",
            },
            clear=True,
        ):
            backend = build_sandbox_backend(repo_root=Path("/tmp/merv-test"))

        self.assertEqual(backend.capabilities.name, "lambda_labs")

    def test_default_backend_is_lambda_labs(self) -> None:
        # No name arg and no RESEARCH_PLUGIN_EXECUTION_BACKEND -> Lambda Labs.
        # Construction is lazy, so this must not resolve credentials yet.
        with patch.dict(os.environ, {}, clear=True):
            backend = build_sandbox_backend(repo_root=Path("/tmp/merv-test"))
        self.assertEqual(backend.capabilities.name, "lambda_labs")
        self.assertTrue(backend.capabilities.requires_hardware_selection)
        self.assertFalse(backend.capabilities.configurable_resources)

    def test_lambda_config_optional_region_and_instance_type(self) -> None:
        # Only an API key present: config resolves with empty region/instance
        # type (the agent picks per request) instead of raising.
        with patch.dict(os.environ, {"LAMBDA_LABS_API_KEY": "k"}, clear=True):
            config = LambdaSandboxConfig.from_env()
        self.assertEqual(config.region_name, "")
        self.assertEqual(config.instance_type_name, "")


class LambdaSelectionTest(unittest.TestCase):
    def _backend(self, **config_kwargs) -> LambdaLabsSandboxBackend:
        client = FakeLambdaSandboxClient()
        config = LambdaSandboxConfig(
            cloud=LambdaCloudConfig(api_key="test-key"),
            poll_interval_seconds=0.001,
            poll_timeout_seconds=1,
            **config_kwargs,
        )
        return LambdaLabsSandboxBackend(config=config, client=client), client  # type: ignore[return-value]

    def test_hardware_catalog_lists_available_cheapest_first(self) -> None:
        backend, _ = self._backend()
        catalog = backend.hardware_catalog()
        self.assertEqual(catalog["provider"], "lambda_labs")
        self.assertTrue(catalog["selection_required"])
        self.assertEqual(catalog["select_with"], "instance_type")
        names = [opt["instance_type"] for opt in catalog["options"]]
        # gpu_8x_a100 has no capacity -> excluded; a10 is cheaper than h100 -> first.
        self.assertEqual(names, ["gpu_1x_a10", "gpu_8x_h100_sxm5"])
        a10 = catalog["options"][0]
        self.assertEqual(a10["gpu"], "A10")
        self.assertEqual(a10["gpu_count"], 1)
        self.assertEqual(a10["vcpus"], 30)
        self.assertEqual(a10["regions"], ["us-west-1"])

    def test_hardware_catalog_filters_by_gpu(self) -> None:
        backend, _ = self._backend()
        catalog = backend.hardware_catalog(gpu="h100")
        names = [opt["instance_type"] for opt in catalog["options"]]
        self.assertEqual(names, ["gpu_8x_h100_sxm5"])

    def test_liveness_keeps_booting_billable_but_not_terminated(self) -> None:
        backend, client = self._backend()
        self.assertTrue(backend.is_alive(sandbox_id="inst_1"))
        with patch.object(
            client,
            "get_instance",
            return_value={"id": "inst_1", "status": "terminated"},
        ):
            self.assertFalse(backend.is_alive(sandbox_id="inst_1"))

    def test_acquire_uses_request_instance_type_and_autopicks_region(self) -> None:
        backend, client = self._backend()  # no configured region/instance type
        request = SandboxRequest(
            experiment_id="exp1",
            project_id="proj1",
            public_key="ssh-ed25519 AAAA test",
            instance_type="gpu_1x_a10",
        )
        with patch("socket.create_connection", fake_socket_connection):
            provisioned = backend.acquire(request=request)
        launch = client.launches[0]
        self.assertEqual(launch["instance_type_name"], "gpu_1x_a10")
        self.assertEqual(
            launch["region_name"], "us-west-1"
        )  # only region with capacity
        # The backend reports the SKU's real reserved hardware back to the registry.
        self.assertEqual(provisioned.instance_type, "gpu_1x_a10")
        self.assertEqual(provisioned.region, "us-west-1")
        self.assertEqual(provisioned.gpu, "A10")
        self.assertEqual(provisioned.cpu, 30.0)
        self.assertEqual(provisioned.memory, 200 * 1024)

    def test_request_instance_type_overrides_config_default(self) -> None:
        backend, client = self._backend(
            region_name="us-east-1", instance_type_name="gpu_8x_h100_sxm5"
        )
        request = SandboxRequest(
            experiment_id="exp1",
            project_id="proj1",
            public_key="ssh-ed25519 AAAA test",
            instance_type="gpu_1x_a10",
        )
        with patch("socket.create_connection", fake_socket_connection):
            backend.acquire(request=request)
        self.assertEqual(client.launches[0]["instance_type_name"], "gpu_1x_a10")
        self.assertEqual(client.launches[0]["region_name"], "us-west-1")

    def test_acquire_without_any_instance_type_raises(self) -> None:
        backend, client = self._backend()
        with self.assertRaises(BackendValidationError):
            backend.acquire(
                request=SandboxRequest(
                    experiment_id="exp1", project_id="proj1", public_key="k"
                )
            )
        self.assertEqual(client.launches, [])

    def test_acquire_unknown_instance_type_raises_with_offered_list(self) -> None:
        backend, _ = self._backend()
        with self.assertRaises(BackendValidationError) as ctx:
            backend.acquire(
                request=SandboxRequest(
                    experiment_id="exp1",
                    project_id="proj1",
                    public_key="k",
                    instance_type="gpu_99x_imaginary",
                )
            )
        self.assertIn("not currently offered", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
