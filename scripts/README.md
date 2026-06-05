# Scripts

Local developer and repository validation scripts live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the Sales Dagster pipeline definitions load.
`scripts/local/floe-manifest.sh` generates the manifest-first Sales Floe
contract.

Local stack scripts under `scripts/local/` are thin wrappers around Terraform for
the lakehouse deployment and around kind for cluster creation/destruction. The
local kind cluster config lives under `infra/kind/local/`. The project-code and
Superset image helper scripts build local images and load them into kind; the
Superset report scripts deploy/export source-controlled report bundles.
