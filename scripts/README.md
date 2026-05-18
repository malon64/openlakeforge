# Scripts

Local developer and repository validation scripts live here.

Iteration 0 includes `check-structure.sh`, which validates the repository skeleton and documentation contract.

Local stack scripts under `scripts/local/` are thin wrappers around Terraform for
the lakehouse deployment and around kind for cluster creation/destruction. The
local kind cluster config lives under `infra/kind/local/`.
