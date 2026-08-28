# Provider Contracts

OpenLakeForge platform modules exchange provider-neutral contracts instead of
allowing runtime code or project descriptors to select infrastructure directly.
The contract is the portability boundary: the same project can use Polaris and
SeaweedFS locally or on Azure, and Glue and S3 on AWS.

Terraform remains the source of physical provider values. `olf` parses those
values, validates them against the resolved Deployment Profile topology, and
derives the environment consumed by Floe, dbt, Dagster, Superset, and metadata
reconciliation. Product-owned descriptors contain logical identities only.

## Versions and deployment phases

The deployed Terraform roots currently export flat `2.0.0` contracts. `olf`
adapts one to a single DEV-stage v3 view, preserving the v0.2 environment
exports. Unknown versions fail closed.

`3.0.0` is the binding stage-aware shape. It is represented by strict schema,
typed parser, and local, Azure, and AWS fixtures now; #133 and #114 will make
the Terraform roots emit and provision it. A native v3 runtime must select a
stage explicitly. No profile or project descriptor receives a bucket, catalog,
provider adapter, credential, or generated endpoint.

`olf deploy` has three ordered phases:

1. Foundation creates the cluster and registry.
2. Platform creates static Terraform-managed services.
3. Artifacts deploys data-project-derived images, manifests, reports, and
   metadata.

The static/dynamic boundary is between platform and artifacts. A code commit
runs artifacts only; platform never waits for project artifacts.

## v3 contract shape

Every v3 document has these top-level fields:

- `deployment`: logical profile name, provider, and region, verified against
  `DeploymentTopology`.
- `shared`: foundation, cluster, metadata database, query, optional catalog or
  governance service, registry, ops storage, secrets, identity/access, and
  observability.
- `stages`: one entry for every enabled DEV/UAT/PROD stage, and no disabled
  stages.

Each stage owns its namespace, distinct Bronze/Silver/Gold storage bindings,
catalog and query bindings, orchestration, capability-dependent reporting and
governance bindings, runtime identity, activation prefix, and endpoint
references. Stage references may point only at `shared/*` or the same
`stage/<name>/*`; a DEV binding cannot reference PROD.

Ops artifacts are shared storage with a stage-specific activation prefix,
`activations/<stage>`. They do not define the revision manifest or promotion
workflow, which belong to #154 and #115.

## Logical and physical identity

Logical SQL is invariant across providers:

```text
lakehouse_<stage>.<owner>_<layer>.<table>
```

For example, every provider exposes DEV sales orders as
`lakehouse_dev.sales_silver.orders`. Business asset keys and descriptors do not
gain a stage prefix.

- Local: shared Polaris service, one physical Polaris catalog per stage, and
  distinct SeaweedFS Bronze, Silver, and Gold buckets per stage.
- Azure: shared Polaris service on AKS, one physical Polaris catalog per
  stage, and distinct SeaweedFS-on-AKS buckets per stage.
- AWS: one custom Glue catalog per stage, exposed through Trino's native Glue
  `catalogid`, and distinct S3 buckets per stage.

AWS custom Glue catalog names are lower-case
`olf_<profile>_<stage>`. If that exceeds Glue's 64-character limit, the
profile portion is truncated and an eight-character hash of the full name is
appended. The physical ID is `<account-id>:<catalog-name>`, while Trino's SQL
alias remains `lakehouse_<stage>`. Thus DEV and PROD may both contain the
physical Glue database `sales_silver` without collision.

The AWS adapter keeps native Glue rather than introducing another catalog
technology. It supplies the stage catalog ID, region, and workload-identity
reference; it never supplies credentials. #114 must upgrade the AWS Terraform
provider to a release supporting `aws_glue_catalog` before it provisions these
catalogs.

## Validation and migration

`olf check contracts` validates the published v3 JSON Schema and parses local,
Azure, and AWS fixture contracts. The parser rejects unknown fields and
versions, missing or extra topology stages, incomplete capability bindings,
cross-stage references, duplicate stage storage/catalog/identity values, and
provider/topology mismatches.

The v2 adapter is intentionally temporary. It lifts a flat deployed contract
to one DEV stage without changing current v0.2 environment output. A v3
contract is not permitted to choose DEV implicitly: callers must select the
stage they are configuring.

## Stable capability interfaces

Consumers depend on fields such as storage URI and bucket, catalog type and
logical name, query endpoint, Secret reference, identity reference, and
artifact activation prefix. They do not depend on SeaweedFS, S3, Polaris, Glue,
kind, AKS, EKS, or a physical catalog name. See ADR 0003 for the binding
decision and ADR 0010 for the provider mappings.
