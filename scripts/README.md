# Scripts

Local developer and repository validation scripts live here.

Iteration 0 includes `check-structure.sh`, which validates the repository skeleton and documentation contract. Infrastructure checks live in `check-infra.sh`; it runs Terraform formatting/validation and renders the upstream Helm charts with local values.

`check-project-code.sh` installs the project-code package into an isolated target
directory and verifies that the Sales Dagster pipeline definitions load.
`scripts/local/floe-manifest.sh` generates the manifest-first Sales Floe
contract.

Local stack scripts under `scripts/local/` are thin wrappers around Terraform for
the lakehouse deployment and around kind for cluster creation/destruction. The
local kind cluster config lives under `infra/kind/local/`. The project-code
image helper scripts build the local image and load it into kind.
