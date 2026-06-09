# Architecture Decision Records

Architecture decision records capture decisions that shape OpenLakeForge and should remain stable unless a later ADR supersedes them.

Start with `0001-v1-platform-baseline.md` for the v1 platform baseline.

`0002-local-object-storage-seaweedfs.md` supersedes the Iteration 1 local object
storage choice from Garage to SeaweedFS.

`0003-local-dagster-project-code-runtime.md` records the Iteration 2 Dagster and
project-code runtime boundary.

`0004-manifest-first-floe-sales-ingestion.md` records the Iteration 3 Sales dlt
and Floe manifest-first runtime boundary.

`0005-dbt-duckdb-gold-on-dagster-kubernetes.md` records the Iteration 4
dbt-duckdb Gold runtime boundary.

`0006-openmetadata-governance-and-openlineage.md` records the Iteration 5
OpenMetadata deployment, the OpenLineage proxy normalisation pattern, and the
Polaris bootstrap workaround.

`0007-superset-reporting-over-gold-via-trino.md` records the Iteration 6
Superset deployment model, custom image, and YAML-based report bundle lifecycle.

`0008-two-phase-deploy-infra-and-artifacts.md` records the split between
Terraform-owned static infrastructure (Phase 1) and domain artifact deployment
(Phase 2), and defines the CD boundary.
