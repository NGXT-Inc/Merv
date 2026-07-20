# Sandbox compute providers

The sandbox module provisions one SSH-reachable runtime per request through a
provider-neutral `SandboxBackend` port. One provider is configured with
`MERV_EXECUTION_BACKEND` (default `lambda_labs`); a fleet is
configured with `MERV_EXECUTION_BACKENDS` (comma-separated), which
wires every named backend behind one multiplexer:

- agents pick a provider per request via `sandbox.request(provider=...)`
  (omit for the default; `sandbox.options` tags every hardware option with the
  provider that serves it);
- sandbox ids are stored as `<provider>:<native_id>` so every later operation
  (liveness, terminate, transcript reads) is routed to the owning provider —
  pre-multiplexer rows keep their un-prefixed ids and route to the default;
- rows and the `sandbox_generations` spend ledger record the owning provider
  (empty = created before multi-provider support = the default backend).

Removing a provider from the config while its VMs still exist makes their ids
unroutable: operations on them fail loudly instead of guessing (a wrong
provider answering "not found" would strand a billing VM behind a terminated
row). Terminate a provider's sandboxes before dropping it from the list.

All VM providers share the same bootstrap outcome: authorize the caller's
public key and the control plane's management key, install the `rec.sh`
transcript wrapper + `merv_run`, and then install the heavy ML toolchain in a
second phase. Each isolated driver chooses cloud-init or a post-create SSH
bootstrap according to its provider API. Secrets (HF_TOKEN) are pushed
post-boot over the management SSH channel, never embedded in provider
user_data.

## Driver platform

Provider composition is registry-driven. `SandboxDriver` is the small stable
contract for capabilities, hardware discovery, acquire, liveness, endpoint
refresh, and termination. It exposes a `SandboxManagementTransport` for the
operational paths that read transcripts, usage metrics, and `merv_run`
receipts or deliver post-boot secrets. `SandboxBackend` remains the flattened
compatibility facade consumed by existing services.

`sandbox/execution/driver_registry.py` holds lightweight descriptors and a
runtime inventory exposed by `sandbox_driver_inventory()`. Descriptors contain
an import string rather than an imported factory, so listing providers does not
load their configuration, credentials, implementation modules, or optional
SDKs. Composition imports and builds only the selected providers. Aliases,
provider kind, and management-transport kind are registered alongside the
factory; there is no provider-name dispatch chain in the factory or services.

The two real driver shapes stay explicit:

- VM drivers share `VmSshSandboxBackend`; management operations use the
  control-owned management SSH principal.
- Modal is a `managed_container` driver with a provider-exec management
  transport. It does not inherit the VM base and its composable GPU/CPU/memory
  catalog is not forced into fixed VM SKUs.

To add a provider, implement its isolated package under
`sandbox/execution/backends/<provider>/`, expose one lazy builder, register one
`SandboxDriverDescriptor`, and run the shared surface/catalog conformance
assertions plus provider-specific fake-client lifecycle tests. The reusable
offline lifecycle/management scenario can be adopted by supplying its fixture
hooks; the in-memory driver exercises that full scenario. The descriptor name,
backend capability name, persisted provider value, and multiplexed id prefix
must agree.

During the service migration, registered builders return a
`SandboxBackend`-compatible facade even though the formal driver and management
transport contracts are smaller. `SandboxBackendBase` supplies the default
self-transport adapter, so an existing provider can adopt the driver platform
without changing its operational behavior; a future service migration can
remove that compatibility requirement from registry factories.

| Driver | Kind | Management transport | Aliases |
|---|---|---|---|
| `lambda_labs` | VM | management SSH | `lambda`, `lambdalabs` |
| `thunder_compute` | VM | management SSH | `thunder`, `thundercompute` |
| `modal` | managed container | provider exec | — |
| `hyperstack` | VM | management SSH | — |
| `digitalocean` | VM | management SSH | — |
| `verda` | VM | management SSH | `datacrunch` |
| `voltage_park` | VM | management SSH | `voltagepark` |
| `tensordock` | VM | management SSH | — |

The in-memory `fake` driver is also registered for deterministic tests, but is
not a production provider.

## Lambda Labs (`lambda_labs`)

- Env: `MERV_LAMBDA_API_KEY` (or `LAMBDA_LABS_API_KEY` /
  `LAMBDA_API_KEY`); optional `MERV_LAMBDA_REGION`,
  `MERV_LAMBDA_INSTANCE_TYPE`.
- Credentials: <https://cloud.lambda.ai> -> API keys -> Generate. Pay-as-you-go
  with a card on file.
- Quirks: fixed machine SKUs (`gpu_1x_a10`, ...); live capacity via the
  instance-types API; per-minute billing. Deep stock of A10/A100/H100.

## Thunder Compute (`thunder_compute`)

- Env: `MERV_THUNDER_API_KEY` (or `THUNDER_COMPUTE_API_KEY` /
  `TNR_API_TOKEN`).
- Quirks: virtualized GPUs behind a port-forwarded SSH endpoint; the bootstrap
  is pushed over SSH rather than user_data. Cheap A100 capacity; per-minute
  billing; prototyping-mode instances can be slow for sustained training.

## Hyperstack (`hyperstack`)

- Env: `MERV_HYPERSTACK_API_KEY` (or `HYPERSTACK_API_KEY`) and
  `MERV_HYPERSTACK_ENVIRONMENT`; optional
  `MERV_HYPERSTACK_IMAGE` (default
  `Ubuntu Server 24.04 LTS (Noble Numbat)`), `MERV_HYPERSTACK_FLAVOR`.
- Credentials: sign up at <https://console.hyperstack.cloud>, add credit
  (prepaid balance or card), then Settings -> API Keys -> Generate. Create an
  **environment** once in the console (it pins the region) and put its name in
  `MERV_HYPERSTACK_ENVIRONMENT`.
- Quirks: VMs are secure-by-default with ZERO inbound ports — the backend
  attaches an inline TCP-22 ingress rule at create, or SSH never answers.
  Flavors carry `stock_available`; prices come from the account pricebook.
  `SHUTOFF` VMs still bill (only delete stops charges). Per-minute billing.
  Login user is `ubuntu`.

## DigitalOcean GPU Droplets (`digitalocean`)

- Env: `MERV_DIGITALOCEAN_TOKEN` (or `DIGITALOCEAN_TOKEN` /
  `DIGITALOCEAN_ACCESS_TOKEN`); optional `MERV_DIGITALOCEAN_IMAGE`
  (default `gpu-h100x1-base`, the AI/ML-ready Ubuntu with NVIDIA drivers),
  `MERV_DIGITALOCEAN_REGION`.
- Credentials: <https://cloud.digitalocean.com> -> API -> Tokens -> Generate
  New Token (full access). GPU sizes stay HIDDEN until the account gets the
  one-time GPU unlock — request it in the console under Create -> GPU Droplets.
- Quirks: powered-off droplets still bill (destroy is the only stop); root SSH
  and public IPv4 are the default; user_data caps at 64 KiB; no A100 SKUs
  (H100/H200/L40S/RTX-Ada fleet). Per-hour billing (hourly cap = monthly rate).

## Verda, formerly DataCrunch (`verda`, alias `datacrunch`)

- Env: `MERV_VERDA_CLIENT_ID` + `MERV_VERDA_CLIENT_SECRET`
  (or `DATACRUNCH_CLIENT_ID`/`DATACRUNCH_CLIENT_SECRET`); optional
  `MERV_VERDA_IMAGE` (default `ubuntu-24.04`),
  `MERV_VERDA_LOCATION` (e.g. `FIN-01`).
- Credentials: <https://cloud.datacrunch.io> (redirects to the verda.com
  console as the rename lands) -> Keys -> REST API credentials -> Generate:
  an OAuth2 client id + secret pair. Prepaid balance or card.
- Quirks: OAuth2 client-credentials (the backend mints and refreshes tokens);
  SSH keys AND the bootstrap startup script are pre-registered account
  resources referenced by id; billing rounds UP to 10-minute increments;
  `offline` instances keep billing their OS volume. The API base is pinned to
  `api.datacrunch.io` while the verda.com host migration is in flight
  (`MERV_VERDA_API_BASE` overrides).

## Voltage Park (`voltage_park`)

- Env: `MERV_VOLTAGE_PARK_TOKEN` (or `VOLTAGE_PARK_TOKEN`).
- Credentials: <https://dashboard.voltagepark.com> -> account/developer
  settings -> API token (Bearer).
- Quirks: H100-SXM5-only on-demand fleet sold as instant-deploy PRESETS — the
  preset uuid is the `instance_type`; SSH public keys are passed raw per
  deploy; the bootstrap rides as structured cloud-init (b64 `write_files` +
  `runcmd`). `Stopped`/`StoppedDisassociated` VMs still hold storage.
  NEEDS LIVE SMOKE TEST: whether bare port 22 answers on the public IP — the
  backend assumes it does and automatically switches to a port forward
  mapping internal 22 when the VM reports one.

## TensorDock (`tensordock`)

- Env: `MERV_TENSORDOCK_TOKEN` (or `TENSORDOCK_TOKEN`); optional
  `MERV_TENSORDOCK_IMAGE` (default `ubuntu2404`).
- Credentials: <https://dashboard.tensordock.com> -> Developer Settings ->
  Generate API token (Bearer). Prepaid balance required (minimum $1 to
  deploy).
- Quirks: a marketplace of third-party hosts; machines are composed, so the
  catalog synthesizes `<count>x-<gpu>` shapes with default vCPU/RAM and the
  100 GB storage minimum. Only locations with `dedicated_ip_available` are
  offered — port-mapped hosts cannot serve direct SSH. Per-second billing
  against the prepaid balance; there is no billing API, so the provision-time
  quote is the recorded rate. Host quality varies by uptime tier.
