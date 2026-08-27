# Cloud POC setup (AWS / Azure)

How to deploy the OpenLakeForge POC to your **own** AWS or Azure account. Nothing
account-specific is committed to the repo: `olf` authenticates through the
cloud SDKs and your configuration stays in local, gitignored files or
environment variables.

> The local (kind/SeaweedFS) path needs none of this — see the root `README.md`.
> This guide is only for the managed-cloud POCs.

## Prerequisites

Installed from PyPI (`pip install openlakeforge`), you need `docker` and
`python3` on your `PATH`. From a source checkout, you additionally need `git`
and `uv`; `make` is optional, deprecated compatibility whose targets delegate
to the same `olf` commands this guide uses directly.

`olf` provisions its own versioned `terraform`, `kubectl`, and `helm` under
`OLF_HOME` (default `~/.openlakeforge`). Neither `aws` nor `az` is required.
Set `OLF_TOOLCHAIN_MODE=host` to use host Terraform, kubectl, and Helm instead.

Terraform state is stored **locally** (no remote backend), so run the `olf`
commands from the same machine/project each time for a given environment.

---

## AWS (EKS)

### 1. Authenticate

Use IAM Identity Center without installing AWS CLI:

```bash
olf auth login --provider aws \
  --start-url "https://your-company.awsapps.com/start" \
  --sso-region eu-west-1
```

This opens an AWS-hosted device authorization page. If the browser cannot open,
use `--no-browser` and enter the printed code. To reuse an existing SDK/CLI SSO
profile without copying its cache, run
`olf auth login --provider aws --profile my-profile`. Export `AWS_PROFILE` if
needed.

If your network intercepts TLS (corporate proxy such as Zscaler), point boto3
at your CA bundle: `export AWS_CA_BUNDLE=/path/to/ca-bundle.pem`. botocore
reads this the same way the AWS CLI did.

### 2. Provide your configuration (tfvars)

Copy the tracked templates to local `sandbox.tfvars` files and edit them. These
carry only non-secret **tags** (and the foundation cluster name) — the values
your account mandates. `sandbox.tfvars` is gitignored, so it stays on your
machine.

In a checkout, the templates are files on disk. Installed from PyPI, they are
part of the read-only payload `olf distribution path` prints, so the same
commands work either way:

```bash
DIST="$(olf distribution path)"
cp "$DIST/infra/terraform/foundations/aws-eks/sandbox.tfvars.example" sandbox.tfvars   # set your Owner tag
export AWS_TFVARS_FILE="$(pwd)/sandbox.tfvars"
```

`AWS_TFVARS_FILE` is reused for both the foundation and the platform apply, so
one file covers both. Set `Owner`/`Requester` to **your** email. If your
account needs an IAM naming prefix such as `limited-`, set that via
`AWS_CLUSTER_NAME`.

### 3. Common overrides (optional)

All have sane defaults; override via environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AWS_REGION` | `eu-west-1` | Region for the foundation + platform |
| `AWS_CLUSTER_NAME` | `limited-eks-openlakeforge-poc` | EKS cluster name (must match `cluster_name` in the foundation tfvars) |
| `AWS_NODE_INSTANCE_TYPES` | `m7i.large` | Node group instance type(s) |
| `AWS_TFVARS_FILE` | `<dir>/sandbox.tfvars` | Path to your tfvars |
| `--kubeconfig-path` (CLI flag) | `.tmp/kubeconfigs/aws.yaml` in a checkout, `~/.openlakeforge/state/aws/kubeconfig.yaml` installed | Isolated EKS kubeconfig; not an environment variable |

### 4. Deploy

Three-step deploy (foundation, platform, then artifacts):

```bash
olf deploy --provider aws --phase foundation   # VPC, EKS, ECR, IAM; writes your kubeconfig context
olf deploy --provider aws --phase platform     # RDS, S3, Glue, Trino, Superset, Dagster, OpenMetadata
olf deploy --provider aws --phase artifacts    # build/push images, upload Floe manifests, load code
olf forward --provider aws                     # port-forward Superset/Dagster/etc. to localhost
```

`olf deploy --provider aws` with no `--phase` runs foundation, platform, and
artifacts in sequence.

### 5. Tear down

```bash
olf destroy --provider aws --phase platform     # platform environment (RDS, buckets, Helm releases)
olf destroy --provider aws --phase foundation   # EKS, ECR, networking
olf destroy --provider aws                      # full teardown: platform, then foundation
```

ECR repositories use `force_delete`. Superset report ZIPs use ephemeral pod
storage, so teardown does not depend on deleting a reports PVC.

---

## Azure (AKS)

Azure resource-group settings live in a local **tfvars file** so temporary
sandbox names are not committed to the repository.

### 1. Authenticate

```bash
olf auth login --provider azure
```

Azure Identity opens Microsoft Entra's hosted sign-in page, then prompts for a
subscription. On a headless machine use `olf auth login --provider azure
--device-code`. An existing `az login` session is reused when `az` is present,
but the CLI is optional.

If your network intercepts TLS, point Azure's SDK (its `requests` transport)
at your CA bundle: `export REQUESTS_CA_BUNDLE=/path/to/ca-bundle.pem`.

### 2. Configure the resource group

Copy the tracked template and set the name and region of your sandbox resource
group, plus an AKS VM size permitted by that subscription and region. Keep
`create_resource_group = false` when the group is supplied by your company
sandbox. When Terraform should create the group, set it to `true`; removing
`resource_group_name` then uses the default `rg-openlakeforge-azure-poc`.

In a checkout, the template is a file on disk. Installed from PyPI, it is
part of the read-only payload `olf distribution path` prints, so the same
commands work either way:

```bash
cp "$(olf distribution path)/infra/terraform/foundations/azure-aks/sandbox.tfvars.example" sandbox.tfvars
export AZURE_TFVARS_FILE="$(pwd)/sandbox.tfvars"
```

### 3. Configure optional runtime overrides

All have defaults; override via environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AZURE_TFVARS_FILE` | `<foundation-dir>/sandbox.tfvars` | Path to your resource-group tfvars |
| `AZURE_CLUSTER_NAME` | `aks-openlakeforge-poc` | AKS cluster name |
| `AZURE_NODE_COUNT` | `3` | Node count |
| `AZURE_ACR_NAME_PREFIX` | `openlakeforgepoc` | ACR name prefix (globally unique) |
| `--kubeconfig-path` (CLI flag) | `.tmp/kubeconfigs/azure.yaml` in a checkout, `~/.openlakeforge/state/azure/kubeconfig.yaml` installed | Isolated AKS kubeconfig; not an environment variable |

### 4. Deploy / tear down

```bash
olf deploy --provider azure --phase foundation
olf deploy --provider azure               # foundation + platform + artifacts
olf forward --provider azure
# ...
olf destroy --provider azure --phase platform     # platform services only
olf destroy --provider azure --phase foundation   # AKS, ACR, and resource group resources
olf destroy --provider azure                      # full teardown: platform, then foundation
```

## Concurrent deployments

The complete local, Azure, and AWS workflows can run at the same time. Each one
uses its own kubeconfig, Helm cache, Docker credential directory, report work
directory, and port-forward logs:

```bash
olf deploy --provider local &
olf deploy --provider azure &
olf deploy --provider aws &
wait
```

The default kubeconfig, Terraform state, and work/cache paths are all
scoped by *provider*, so the three different providers above never collide
with no extra flags needed. That scoping is per-provider, not per-invocation:
**two concurrent deployments of the same provider are not supported.** They
would resolve the same default kubeconfig, the same Terraform state file, and
the same work/cache directories regardless of `--kubeconfig-path` — that flag
only relocates the kubeconfig, not the Terraform state or work root the two
runs would still contend for. The workflows never switch the current context
in your global kubeconfig.

---

## What stays out of git

- **Credentials** — never in the repo; resolved by the cloud SDK from OLF
  browser authentication, vendor session reuse, or automation identity.
- **`*.tfvars`** — gitignored; only the `*.tfvars.example` templates are tracked.
- **Terraform state** (`*.tfstate`) and `.terraform/` — local only.

If you add a new account-specific value, put it in your local `sandbox.tfvars`,
not in a tracked file.
