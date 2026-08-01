# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Connectable-provider catalog: which clouds exist and what connecting takes.

One entry per user-connectable compute provider (Modal is excluded: it is a
managed-container runtime configured by its own CLI login, not a paste-a-key
cloud). Each field is keyed by the provider's canonical ``MERV_*`` env name so
saved credentials and environment configuration stay one vocabulary; ``alt_env``
carries the vendor spellings the adapters also accept, used only to detect
"already configured via environment". Every field carries ``help`` — the one
sentence the setup wizard shows while collecting exactly that value. Secret
values saved through the API are WRITE-ONLY (set/replaced/cleared, never
returned); detection here reads env presence, not validity — validity is
``credential_check``'s job.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from ...kernel.env import env_value


@dataclass(frozen=True, slots=True)
class ProviderField:
    """One credential/config input a provider's connection wizard collects."""

    key: str  # canonical MERV_* env spelling; also the saved-credential key
    label: str
    help: str = ""  # wizard guidance: where this value lives and how to get it
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    multiline: bool = False
    alt_env: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """A connectable provider: identity, console pointer, and its fields."""

    name: str  # canonical driver name (adapters registry spelling)
    label: str
    console_url: str
    note: str  # one line: where the credential comes from
    fields: tuple[ProviderField, ...]
    # True when this deployment ships shared platform credentials for the
    # provider by default (the operator can widen/narrow the set with
    # MERV_PLATFORM_PROVIDERS). Only Lambda Labs ships them today.
    platform_default: bool = False

    def required_fields(self) -> tuple[ProviderField, ...]:
        return tuple(field for field in self.fields if field.required)

    def field(self, key: str) -> ProviderField | None:
        for candidate in self.fields:
            if candidate.key == key:
                return candidate
        return None

    def env_configured(self, *, env: Mapping[str, str] | None = None) -> bool:
        """Whether the process environment already satisfies every required
        field (canonical or vendor spelling). Best-effort presence check —
        ambient chains (boto3 profiles, GCP ADC) configure without env vars
        and are invisible here."""
        return all(
            _env_present(field, env=env) for field in self.required_fields()
        )

    def env_values(self, *, env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Field values as the environment provides them (canonical spelling
        first, vendor spellings after), for verification of env/platform
        credentials. Only present values appear."""
        values: dict[str, str] = {}
        for field in self.fields:
            found = env_value(field.key, env=env) or next(
                (
                    value
                    for name in field.alt_env
                    if (value := env_value(name, env=env))
                ),
                None,
            )
            if found:
                values[field.key] = found
        return values


def _env_present(field: ProviderField, *, env: Mapping[str, str] | None) -> bool:
    if env_value(field.key, env=env):
        return True
    return any(env_value(name, env=env) for name in field.alt_env)


CONNECTABLE_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="lambda_labs",
        label="Lambda Labs",
        console_url="https://cloud.lambda.ai",
        note="API keys → Generate; pay-as-you-go with a card on file.",
        platform_default=True,
        fields=(
            ProviderField(
                key="MERV_LAMBDA_API_KEY",
                label="API key",
                help="In the Lambda console, open API keys in the left rail "
                "and click Generate API key. Copy the key it shows once.",
                secret=True,
                alt_env=("LAMBDA_LABS_API_KEY", "LAMBDA_API_KEY"),
            ),
            ProviderField(
                key="MERV_LAMBDA_REGION",
                label="Region",
                help="Optional: pin instances to one region (for example "
                "us-east-1). Leave blank to let capacity decide.",
                required=False,
                placeholder="us-east-1",
            ),
        ),
    ),
    ProviderSpec(
        name="thunder_compute",
        label="Thunder Compute",
        console_url="https://console.thundercompute.com",
        note="Account settings → API token.",
        fields=(
            ProviderField(
                key="MERV_THUNDER_API_KEY",
                label="API token",
                help="In the Thunder Compute console, open Account settings "
                "and create an API token; copy it here.",
                secret=True,
                alt_env=("THUNDER_COMPUTE_API_KEY", "TNR_API_TOKEN"),
            ),
        ),
    ),
    ProviderSpec(
        name="hyperstack",
        label="Hyperstack",
        console_url="https://console.hyperstack.cloud",
        note="Settings → API Keys; the environment (created once in the "
        "console) pins the region.",
        fields=(
            ProviderField(
                key="MERV_HYPERSTACK_API_KEY",
                label="API key",
                help="In the Hyperstack console, go to Settings → API Keys "
                "and generate a key. Add prepaid credit or a card first — "
                "keys on unfunded accounts cannot deploy.",
                secret=True,
                alt_env=("HYPERSTACK_API_KEY",),
            ),
            ProviderField(
                key="MERV_HYPERSTACK_ENVIRONMENT",
                label="Environment",
                help="Hyperstack groups VMs into named environments that pin "
                "a region. Create one under Environments (once) and paste "
                "its exact name.",
                placeholder="default-CANADA-1",
            ),
        ),
    ),
    ProviderSpec(
        name="digitalocean",
        label="DigitalOcean",
        console_url="https://cloud.digitalocean.com",
        note="API → Tokens → Generate New Token (full access); GPU sizes "
        "need the one-time GPU unlock.",
        fields=(
            ProviderField(
                key="MERV_DIGITALOCEAN_TOKEN",
                label="Access token",
                help="In the DigitalOcean control panel, open API → Tokens "
                "and generate a token with full access (read + write). GPU "
                "droplets also need the one-time GPU unlock under Create → "
                "GPU Droplets.",
                secret=True,
                alt_env=("DIGITALOCEAN_TOKEN", "DIGITALOCEAN_ACCESS_TOKEN"),
            ),
            ProviderField(
                key="MERV_DIGITALOCEAN_REGION",
                label="Region",
                help="Optional: pin droplets to one region slug (for example "
                "nyc2 or tor1). Leave blank to let capacity decide.",
                required=False,
                placeholder="nyc2",
            ),
        ),
    ),
    ProviderSpec(
        name="verda",
        label="Verda (DataCrunch)",
        console_url="https://cloud.datacrunch.io",
        note="Keys → REST API credentials → Generate: an OAuth2 client id + "
        "secret pair.",
        fields=(
            ProviderField(
                key="MERV_VERDA_CLIENT_ID",
                label="Client ID",
                help="In the Verda (DataCrunch) console, open Keys → REST "
                "API credentials → Generate. It creates a client id and "
                "secret pair; this is the id half.",
                alt_env=("DATACRUNCH_CLIENT_ID",),
            ),
            ProviderField(
                key="MERV_VERDA_CLIENT_SECRET",
                label="Client secret",
                help="The secret half of the same REST API credential pair. "
                "It is shown once at generation time.",
                secret=True,
                alt_env=("DATACRUNCH_CLIENT_SECRET",),
            ),
            ProviderField(
                key="MERV_VERDA_LOCATION",
                label="Location",
                help="Optional: pin instances to one datacenter code (for "
                "example FIN-01). Leave blank to let capacity decide.",
                required=False,
                placeholder="FIN-01",
            ),
        ),
    ),
    ProviderSpec(
        name="voltage_park",
        label="Voltage Park",
        console_url="https://dashboard.voltagepark.com",
        note="Account → developer settings → API token.",
        fields=(
            ProviderField(
                key="MERV_VOLTAGE_PARK_TOKEN",
                label="API token",
                help="In the Voltage Park dashboard, open your account's "
                "developer settings and create an API token (Bearer).",
                secret=True,
                alt_env=("VOLTAGE_PARK_TOKEN",),
            ),
        ),
    ),
    ProviderSpec(
        name="tensordock",
        label="TensorDock",
        console_url="https://dashboard.tensordock.com",
        note="Developer Settings → Generate API token; prepaid balance "
        "required to deploy.",
        fields=(
            ProviderField(
                key="MERV_TENSORDOCK_TOKEN",
                label="API token",
                help="In the TensorDock dashboard, open Developer Settings "
                "and generate an API token. Deploys need at least $1 of "
                "prepaid balance on the account.",
                secret=True,
                alt_env=("TENSORDOCK_TOKEN",),
            ),
        ),
    ),
    ProviderSpec(
        name="aws",
        label="AWS EC2",
        console_url="https://console.aws.amazon.com/iam/",
        note="IAM access key pair with EC2 permissions; fresh accounts need "
        "the one-time G/P-family vCPU quota increase.",
        fields=(
            ProviderField(
                key="MERV_AWS_ACCESS_KEY_ID",
                label="Access key ID",
                help="In the AWS console, open IAM → Users → your user → "
                "Security credentials → Create access key. The user needs "
                "EC2 permissions (AmazonEC2FullAccess or a scoped-down "
                "policy).",
                alt_env=("AWS_ACCESS_KEY_ID",),
            ),
            ProviderField(
                key="MERV_AWS_SECRET_ACCESS_KEY",
                label="Secret access key",
                help="The secret half of the same access key, shown once at "
                "creation. If you lost it, create a new key pair.",
                secret=True,
                alt_env=("AWS_SECRET_ACCESS_KEY",),
            ),
            ProviderField(
                key="MERV_AWS_SESSION_TOKEN",
                label="Session token",
                help="Only for temporary (STS) credentials — leave blank for "
                "a normal IAM access key.",
                secret=True,
                required=False,
                alt_env=("AWS_SESSION_TOKEN",),
            ),
            ProviderField(
                key="MERV_AWS_REGION",
                label="Region",
                help="Optional: the region to launch in (default us-east-1). "
                "GPU capacity and quota are per-region.",
                required=False,
                placeholder="us-east-1",
                alt_env=("AWS_REGION", "AWS_DEFAULT_REGION"),
            ),
        ),
    ),
    ProviderSpec(
        name="gcp",
        label="Google Cloud",
        console_url="https://console.cloud.google.com/iam-admin/serviceaccounts",
        note="Service account with Compute Admin → JSON key; fresh projects "
        "need the one-time GPUS_ALL_REGIONS quota increase.",
        fields=(
            ProviderField(
                key="MERV_GCP_PROJECT",
                label="Project ID",
                help="The GCP project id (not the display name) — shown on "
                "the console dashboard, e.g. my-lab-472113.",
                alt_env=("GOOGLE_CLOUD_PROJECT",),
            ),
            ProviderField(
                key="MERV_GCP_SERVICE_ACCOUNT_JSON",
                label="Service account key (JSON)",
                help="In IAM & Admin → Service Accounts, create (or pick) an "
                "account with the Compute Admin role, then Keys → Add key → "
                "JSON. Paste the entire downloaded file here.",
                secret=True,
                multiline=True,
                alt_env=("GOOGLE_APPLICATION_CREDENTIALS",),
            ),
            ProviderField(
                key="MERV_GCP_ZONE",
                label="Zone",
                help="Optional: the zone to launch in (default us-central1-a). "
                "GPU machine types vary by zone.",
                required=False,
                placeholder="us-central1-a",
            ),
        ),
    ),
    ProviderSpec(
        name="azure",
        label="Microsoft Azure",
        console_url="https://portal.azure.com",
        note="az ad sp create-for-rbac --role Contributor prints the "
        "tenant/client/secret triple.",
        fields=(
            ProviderField(
                key="MERV_AZURE_TENANT_ID",
                label="Tenant ID",
                help="Run: az ad sp create-for-rbac --role Contributor "
                "--scopes /subscriptions/<your-subscription-id>. It prints "
                "tenant, appId (client) and password (secret); this is the "
                "tenant value. Also visible under Entra ID → Overview.",
                alt_env=("AZURE_TENANT_ID",),
            ),
            ProviderField(
                key="MERV_AZURE_CLIENT_ID",
                label="Client ID",
                help="The appId printed by the same az command (the service "
                "principal's application id).",
                alt_env=("AZURE_CLIENT_ID",),
            ),
            ProviderField(
                key="MERV_AZURE_CLIENT_SECRET",
                label="Client secret",
                help="The password printed by the same az command, shown "
                "once. Rotate it with az ad sp credential reset if lost.",
                secret=True,
                alt_env=("AZURE_CLIENT_SECRET",),
            ),
            ProviderField(
                key="MERV_AZURE_SUBSCRIPTION_ID",
                label="Subscription ID",
                help="The subscription the VMs bill to — az account show "
                "--query id, or Subscriptions in the portal.",
                alt_env=("AZURE_SUBSCRIPTION_ID",),
            ),
            ProviderField(
                key="MERV_AZURE_LOCATION",
                label="Location",
                help="Optional: the region to deploy in (default eastus). "
                "GPU quota is per-region.",
                required=False,
                placeholder="eastus",
            ),
        ),
    ),
)

CONNECTABLE_PROVIDERS_BY_NAME = {
    spec.name: spec for spec in CONNECTABLE_PROVIDERS
}


def connectable_provider(name: str) -> ProviderSpec | None:
    return CONNECTABLE_PROVIDERS_BY_NAME.get(name)


__all__ = [
    "CONNECTABLE_PROVIDERS",
    "CONNECTABLE_PROVIDERS_BY_NAME",
    "ProviderField",
    "ProviderSpec",
    "connectable_provider",
]
