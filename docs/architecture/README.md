# Architecture Documentation

This directory contains the repo-local architecture source of truth for OpenLakeForge.

- `overview.md` describes the initial v1 platform shape and ownership boundaries.
- `azure-aks-poc.md` describes the first Azure deployment target and test flow.
- `aws-eks-poc.md` describes the AWS EKS managed-services POC and compatibility
  gate.
- `local-stack-contracts.md` describes the Terraform-managed local service interfaces.
- `provider-contracts.md` describes the provider-neutral contract boundary that
  keeps the local implementation cloud-ready.
- `../technical-debt.md` tracks known weaknesses, mitigations, and fix paths.
- `../testing/floe-openlineage-capture-test-plan.md` describes the capture-based
  validation path for Floe OpenLineage events.
- Architecture decision records live in `../adr/`.
