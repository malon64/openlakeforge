# ADR 0030: SDK-managed cloud authentication

## Status

Accepted. Supersedes ADR 0029's decision to leave `aws` and `az` as required
host prerequisites. It does not change ADR 0008's deploy phases or the
Terraform/Helm lifecycle.

## Context

The managed toolchain made Terraform, Helm, kubectl, and kind installable by
`olf`, but AWS and Azure deploys still required vendor CLIs only for
authentication and a handful of API calls. boto3 and Azure's Python SDKs cover
those APIs directly. Keeping cloud CLIs made a clean-machine cloud deployment
needlessly large and split credential ownership between `olf` and host tools.

EKS creates one special constraint: the normal AWS CLI kubeconfig uses an
`exec` plugin that invokes `aws` every time kubectl needs a token. AzureRM has
the inverse constraint: its locked provider version asks an executable named
`az` for account metadata and access tokens when using interactive user auth.

## Decision

- `olf auth login --provider aws` uses boto3 IAM Identity Center device
  authorization. AWS supplies the verification URL; OLF only opens that URL
  with the operating system browser or prints it with a one-time code. OLF
  never hosts an authentication webpage or handles user passwords/MFA input.
- `olf auth login --provider azure` uses Azure Identity's browser credential.
  Microsoft Entra hosts the page and the SDK owns its local redirect listener.
  Azure device code is the explicit headless fallback.
- OLF state lives under `OLF_HOME/auth`, uses owner-only files, and never
  stores long-lived AWS keys or generated-artifact credentials. Vendor-owned
  AWS/Azure CLI caches are read through their SDK credential providers rather
  than copied or deleted.
- AWS EKS kubeconfig contains a Python-minted `k8s-aws-v1` bearer token in a
  private token file, never an `exec` block. OLF refreshes the token for cloud
  operations before its short expiry.
- Terraform receives OLF-managed AWS credentials through a generated,
  process-scoped `credential_process` profile. No `aws` binary participates.
- AzureRM's four-command Azure CLI protocol is implemented by a tiny SDK-backed
  bridge exposed as `az` only in Terraform's child `PATH`. It accepts only
  `version`, `account show`, `account list`, and `account get-access-token`.
  It is not installed globally and is covered by a fixed command-contract test.
- Existing automation credentials remain first-class: workload identity,
  managed identity, service principal, and normal AWS SDK credential sources
  all bypass interactive browser login.

## Consequences

- AWS and Azure users authenticate once with `olf` and can deploy without the
  corresponding vendor CLI installed.
- A user who already ran `aws sso login` can adopt that named profile. A user
  with a valid `az login` session can reuse it while `az` remains installed.
- AzureRM upgrades require review of the bridge contract. If the provider gains
  direct delegated-token support, the bridge should be removed in a later ADR.
