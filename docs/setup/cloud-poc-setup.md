# Cloud POC setup (AWS / Azure)

How to deploy the OpenLakeForge POC to your **own** AWS or Azure account. Nothing
account-specific is committed to the repo: you provide your credentials through
your cloud CLI and your configuration through a local, gitignored file (AWS) or
environment variables (Azure).

> The local (kind/SeaweedFS) path needs none of this — see the root `README.md`.
> This guide is only for the managed-cloud POCs.

## Prerequisites

Install and put on your `PATH`: `terraform` (>= 1.6), `kubectl`, `helm`,
`docker`, `python3`, `make`, plus the CLI for your cloud (`aws` or `az`).

Terraform state is stored **locally** (no remote backend), so run the `make`
targets from the same machine/checkout each time for a given environment.

---

## AWS (EKS)

### 1. Authenticate

Configure AWS credentials however your account works — a named profile, SSO, or
static keys — so that `aws sts get-caller-identity` succeeds. The scripts use your
ambient AWS credentials, so export the profile if you use one:

```bash
export AWS_PROFILE=my-profile
aws sso login --profile my-profile   # only if the profile is SSO-based
aws sts get-caller-identity          # sanity check
```

If your network intercepts TLS (corporate proxy such as Zscaler), point the AWS
CLI at your CA bundle: `export AWS_CA_BUNDLE=/path/to/ca-bundle.pem`.

### 2. Provide your configuration (tfvars)

Copy the tracked templates to local `sandbox.tfvars` files and edit them. These
carry only non-secret **tags** (and the foundation cluster name) — the values
your account mandates. `sandbox.tfvars` is gitignored, so it stays on your
machine.

```bash
cd infra/terraform/foundations/aws-eks
cp sandbox.tfvars.example sandbox.tfvars      # set your Owner tag

cd ../../environments/aws-poc
cp sandbox.tfvars.example sandbox.tfvars      # set your Owner tag
```

Set `Owner`/`Requester` to **your** email. The **cluster name** is not in tfvars —
it comes from `AWS_CLUSTER_NAME` (see the overrides table below) so it stays in
sync with the kube context; if your account needs an IAM naming prefix such as
`limited-`, set that via `AWS_CLUSTER_NAME`. To keep the tfvars elsewhere:
`export AWS_TFVARS_FILE=/abs/path/to/your.tfvars`.

### 3. Common overrides (optional)

All have sane defaults; override via environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AWS_REGION` | `eu-west-1` | Region for the foundation + platform |
| `AWS_CLUSTER_NAME` | `eks-openlakeforge-poc` | EKS cluster name (must match `cluster_name` in the foundation tfvars) |
| `AWS_NODE_INSTANCE_TYPES` | `m7i.large` | Node group instance type(s) |
| `AWS_TFVARS_FILE` | `<dir>/sandbox.tfvars` | Path to your tfvars |

### 4. Deploy

Two-phase deploy (foundation, then platform + artifacts — see ADR 0008):

```bash
make aws-foundation-up      # VPC, EKS, ECR, IAM; writes your kubeconfig context
make aws-infra-up           # RDS, S3, Glue, Trino, Superset, Dagster, OpenMetadata
make aws-artifacts-deploy   # build/push images, upload Floe manifests, load code
make aws-forward            # port-forward Superset/Dagster/etc. to localhost
```

`make aws-up` runs infra + artifacts together after the foundation exists.

### 5. Tear down

```bash
make aws-down               # platform environment (RDS, buckets, Helm releases)
make aws-foundation-down    # EKS, ECR, networking
```

ECR repos use `force_delete` and the Superset module has a destroy-time guard, so
teardown does not stall on non-empty registries or a stuck reports PVC.

---

## Azure (AKS)

Azure is configured through **environment variables** (no tfvars file).

### 1. Authenticate

```bash
az login
az account set --subscription "<your-subscription-id>"
az account show          # sanity check — the scripts require this to succeed
```

### 2. Configure (optional overrides)

All have defaults; override via environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AZURE_RESOURCE_GROUP` | `rg-openlakeforge-azure-poc` | Resource group |
| `AZURE_LOCATION` | `westeurope` | Region |
| `AZURE_CLUSTER_NAME` | `aks-openlakeforge-poc` | AKS cluster name |
| `AZURE_NODE_COUNT` | `3` | Node count |
| `AZURE_NODE_VM_SIZE` | `Standard_D4s_v5` | Node VM size |
| `AZURE_ACR_NAME_PREFIX` | `openlakeforgepoc` | ACR name prefix (globally unique) |

### 3. Deploy / tear down

```bash
make azure-foundation-up
make azure-up               # infra + artifacts
make azure-forward
# ...
make azure-down
make azure-foundation-down
```

---

## What stays out of git

- **Credentials** — never in the repo; supplied by `aws`/`az` CLI.
- **`*.tfvars`** — gitignored; only the `*.tfvars.example` templates are tracked.
- **Terraform state** (`*.tfstate`) and `.terraform/` — local only.

If you add a new account-specific value, put it in your local `sandbox.tfvars`
(AWS) or an env var (Azure) — not in a tracked file.
