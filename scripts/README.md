# Scripts

Local developer and repository validation scripts live here.

Iteration 0 includes `check-structure.sh`, which validates the repository skeleton and documentation contract. Infrastructure checks live in `check-infra.sh`; it runs Terraform formatting/validation and renders the upstream Helm charts with local values.

Iteration 2 adds `check-project-code.sh`, which installs the project-code package
into an isolated target directory and executes the Sales Dagster smoke job
in-process. Iteration 3 adds `scripts/local/floe-manifest.sh` and
`scripts/local/iteration3-smoke.sh` for the manifest-first Sales dlt and Floe
path.

Local stack scripts under `scripts/local/` are thin wrappers around Terraform for
the lakehouse deployment and around kind for cluster creation/destruction. The
local kind cluster config lives under `infra/kind/local/`. The project-code
image helper scripts build the local image, load it into kind, and launch the
Dagster smoke runs.
