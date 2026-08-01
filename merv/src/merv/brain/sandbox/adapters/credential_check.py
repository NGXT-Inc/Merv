# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""One cheap authenticated call per provider: does this credential work?

The setup wizard's final gate. Each check takes the collected field values
(keyed by canonical ``MERV_*`` names, the same vocabulary the catalog and the
saved-credential store use), performs the smallest read the provider's API
offers, and returns a one-line human detail. Every failure — wrong key, no
network, missing optional SDK — raises kernel ``ValidationError`` with a
reason a person can act on; nothing else escapes. Checks prove ACCESS, not
quota: a key can pass here and still hit a zero-GPU quota at provision time.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ...kernel.utils import ValidationError

_TIMEOUT_SECONDS = 20.0
_USER_AGENT = "merv-credential-check"


def _call(
    *,
    provider: str,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> dict:
    request = Request(  # noqa: S310 - fixed https provider endpoints
        url,
        data=body,
        method=method,
        headers={"User-Agent": _USER_AGENT, **(headers or {})},
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ValidationError(
                f"{provider} rejected the credential (HTTP {exc.code}) — "
                "re-check the value you pasted"
            ) from exc
        raise ValidationError(
            f"{provider} API answered HTTP {exc.code}; the credential could "
            "not be confirmed"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(f"{provider} API is unreachable: {exc}") from exc
    try:
        parsed = json.loads(payload) if payload.strip() else {}
    except ValueError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _need(values: Mapping[str, str], key: str, label: str) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise ValidationError(f"{label} is required before verification")
    return value


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def check_lambda_labs(values: Mapping[str, str]) -> str:
    key = _need(values, "MERV_LAMBDA_API_KEY", "API key")
    _call(
        provider="Lambda Labs",
        method="GET",
        url="https://cloud.lambda.ai/api/v1/instance-types",
        headers=_bearer(key),
    )
    return "API key accepted by Lambda Labs"


def check_thunder_compute(values: Mapping[str, str]) -> str:
    key = _need(values, "MERV_THUNDER_API_KEY", "API token")
    _call(
        provider="Thunder Compute",
        method="GET",
        url="https://api.thundercompute.com:8443/v1/instances/list",
        headers=_bearer(key),
    )
    return "API token accepted by Thunder Compute"


def check_hyperstack(values: Mapping[str, str]) -> str:
    key = _need(values, "MERV_HYPERSTACK_API_KEY", "API key")
    _need(values, "MERV_HYPERSTACK_ENVIRONMENT", "Environment")
    _call(
        provider="Hyperstack",
        method="GET",
        url="https://infrahub-api.nexgencloud.com/v1/core/flavors",
        headers={"api_key": key, "Accept": "application/json"},
    )
    # The environment name is account data, not credential data; flavors
    # answering proves the key. A wrong environment fails at provision with
    # its own explicit error.
    return "API key accepted by Hyperstack"


def check_digitalocean(values: Mapping[str, str]) -> str:
    token = _need(values, "MERV_DIGITALOCEAN_TOKEN", "Access token")
    data = _call(
        provider="DigitalOcean",
        method="GET",
        url="https://api.digitalocean.com/v2/account",
        headers=_bearer(token),
    )
    email = str((data.get("account") or {}).get("email") or "")
    return f"token accepted for {email}" if email else "token accepted"


def check_verda(values: Mapping[str, str]) -> str:
    client_id = _need(values, "MERV_VERDA_CLIENT_ID", "Client ID")
    secret = _need(values, "MERV_VERDA_CLIENT_SECRET", "Client secret")
    data = _call(
        provider="Verda",
        method="POST",
        url="https://api.datacrunch.io/v1/oauth2/token",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            }
        ).encode("utf-8"),
    )
    if not data.get("access_token"):
        raise ValidationError("Verda accepted the call but returned no token")
    return "OAuth2 client credentials accepted by Verda"


def check_voltage_park(values: Mapping[str, str]) -> str:
    token = _need(values, "MERV_VOLTAGE_PARK_TOKEN", "API token")
    _call(
        provider="Voltage Park",
        method="GET",
        url="https://cloud-api.voltagepark.com/api/v1/virtual-machines/",
        headers=_bearer(token),
    )
    return "API token accepted by Voltage Park"


def check_tensordock(values: Mapping[str, str]) -> str:
    token = _need(values, "MERV_TENSORDOCK_TOKEN", "API token")
    _call(
        provider="TensorDock",
        method="GET",
        url="https://dashboard.tensordock.com/api/v2/instances",
        headers=_bearer(token),
    )
    return "API token accepted by TensorDock"


def check_aws(values: Mapping[str, str]) -> str:
    access_key = _need(values, "MERV_AWS_ACCESS_KEY_ID", "Access key ID")
    secret = _need(values, "MERV_AWS_SECRET_ACCESS_KEY", "Secret access key")
    try:
        import boto3  # noqa: PLC0415 - lazy, mirrors the adapter
        from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415
    except ImportError as exc:
        raise ValidationError(
            "boto3 is not installed on this deployment "
            "(pip install 'merv[aws]') so AWS keys cannot be verified"
        ) from exc
    try:
        identity = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret,
            aws_session_token=(values.get("MERV_AWS_SESSION_TOKEN") or None),
            region_name=(values.get("MERV_AWS_REGION") or "us-east-1"),
        ).get_caller_identity()
    except (ClientError, BotoCoreError) as exc:
        raise ValidationError(f"AWS rejected the key pair: {exc}") from exc
    return f"authenticated as AWS account {identity.get('Account', 'unknown')}"


def check_gcp(values: Mapping[str, str]) -> str:
    project = _need(values, "MERV_GCP_PROJECT", "Project ID")
    raw_json = _need(
        values, "MERV_GCP_SERVICE_ACCOUNT_JSON", "Service account key (JSON)"
    )
    try:
        info = json.loads(raw_json)
    except ValueError as exc:
        raise ValidationError(
            "the service account key is not valid JSON — paste the whole "
            "downloaded key file"
        ) from exc
    try:
        from google.oauth2 import service_account  # noqa: PLC0415 - lazy
        from google.auth.transport.requests import Request as GARequest  # noqa: PLC0415
    except ImportError as exc:
        raise ValidationError(
            "google-auth is not installed on this deployment "
            "(pip install 'merv[gcp]') so GCP keys cannot be verified"
        ) from exc
    try:
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(GARequest())
    except Exception as exc:  # noqa: BLE001 - every auth failure reads the same way
        raise ValidationError(f"GCP rejected the service account key: {exc}") from exc
    _call(
        provider="GCP",
        method="GET",
        url=f"https://compute.googleapis.com/compute/v1/projects/{project}",
        headers=_bearer(str(credentials.token or "")),
    )
    return f"service account authenticated against project {project}"


def check_azure(values: Mapping[str, str]) -> str:
    tenant = _need(values, "MERV_AZURE_TENANT_ID", "Tenant ID")
    client_id = _need(values, "MERV_AZURE_CLIENT_ID", "Client ID")
    secret = _need(values, "MERV_AZURE_CLIENT_SECRET", "Client secret")
    subscription = _need(values, "MERV_AZURE_SUBSCRIPTION_ID", "Subscription ID")
    data = _call(
        provider="Azure",
        method="POST",
        url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
                "scope": "https://management.azure.com/.default",
            }
        ).encode("utf-8"),
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise ValidationError("Azure accepted the call but returned no token")
    _call(
        provider="Azure",
        method="GET",
        url=(
            "https://management.azure.com/subscriptions/"
            f"{subscription}?api-version=2022-12-01"
        ),
        headers=_bearer(token),
    )
    return "service principal authenticated; subscription visible"


CREDENTIAL_CHECKS = {
    "lambda_labs": check_lambda_labs,
    "thunder_compute": check_thunder_compute,
    "hyperstack": check_hyperstack,
    "digitalocean": check_digitalocean,
    "verda": check_verda,
    "voltage_park": check_voltage_park,
    "tensordock": check_tensordock,
    "aws": check_aws,
    "gcp": check_gcp,
    "azure": check_azure,
}


__all__ = ["CREDENTIAL_CHECKS"]
