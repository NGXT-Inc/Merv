from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from merv.brain.sandbox.execution.backends.digitalocean.config import (
    DEFAULT_BASE_URL as DIGITALOCEAN_BASE_URL,
)
from merv.brain.sandbox.execution.backends.digitalocean.config import (
    DigitalOceanCloudConfig,
)
from merv.brain.sandbox.execution.backends.hyperstack.config import (
    DEFAULT_BASE_URL as HYPERSTACK_BASE_URL,
)
from merv.brain.sandbox.execution.backends.hyperstack.config import (
    HyperstackCloudConfig,
)
from merv.brain.sandbox.execution.backends.lambda_labs.config import (
    DEFAULT_BASE_URL as LAMBDA_BASE_URL,
)
from merv.brain.sandbox.execution.backends.lambda_labs.config import LambdaCloudConfig
from merv.brain.sandbox.execution.backends.tensordock.config import (
    DEFAULT_BASE_URL as TENSORDOCK_BASE_URL,
)
from merv.brain.sandbox.execution.backends.tensordock.config import (
    TensorDockCloudConfig,
)
from merv.brain.sandbox.execution.backends.verda.config import (
    DEFAULT_BASE_URL as VERDA_BASE_URL,
)
from merv.brain.sandbox.execution.backends.verda.config import VerdaCloudConfig
from merv.brain.sandbox.execution.backends.voltage_park.config import (
    DEFAULT_BASE_URL as VOLTAGE_PARK_BASE_URL,
)
from merv.brain.sandbox.execution.backends.voltage_park.config import (
    VoltageParkCloudConfig,
)
from merv.brain.sandbox.sandbox_backend import BackendValidationError


class ProviderConfigValueTest(unittest.TestCase):
    CASES = (
        (
            DigitalOceanCloudConfig,
            "token",
            "MERV_DIGITALOCEAN_TOKEN",
            "DIGITALOCEAN_ACCESS_TOKEN",
            "MERV_DIGITALOCEAN_API_BASE",
            DIGITALOCEAN_BASE_URL,
            "DigitalOcean API token is required; set MERV_DIGITALOCEAN_TOKEN, "
            "DIGITALOCEAN_TOKEN, or DIGITALOCEAN_ACCESS_TOKEN",
        ),
        (
            HyperstackCloudConfig,
            "api_key",
            "MERV_HYPERSTACK_API_KEY",
            "HYPERSTACK_API_KEY",
            "MERV_HYPERSTACK_API_BASE",
            HYPERSTACK_BASE_URL,
            "Hyperstack API key is required; set MERV_HYPERSTACK_API_KEY or "
            "HYPERSTACK_API_KEY",
        ),
        (
            LambdaCloudConfig,
            "api_key",
            "MERV_LAMBDA_API_KEY",
            "LAMBDA_API_KEY",
            "MERV_LAMBDA_API_BASE",
            LAMBDA_BASE_URL,
            "Lambda Cloud API key is required; set MERV_LAMBDA_API_KEY, "
            "LAMBDA_LABS_API_KEY, or LAMBDA_API_KEY",
        ),
        (
            TensorDockCloudConfig,
            "token",
            "MERV_TENSORDOCK_TOKEN",
            "TENSORDOCK_TOKEN",
            "MERV_TENSORDOCK_API_BASE",
            TENSORDOCK_BASE_URL,
            "TensorDock API token is required; set MERV_TENSORDOCK_TOKEN or "
            "TENSORDOCK_TOKEN",
        ),
        (
            VoltageParkCloudConfig,
            "token",
            "MERV_VOLTAGE_PARK_TOKEN",
            "VOLTAGE_PARK_TOKEN",
            "MERV_VOLTAGE_PARK_API_BASE",
            VOLTAGE_PARK_BASE_URL,
            "Voltage Park API token is required; set MERV_VOLTAGE_PARK_TOKEN or "
            "VOLTAGE_PARK_TOKEN",
        ),
    )

    def test_single_credential_configs_preserve_precedence_and_urls(self) -> None:
        for (
            config_type,
            credential_field,
            primary_name,
            fallback_name,
            base_name,
            default_base,
            _error,
        ) in self.CASES:
            with self.subTest(provider=config_type.__name__, source="fallback"):
                with patch.dict(
                    os.environ, {fallback_name: " fallback-token "}, clear=True
                ):
                    config = config_type.from_env()
                self.assertEqual(getattr(config, credential_field), "fallback-token")
                self.assertEqual(config.base_url, default_base)
            with self.subTest(provider=config_type.__name__, source="primary"):
                with patch.dict(
                    os.environ,
                    {
                        primary_name: " primary-token ",
                        fallback_name: "fallback-token",
                        base_name: " https://provider.test/root/ ",
                    },
                    clear=True,
                ):
                    config = config_type.from_env()
                self.assertEqual(getattr(config, credential_field), "primary-token")
                self.assertEqual(config.base_url, "https://provider.test/root")

    def test_single_credential_configs_preserve_exact_errors(self) -> None:
        for (
            config_type,
            _credential_field,
            primary_name,
            _fallback_name,
            base_name,
            _default_base,
            error,
        ) in self.CASES:
            with self.subTest(provider=config_type.__name__, failure="credential"):
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(BackendValidationError) as raised:
                        config_type.from_env()
                self.assertEqual(str(raised.exception), error)
            with self.subTest(provider=config_type.__name__, failure="base_url"):
                with patch.dict(
                    os.environ,
                    {primary_name: "token", base_name: "ftp://provider.test"},
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        BackendValidationError,
                        f"^{base_name} must be an HTTP URL$",
                    ):
                        config_type.from_env()

    def test_verda_preserves_two_credentials_and_shared_url_rules(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATACRUNCH_CLIENT_ID": " client ",
                "DATACRUNCH_CLIENT_SECRET": " secret ",
            },
            clear=True,
        ):
            defaulted = VerdaCloudConfig.from_env()
        self.assertEqual(defaulted.client_id, "client")
        self.assertEqual(defaulted.client_secret, "secret")
        self.assertEqual(defaulted.base_url, VERDA_BASE_URL)

        with patch.dict(
            os.environ,
            {
                "MERV_VERDA_CLIENT_ID": "primary-client",
                "DATACRUNCH_CLIENT_ID": "fallback-client",
                "MERV_VERDA_CLIENT_SECRET": "primary-secret",
                "DATACRUNCH_CLIENT_SECRET": "fallback-secret",
                "MERV_VERDA_API_BASE": " http://provider.test/root/ ",
            },
            clear=True,
        ):
            configured = VerdaCloudConfig.from_env()
        self.assertEqual(configured.client_id, "primary-client")
        self.assertEqual(configured.client_secret, "primary-secret")
        self.assertEqual(configured.base_url, "http://provider.test/root")

    def test_verda_preserves_joint_credential_and_url_errors(self) -> None:
        error = (
            "Verda OAuth2 credentials are required; set MERV_VERDA_CLIENT_ID and "
            "MERV_VERDA_CLIENT_SECRET (DATACRUNCH_* variants also accepted)"
        )
        for env in (
            {"MERV_VERDA_CLIENT_ID": "client"},
            {"MERV_VERDA_CLIENT_SECRET": "secret"},
        ):
            with self.subTest(env=env):
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(BackendValidationError) as raised:
                        VerdaCloudConfig.from_env()
                self.assertEqual(str(raised.exception), error)

        with patch.dict(
            os.environ,
            {
                "MERV_VERDA_CLIENT_ID": "client",
                "MERV_VERDA_CLIENT_SECRET": "secret",
                "MERV_VERDA_API_BASE": "file:///tmp/provider",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                BackendValidationError,
                "^MERV_VERDA_API_BASE must be an HTTP URL$",
            ):
                VerdaCloudConfig.from_env()


if __name__ == "__main__":
    unittest.main()
