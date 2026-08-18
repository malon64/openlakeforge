# Build your first data product

This tutorial walks through adding a new data product to OpenLakeForge and running it end to end.

By the end, you will have:

```text
CSV source
   ↓
dlt ingestion
   ↓
Bronze
   ↓
Floe validation
   ↓
Silver Iceberg
   ↓
dbt-trino
   ↓
Gold Iceberg
   ↓
Trino
```

with the complete pipeline orchestrated by Dagster.

The example creates a new `marketing` domain containing one data product:

```text
campaign_performance
```

with one source entity:

```text
campaigns.csv
```

and one Gold mart:

```text
mart_campaign_performance
```

> **Alpha note**
>
> Product onboarding is currently source-driven: you create a small set of files under `domains/`.
>
> A product scaffold is planned to automate this workflow. Until then, this tutorial documents the current golden path explicitly.

---

# Before you start

This tutorial assumes you already have the local **Slim** environment running.

If not:

```bash
make local-slim-up
```

See the [local installation guide](../setup/local.md) for the complete setup.

Slim is recommended for this tutorial because it contains the entire data-engineering path without the optional OpenMetadata and Superset services.

---

# How OpenLakeForge organizes data products

Business and data-product code lives under:

```text
domains/
```

A domain groups one or more related data products.

For example:

```text
domains/
├── sales/
│   ├── order_revenue
│   └── customer_health
│
└── supply_chain/
    └── inventory_reliability
```

The implementation is organized by **capability**, while each product keeps the same identity across those capabilities:

```text
domains/<domain>/
├── domain.yaml
│
├── examples/raw/<product>/
├── extract/dlt/<product>.py
├── contracts/floe/<product>.yml
├── transformations/dbt/<product>/
├── pipelines/dagster/<product>.py
└── reports/superset/<product>/       # optional analytics layer
```

For this tutorial:

```text
domain  = marketing
product = campaign_performance
```

OpenLakeForge derives platform identities from those logical names:

```text
Dagster job
marketing_campaign_performance_pipeline

Silver namespace
marketing_campaign_performance_silver

Gold namespace
marketing_campaign_performance_gold
```

You do **not** create those namespaces manually and you do **not** modify Terraform to register a product.

---

# 1. Create the domain structure

From the repository root:

```bash
mkdir -p domains/marketing/examples/raw/campaign_performance
mkdir -p domains/marketing/extract/dlt
mkdir -p domains/marketing/contracts/floe
mkdir -p domains/marketing/transformations/dbt/campaign_performance/models/gold
mkdir -p domains/marketing/pipelines/dagster

touch domains/marketing/__init__.py
touch domains/marketing/extract/__init__.py
touch domains/marketing/extract/dlt/__init__.py
touch domains/marketing/pipelines/__init__.py
touch domains/marketing/pipelines/dagster/__init__.py
```

Your domain should now look like:

```text
domains/marketing/
├── __init__.py
├── domain.yaml
│
├── examples/
│   └── raw/
│       └── campaign_performance/
│
├── extract/
│   ├── __init__.py
│   └── dlt/
│       └── __init__.py
│
├── contracts/
│   └── floe/
│
├── transformations/
│   └── dbt/
│       └── campaign_performance/
│           └── models/
│               └── gold/
│
└── pipelines/
    ├── __init__.py
    └── dagster/
        └── __init__.py
```

---

# 2. Declare the domain and product

Create:

```text
domains/marketing/domain.yaml
```

with:

```yaml
apiVersion: openlakeforge.io/v1alpha2
kind: Domain

name: marketing
displayName: Marketing
description: Marketing analytics data products.
status: active

data_products:
  - id: campaign_performance
    name: marketing_campaign_performance
    displayName: Campaign Performance
    description: Campaign spend and conversion performance by channel.
    status: active

    asset_prefix: marketing_campaign_performance

    bronze:
      - name: campaigns
        path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns
        description: Raw marketing campaign performance data.

    silver_tables:
      tables:
        - name: campaigns
          description: Validated marketing campaigns.

    gold_tables:
      tables:
        - name: mart_campaign_performance
          description: Aggregated campaign performance by channel.
```

`domain.yaml` is the **logical source of truth** for the domain inventory.

It tells OpenLakeForge:

* which domains exist
* which products exist
* which Bronze entities belong to the product
* which Silver tables should exist
* which Gold tables should exist
* how the product is identified across the platform

The descriptor must remain provider-neutral.

You should not put Polaris, Glue, SeaweedFS, S3 endpoints, Kubernetes names, or other environment-specific infrastructure into it.

Those values are resolved by OpenLakeForge provider contracts.

---

# 3. Register the domain with Dagster

Create:

```text
domains/marketing/definitions.py
```

with:

```python
from __future__ import annotations

from libs.domain_definitions import definitions_for_domain

defs = definitions_for_domain("marketing", __file__)
```

That is all the domain-level Dagster configuration required.

The root `domains.definitions` module discovers domain Python packages automatically, and this adapter loads the products declared by the domain inventory.

You do **not** need to edit:

```text
domains/definitions.py
```

when adding the domain.

---

# 4. Add some source data

Create:

```text
domains/marketing/examples/raw/campaign_performance/campaigns.csv
```

with:

```csv
campaign_id,campaign_date,channel,spend,conversions
cmp_001,2026-08-01,search,120.50,18
cmp_002,2026-08-01,social,80.00,9
cmp_003,2026-08-02,search,150.00,24
cmp_004,2026-08-02,email,30.00,12
cmp_005,2026-08-03,social,95.50,11
```

The repository's current golden-path ingestion helper uses local CSV examples.

The same OpenLakeForge product model can support other dlt sources, but this tutorial deliberately uses the simplest existing ingestion path.

---

# 5. Create the Bronze loader

Create:

```text
domains/marketing/extract/dlt/campaign_performance.py
```

with:

```python
from __future__ import annotations

from pathlib import Path

from libs.bronze_csv import BronzeLoadResult, load_entities_to_bronze


CAMPAIGN_PERFORMANCE_ENTITIES = ("campaigns",)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]
_RAW_DIR = _DOMAIN_DIR / "examples" / "raw" / "campaign_performance"
_BRONZE_PREFIX = "marketing/campaign_performance"


def load_all_entities_to_bronze(
    raw_dir: Path | None = None,
) -> dict[str, BronzeLoadResult]:
    return load_entities_to_bronze(
        entities=CAMPAIGN_PERFORMANCE_ENTITIES,
        raw_dir=raw_dir or _RAW_DIR,
        bronze_prefix=_BRONZE_PREFIX,
    )
```

The loader:

1. reads `campaigns.csv` through dlt
2. writes it to the configured Bronze object store
3. returns metadata about the loaded entity to Dagster

The physical object store depends on the environment.

The logical Bronze path remains:

```text
marketing/campaign_performance/campaigns
```

whether the backend is local S3-compatible storage or a cloud provider.

---

# 6. Define the Bronze → Silver contract

Create:

```text
domains/marketing/contracts/floe/campaign_performance.yml
```

with:

```yaml
version: "0.2"

metadata:
  project: "openlakeforge"
  owner: "marketing"
  description: "Campaign Performance Bronze to Silver contract."
  tags: ["marketing", "campaign_performance", "silver"]

storages:
  default: "lakehouse_bronze"

  definitions:
    - name: "lakehouse_bronze"
      type: "s3"
      bucket: "{{OPENLAKEFORGE_STORAGE_BRONZE_BUCKET}}"
      region: "{{OPENLAKEFORGE_STORAGE_REGION}}"

    - name: "lakehouse_silver"
      type: "s3"
      bucket: "{{OPENLAKEFORGE_STORAGE_SILVER_BUCKET}}"
      region: "{{OPENLAKEFORGE_STORAGE_REGION}}"

    - name: "openlakeforge_ops"
      type: "s3"
      bucket: "{{OPENLAKEFORGE_OPS_BUCKET_NAME}}"
      region: "{{OPENLAKEFORGE_STORAGE_REGION}}"

report:
  path: "floe/reports/marketing/campaign_performance"
  storage: "openlakeforge_ops"

entities:
  - name: "campaigns"

    incremental_mode: "none"

    source:
      format: "csv"
      path: "marketing/campaign_performance/campaigns"
      storage: "lakehouse_bronze"

      options:
        header: true
        separator: ","
        encoding: "utf-8"
        glob: "*.csv"

      cast_mode: "strict"

    sink:
      write_mode: "overwrite"

      accepted:
        format: "iceberg"
        path: "marketing/campaign_performance/campaigns"
        storage: "lakehouse_silver"

        iceberg:
          catalog: "iceberg_catalog"
          namespace: "marketing_campaign_performance_silver"
          table: "campaigns"

      rejected:
        format: "csv"
        path: "floe/rejected/marketing/campaign_performance/campaigns"
        storage: "lakehouse_silver"

    policy:
      severity: "reject"

    schema:
      normalize_columns:
        enabled: true
        strategy: "snake_case"

      primary_key:
        - "campaign_id"

      columns:
        - name: "campaign_id"
          type: "string"
          nullable: false

        - name: "campaign_date"
          type: "date"
          nullable: false

        - name: "channel"
          type: "string"
          nullable: false

        - name: "spend"
          type: "number"
          nullable: false

        - name: "conversions"
          type: "integer"
          nullable: false
```

Floe now owns the technical transition from:

```text
Bronze
   ↓
validation
   ↓
Silver Iceberg
```

Rows satisfying the contract are written to:

```text
marketing_campaign_performance_silver.campaigns
```

Rejected rows are written to the product's rejection path instead of silently entering Silver.

---

# 7. Create the dbt project

Create:

```text
domains/marketing/transformations/dbt/campaign_performance/dbt_project.yml
```

with:

```yaml
name: marketing_campaign_performance
version: "1.0.0"
config-version: 2

profile: marketing_campaign_performance

model-paths: ["models"]
macro-paths: []

target-path: "target"

clean-targets:
  - "target"
  - "dbt_packages"

models:
  marketing_campaign_performance:
    +database: "{{ env_var('OPENLAKEFORGE_CATALOG_NAME', 'lakehouse_dev') }}"
    +materialized: table
    +on_table_exists: replace

    +meta:
      dagster:
        group: marketing_campaign_performance

    gold:
      +tags:
        - marketing
        - campaign_performance
        - gold
```

OpenLakeForge renders the environment-specific dbt `profiles.yml` automatically.

You should therefore **not hardcode Trino hosts or cloud-specific catalog settings into the dbt project**.

---

## Add the shared OpenLakeForge dbt package

Create:

```text
domains/marketing/transformations/dbt/campaign_performance/packages.yml
```

with:

```yaml
packages:
  - local: ../../../../../libs/dbt/openlakeforge_dbt
```

---

# 8. Declare the Silver source

Create:

```text
domains/marketing/transformations/dbt/campaign_performance/models/sources.yml
```

with:

```yaml
version: 2

sources:
  - name: silver

    database: "{{ env_var('OPENLAKEFORGE_CATALOG_NAME', 'lakehouse_dev') }}"
    schema: marketing_campaign_performance_silver

    description: Floe-owned Campaign Performance Silver Iceberg tables.

    tables:
      - name: campaigns
        description: Validated marketing campaign data.
```

dbt will consume the table created by Floe rather than owning a second staging layer.

The ownership boundary is:

```text
Bronze  → ingestion
Silver  → Floe
Gold    → dbt
```

---

# 9. Create the Gold model

Create:

```text
domains/marketing/transformations/dbt/campaign_performance/models/gold/mart_campaign_performance.sql
```

with:

```sql
with campaigns as (

    select
        campaign_id,
        cast(campaign_date as date) as campaign_date,
        channel,
        cast(spend as double) as spend,
        cast(conversions as bigint) as conversions

    from {{ source('silver', 'campaigns') }}

)

select
    channel,
    cast(count(*) as bigint) as campaign_count,
    cast(sum(spend) as double) as total_spend,
    cast(sum(conversions) as bigint) as total_conversions

from campaigns

group by channel
```

This model will be materialized as an Iceberg table in:

```text
marketing_campaign_performance_gold.mart_campaign_performance
```

---

## Document the Gold model

Create:

```text
domains/marketing/transformations/dbt/campaign_performance/models/gold/schema.yml
```

with:

```yaml
version: 2

models:
  - name: mart_campaign_performance
    description: Campaign spend and conversions aggregated by channel.
```

---

# 10. Create the Dagster product definition

Create:

```text
domains/marketing/pipelines/dagster/campaign_performance.py
```

with:

```python
from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.marketing.extract.dlt.campaign_performance import (
    CAMPAIGN_PERFORMANCE_ENTITIES,
    load_all_entities_to_bronze,
)


_DOMAIN_DIR = Path(__file__).resolve().parents[2]

CAMPAIGN_PERFORMANCE_GOLD_ASSETS = (
    "mart_campaign_performance",
)


defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="marketing",
        product="campaign_performance",
        asset_prefix="marketing_campaign_performance",
        entities=CAMPAIGN_PERFORMANCE_ENTITIES,
        gold_assets=CAMPAIGN_PERFORMANCE_GOLD_ASSETS,
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
```

This produces the Dagster job:

```text
marketing_campaign_performance_pipeline
```

and connects the three stages of the product:

```text
Bronze assets
     ↓
Floe Silver assets
     ↓
dbt Gold assets
```

---

# 11. Review the completed product

Your new domain should now contain approximately:

```text
domains/marketing/
├── __init__.py
├── definitions.py
├── domain.yaml
│
├── examples/
│   └── raw/
│       └── campaign_performance/
│           └── campaigns.csv
│
├── extract/
│   ├── __init__.py
│   └── dlt/
│       ├── __init__.py
│       └── campaign_performance.py
│
├── contracts/
│   └── floe/
│       └── campaign_performance.yml
│
├── transformations/
│   └── dbt/
│       └── campaign_performance/
│           ├── dbt_project.yml
│           ├── packages.yml
│           └── models/
│               ├── sources.yml
│               └── gold/
│                   ├── mart_campaign_performance.sql
│                   └── schema.yml
│
└── pipelines/
    ├── __init__.py
    └── dagster/
        ├── __init__.py
        └── campaign_performance.py
```

You did **not** modify:

```text
Terraform
Helm
domains/definitions.py
Dagster deployment configuration
catalog configuration
cloud configuration
```

Those platform concerns are derived from the domain inventory and provider contracts.

---

# 12. Validate the descriptor

Run:

```bash
make check-contracts
```

Among other platform contract checks, this validates every:

```text
domains/*/domain.yaml
```

against the canonical OpenLakeForge domain model and JSON Schema.

If the descriptor contains an invalid identifier, missing field, duplicate product identity, or invalid API version, validation fails before deployment.

For example, identifiers such as `id` and `asset_prefix` use:

```text
^[a-z][a-z0-9_]*$
```

so prefer:

```text
campaign_performance
```

rather than:

```text
campaign-performance
```

---

# 13. Validate dbt

Run:

```bash
make check-dbt
```

This discovers every product dbt project and validates that it compiles against the OpenLakeForge contracts.

For this product, OpenLakeForge expects:

```text
Silver:
lakehouse_dev.marketing_campaign_performance_silver.*

Gold:
lakehouse_dev.marketing_campaign_performance_gold.*
```

The exact catalog implementation can differ between environments while those logical relations remain stable.

---

# 14. Generate and validate the Floe manifest

Floe manifests are generated from the product contract.

Run:

```bash
make floe-manifest
```

OpenLakeForge discovers:

```text
domains/*/contracts/floe/*.yml
```

automatically.

For this product it generates:

```text
domains/marketing/contracts/floe/manifests/campaign_performance.manifest.json
```

Do not hand-author the generated manifest.

The artifact deployment workflow also generates the immutable runtime version used by Dagster and the Floe runner.

---

# 15. Deploy the new product

Because the platform is already running, you do **not** need to recreate Kubernetes or reinstall the platform.

For Slim:

```bash
make local-slim-artifacts-deploy
```

This performs the product-aware deployment phase:

```text
domain.yaml
     ↓
domain inventory
     ↓
reconcile catalog namespaces
     ↓
generate Floe runtime manifests
     ↓
publish runtime artifacts
     ↓
build project-code image
     ↓
load image into kind
     ↓
restart Dagster code
```

The new namespaces are created automatically:

```text
marketing_campaign_performance_silver
marketing_campaign_performance_gold
```

No Terraform product registration is required.

---

# 16. Run the pipeline

Start port forwarding if it is not already running:

```bash
make local-forward
```

Open:

```text
http://localhost:3000
```

In Dagster, look for:

```text
marketing_campaign_performance_pipeline
```

Launch the job.

Dagster should execute:

```text
campaigns.csv
     ↓
marketing_campaign_performance/campaigns_source
     ↓
dlt → Bronze
     ↓
Floe → campaigns
     ↓
Silver Iceberg
     ↓
dbt-trino
     ↓
mart_campaign_performance
     ↓
Gold Iceberg
```

You can inspect every step from the Dagster asset graph.

---

# 17. Query the Gold table

Once the pipeline succeeds, query the result through Trino.

From the repository root:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local \
exec -n lakehouse deploy/trino-coordinator -- \
trino --execute "
SELECT *
FROM iceberg.marketing_campaign_performance_gold.mart_campaign_performance
ORDER BY channel
"
```

You should see one row per marketing channel with its campaign count, spend, and conversions.

You can also inspect the tables:

```sql
SHOW TABLES
FROM iceberg.marketing_campaign_performance_silver;
```

and:

```sql
SHOW TABLES
FROM iceberg.marketing_campaign_performance_gold;
```

---

# 18. Run the complete Slim validation

Finally:

```bash
make local-slim-e2e
```

The E2E suite discovers products from the domain inventory.

Your new product therefore becomes part of the validation automatically.

The suite will:

1. verify the descriptor-derived catalog namespaces
2. discover the Dagster job
3. launch the product pipeline
4. verify the expected Silver table exists
5. verify the expected Gold table exists
6. query the Gold table and confirm that it contains data
7. verify the product's runtime artifacts

There is no seed-product allowlist to update.

---

# What OpenLakeForge derived for you

From:

```yaml
name: marketing

data_products:
  - id: campaign_performance
    asset_prefix: marketing_campaign_performance
```

OpenLakeForge can derive:

| Resource              | Derived identity                                                    |
| --------------------- | ------------------------------------------------------------------- |
| Dagster module        | `domains.marketing.pipelines.dagster.campaign_performance`          |
| Dagster job           | `marketing_campaign_performance_pipeline`                           |
| Silver namespace      | `marketing_campaign_performance_silver`                             |
| Gold namespace        | `marketing_campaign_performance_gold`                               |
| Floe manifest         | `marketing/campaign_performance/campaign_performance.manifest.json` |
| Floe reports          | `marketing/campaign_performance/`                                   |
| dbt runtime artifacts | `marketing/campaign_performance/`                                   |

Provider contracts then resolve those logical identities into the physical storage and catalog implementation of the active environment.

That is why the same data product can move between local Kubernetes, AWS, and Azure without embedding provider-specific infrastructure into its business definition.

---

# Adding more source entities

A product can contain more than one Bronze entity.

For example:

```text
campaigns
ad_groups
ad_events
```

Update `domain.yaml`:

```yaml
bronze:
  - name: campaigns
    path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns

  - name: ad_groups
    path: s3://lakehouse-bronze/marketing/campaign_performance/ad_groups

  - name: ad_events
    path: s3://lakehouse-bronze/marketing/campaign_performance/ad_events
```

Add the corresponding Silver tables:

```yaml
silver_tables:
  tables:
    - name: campaigns
    - name: ad_groups
    - name: ad_events
```

Then update:

```python
CAMPAIGN_PERFORMANCE_ENTITIES = (
    "campaigns",
    "ad_groups",
    "ad_events",
)
```

and define each entity in the Floe contract.

The Dagster product definition automatically creates the Bronze/Floe asset selection from that entity list.

---

# Adding more Gold models

Add another SQL model under:

```text
transformations/dbt/campaign_performance/models/gold/
```

For example:

```text
mart_campaign_daily.sql
```

Declare it in `domain.yaml`:

```yaml
gold_tables:
  tables:
    - name: mart_campaign_performance
    - name: mart_campaign_daily
```

and add it to the Dagster product definition:

```python
CAMPAIGN_PERFORMANCE_GOLD_ASSETS = (
    "mart_campaign_performance",
    "mart_campaign_daily",
)
```

After redeploying the artifacts, both Gold models become part of the product pipeline.

---

# Using the Full profile

The data product created in this tutorial works with the same core pipeline in the Full profile.

When governance is enabled, OpenMetadata metadata is derived from `domain.yaml` and deployed during the optional artifact phase.

Superset reporting is maintained separately under:

```text
domains/<domain>/reports/superset/<product>/
```

and is not required for the Slim tutorial.

> **Current alpha limitation**
>
> The Full E2E suite currently expects each discovered product to provide source-controlled Superset dashboard assets when the analytics layer is enabled.
>
> If you want this product to participate in `make local-e2e`, add its Superset report bundle before running the Full validation suite.

The core product pipeline itself does not depend on Superset.

---

# Adding a product to an existing domain

You do not need to create a new domain for every product.

To add another product to `marketing`, add another entry to:

```text
domains/marketing/domain.yaml
```

and create the corresponding:

```text
examples/raw/<product>/
extract/dlt/<product>.py
contracts/floe/<product>.yml
transformations/dbt/<product>/
pipelines/dagster/<product>.py
```

`definitions.py` does not need to change.

The domain inventory discovers the new product from `domain.yaml`.

---

# The product contract

When adding a product, keep these identities aligned:

```text
domain.yaml product id
        │
        ├── Floe config filename
        │
        ├── dlt module filename
        │
        ├── dbt project directory
        │
        └── Dagster module filename
```

For this tutorial:

```text
campaign_performance
```

is consistently used in:

```text
domain.yaml

contracts/floe/campaign_performance.yml

extract/dlt/campaign_performance.py

transformations/dbt/campaign_performance/

pipelines/dagster/campaign_performance.py
```

The `asset_prefix` provides the globally unique runtime identity:

```text
marketing_campaign_performance
```

Consistency here allows OpenLakeForge to discover and assemble the product without a central product registry.

---

# Next steps

You now have a complete OpenLakeForge data product.

From here you can:

* replace the example CSV ingestion with your own dlt source
* add additional Floe validation rules
* create more Silver entities
* build richer dbt Gold marts
* add OpenMetadata governance
* create Superset dashboards
* deploy the same product to AWS or Azure

Useful documentation:

* [Local installation](../setup/local.md)
* [Architecture overview](../architecture/overview.md)
* [Floe validation](../architecture/floe-validation.md)
* [Provider contracts](../architecture/provider-contracts.md)
* [Domain descriptor schema](../schema/domain.schema.json)
* [`olf` deployment tooling](../../tools/olf/README.md)
