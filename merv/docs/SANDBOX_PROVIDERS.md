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
provider-neutral capability consumed by `SandboxEngine`.

`sandbox/adapters/__init__.py` holds lightweight descriptors and a
runtime inventory exposed by `sandbox_driver_inventory()`. Descriptors contain
an import string rather than an imported factory, so listing providers does not
load their configuration, credentials, implementation modules, or optional
SDKs. Composition imports and builds only the selected providers. Aliases and
builders live in the registry; services contain no provider-name dispatch
chain.

The two real driver shapes stay explicit:

- VM drivers share `VmSshSandboxBackend`; management operations use the
  control-owned management SSH principal.
- Modal is a `managed_container` driver with a provider-exec management
  transport. It does not inherit the VM base and its composable GPU/CPU/memory
  catalog is not forced into fixed VM SKUs.

To add a provider, implement one explicit
`sandbox/adapters/<provider>.py` file, expose one lazy builder, register one
`SandboxDriverDescriptor`, and run the shared surface/catalog conformance
assertions plus provider-specific fake-client lifecycle tests. The reusable
offline lifecycle/management scenario can be adopted by supplying its fixture
hooks; the in-memory driver exercises that full scenario. The descriptor name,
backend capability name, persisted provider value, and multiplexed id prefix
must agree.

Registered builders return the one provider-neutral `SandboxBackend` contract.
`SandboxBackendBase` supplies harmless defaults for optional observations, so a
provider file contains only behavior it actually supports.

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
| `aws` | VM | management SSH | `ec2`, `amazon` |
| `gcp` | VM | management SSH | `gce`, `google`, `google_cloud` |
| `azure` | VM | management SSH | `az`, `microsoft_azure` |

Tests inject an in-memory fake through the sandbox port; it is intentionally
absent from the production provider registry.

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

## AWS EC2 (`aws`, aliases `ec2`, `amazon`)

- Env: standard AWS credentials — `AWS_ACCESS_KEY_ID` +
  `AWS_SECRET_ACCESS_KEY` (or `MERV_AWS_*` variants), optional
  `AWS_SESSION_TOKEN`; `MERV_AWS_REGION` (default `us-east-1`); optional
  `MERV_AWS_IMAGE_ID`, `MERV_AWS_INSTANCE_TYPE`, `MERV_AWS_VOLUME_GIB`
  (default 200). With no keys set, boto3's default chain (shared credentials
  file, SSO, instance profile) applies, so a brain hosted inside AWS needs no
  long-lived secret.
- Deps: `boto3` (`pip install 'merv[aws]'`; already present in the `control`
  extra). Imported lazily — only when the driver is selected.
- Credentials: IAM user or role with EC2 permissions (`AmazonEC2FullAccess`
  scoped down as desired) -> access key pair.
- Quirks: user_data caps at 16 KiB, which our bootstrap brushes against, so
  the bootstrap is pushed over management SSH after boot (Thunder-style) —
  the management key is the EC2 key pair. Default image resolves to the
  newest Deep Learning Base GPU AMI (NVIDIA drivers preinstalled). A shared
  `merv-sandbox-ssh` security group (inbound 22 only) is created once per
  account. The EC2 API exposes no prices; options stay unknown-price. Fresh
  accounts have a ZERO vCPU quota for G/P families — request the increase
  once under Service Quotas > Amazon EC2. Stopped instances still bill EBS;
  terminate is the only stop.
  NEEDS LIVE SMOKE TEST: DLAMI SSH bootstrap + default-VPC public IP
  auto-assignment.

## GCP Compute Engine (`gcp`, aliases `gce`, `google`, `google_cloud`)

- Env: `MERV_GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`); `MERV_GCP_ZONE`
  (default `us-central1-a`); credentials via `GOOGLE_APPLICATION_CREDENTIALS`
  (a service-account JSON path) or ambient ADC/metadata credentials; optional
  `MERV_GCP_MACHINE_TYPE`, `MERV_GCP_IMAGE_PROJECT`/`MERV_GCP_IMAGE_FAMILY`,
  `MERV_GCP_BOOT_DISK_GIB` (default 200).
- Deps: `google-auth` + `requests` (`pip install 'merv[gcp]'`), used only to
  mint OAuth tokens; the Compute API is called over the stdlib.
- Credentials: create a service account with Compute Admin, download its JSON
  key, point `GOOGLE_APPLICATION_CREDENTIALS` at it.
- Quirks: instances are addressed by NAME (the rp- name is the sandbox id).
  Catalog lists GPU-bundled machine types (a2/a3/g2) in the configured zone;
  N1+attached-GPU shapes are not composed. Default image is the Deep Learning
  VM Ubuntu family with `install-nvidia-driver=True`. A shared
  `merv-sandbox-allow-ssh` firewall rule (tag-scoped, port 22) is created
  once per project; per-instance `enable-oslogin=FALSE` keeps OS Login from
  overriding the bootstrap's SSH principals. No prices in the Compute API.
  `TERMINATED` means stopped-but-billing-disks — only a 404 is gone. Fresh
  projects have a ZERO GPU quota (`GPUS_ALL_REGIONS`) — request an increase
  once. NEEDS LIVE SMOKE TEST: the DLVM image family name drifts with CUDA
  releases; override `MERV_GCP_IMAGE_FAMILY` if the default 404s.

## Azure (`azure`, aliases `az`, `microsoft_azure`)

- Env: `MERV_AZURE_TENANT_ID`, `MERV_AZURE_CLIENT_ID`,
  `MERV_AZURE_CLIENT_SECRET`, `MERV_AZURE_SUBSCRIPTION_ID` (`AZURE_*`
  variants accepted); `MERV_AZURE_LOCATION` (default `eastus`); optional
  `MERV_AZURE_VM_SIZE`, `MERV_AZURE_IMAGE` (publisher:offer:sku:version URN,
  default `microsoft-dsvm:ubuntu-hpc:2204:latest`), `MERV_AZURE_OS_DISK_GIB`
  (default 200).
- Deps: none — client-credentials OAuth2 and ARM REST ride the stdlib.
- Credentials: `az ad sp create-for-rbac --role Contributor --scopes
  /subscriptions/<id>` prints the tenant/client/secret triple.
- Quirks: each sandbox lives in its own resource group
  (`rp-<uid>-rg` holding NSG + VNet + public IP + NIC + VM), so terminate is
  ONE atomic group delete (async server-side). The Ubuntu HPC image ships
  NVIDIA drivers. The Resource SKUs API supplies the GPU menu (subscription
  restrictions filtered out); $/hr comes best-effort from the public Retail
  Prices API at placement. Deallocated/Failed VMs still bill disks — only a
  404 is gone. Fresh subscriptions have a ZERO vCPU quota for GPU families —
  request the increase once under Quotas > Compute.
  NEEDS LIVE SMOKE TEST: ubuntu-hpc cloud-init handling of customData and
  NSG propagation delay before first SSH.
