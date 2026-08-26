# ADR 0031: PyPI `olf` carries a verified platform payload

## Status

Accepted. Extends ADR 0029's managed-toolchain decision without changing the
two-phase deployment boundary in ADR 0008 or the provider adapter boundary.

## Context

The deployment engine was previously useful only from a repository checkout:
Terraform roots, Helm values, image build assets, schemas, and the demo
project were resolved from the checkout. That makes a Python package alone
insufficient for a no-clone installation.

## Decision

- Publish `openlakeforge-domain-model` and `openlakeforge`; the latter owns
  the `olf` console command and pins the former exactly.
- `release/component-catalog.yaml` is the canonical release identity. Its
  alpha form (for example `0.1.0-alpha.1`) maps to PEP 440 (`0.1.0a1`).
- The `openlakeforge` wheel and sdist carry a deterministic, allowlisted
  `platform.tar.gz` plus per-file manifest. Installed `olf` verifies and
  atomically extracts it under `OLF_HOME/distributions` before use.
- Platform assets are immutable. Project code, Terraform state, Terraform
  data, generated artifacts, Docker staging, and Helm downloads have separate
  project, state, work, and cache roots. Source mode retains `.tmp`.
- Helm charts stay outside the wheel. The component catalog pins each chart's
  repository, exact version, and archive SHA-256; cached packages are verified
  before Terraform is allowed to use them.
- PyPI publication uses GitHub OIDC Trusted Publishing. GitHub Releases retain
  signed checksums, SBOMs, provenance, and the published Python artifacts.

## Consequences

Users provide Python 3.12, uv, and Docker, then install a fixed release with
`uv tool install "openlakeforge==<version>" --python 3.12`. Terraform, Helm,
kubectl, and kind continue to be privately managed by ADR 0029. Automatic
state migration remains outside this alpha decision.
