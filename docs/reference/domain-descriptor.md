# Domain descriptor reference

Every OpenLakeForge domain is declared through a versioned:

```text
domains/<domain>/domain.yaml
```

The descriptor is the **provider-neutral source of truth for domain and data-product identity**.

OpenLakeForge uses it to discover:

* domains
* data products
* Bronze entities
* Silver tables
* Gold tables
* Dagster job identities
* catalog namespaces
* runtime artifact locations
* governance metadata

Infrastructure-specific values such as Kubernetes resources, catalog implementations, storage endpoints, and physical credentials do **not** belong in the descriptor.

Those are resolved separately through OpenLakeForge provider contracts.

---

## Current API version

The current descriptor version is:

```yaml
apiVersion: openlakeforge.io/v1alpha2
kind: Domain
```

`v1alpha2` is required by the canonical OpenLakeForge domain inventory.

Descriptors using the legacy `v1alpha1` format can still be parsed for migration purposes, but they cannot be used by current inventory consumers.

See:

➡️ [v1alpha1 → v1alpha2 migration](../migrations/domain-v1alpha1-to-v1alpha2.md)

---

# Minimal example

A domain containing a single data product looks like:

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
    description: Campaign spend and conversion performance.
    status: active

    asset_prefix: marketing_campaign_performance

    bronze:
      - name: campaigns
        path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns
        description: Raw campaign data.

    silver_tables:
      tables:
        - name: campaigns
          description: Validated campaign data.

    gold_tables:
      tables:
        - name: mart_campaign_performance
          description: Campaign performance aggregated by channel.
```

This descriptor is enough for OpenLakeForge to discover the logical product and derive its runtime identities.

---

# Domain fields

## `apiVersion`

```yaml
apiVersion: openlakeforge.io/v1alpha2
```

**Required.**

Identifies the descriptor contract version.

Current value:

```text
openlakeforge.io/v1alpha2
```

---

## `kind`

```yaml
kind: Domain
```

**Required.**

Must be exactly:

```text
Domain
```

---

## `name`

```yaml
name: marketing
```

**Required.**

The stable machine-readable domain identifier.

It must match:

```text
^[a-z][a-z0-9_]*$
```

Valid examples:

```text
marketing
supply_chain
customer_success
```

Invalid examples:

```text
Marketing
supply-chain
123_sales
```

The value must also match the directory containing the descriptor.

For:

```text
domains/marketing/domain.yaml
```

the descriptor must contain:

```yaml
name: marketing
```

---

## `displayName`

```yaml
displayName: Marketing
```

**Required.**

Human-readable domain name.

Unlike `name`, this field does not need to follow identifier naming rules.

---

## `description`

```yaml
description: Marketing analytics data products.
```

**Required.**

Human-readable description of the domain.

The value must be a string, but it may be empty.

A meaningful description is strongly recommended because the same metadata can later be surfaced through governance tooling.

---

## `status`

```yaml
status: active
```

**Required.**

Human-readable lifecycle status.

The current schema does not restrict this field to a fixed enum.

Typical values might include:

```text
planned
active
deprecated
```

Projects should use a consistent status vocabulary across domains.

---

## `data_products`

```yaml
data_products:
  - ...
```

**Required.**

Contains the products owned by the domain.

Each product must conform to the product contract described below.

Although the schema represents this as an array, the OpenLakeForge inventory requires at least one discoverable product.

---

# Data product fields

A typical product entry is:

```yaml
data_products:
  - id: campaign_performance
    name: marketing_campaign_performance
    displayName: Campaign Performance
    description: Campaign spend and conversion performance.
    status: active
    asset_prefix: marketing_campaign_performance

    bronze:
      - name: campaigns
        path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns

    silver_tables:
      tables:
        - name: campaigns

    gold_tables:
      tables:
        - name: mart_campaign_performance
```

---

## `id`

```yaml
id: campaign_performance
```

**Required.**

Stable product identifier within the domain.

It must match:

```text
^[a-z][a-z0-9_]*$
```

The ID is used to discover product implementation files.

For example:

```text
id: campaign_performance
```

maps naturally to:

```text
contracts/floe/campaign_performance.yml
extract/dlt/campaign_performance.py
transformations/dbt/campaign_performance/
pipelines/dagster/campaign_performance.py
```

Product IDs must be unique inside a domain.

---

## `name`

```yaml
name: marketing_campaign_performance
```

**Required.**

Logical name of the data product.

Unlike `id`, the current schema only requires this to be a non-empty string.

However, using a stable identifier-style name is recommended.

A useful convention is:

```text
<domain>_<product>
```

For example:

```text
marketing_campaign_performance
sales_order_revenue
supply_chain_inventory_reliability
```

Product names must be globally unique across the OpenLakeForge inventory.

---

## `displayName`

```yaml
displayName: Campaign Performance
```

**Required.**

Human-readable product name.

---

## `description`

```yaml
description: Campaign spend and conversion performance.
```

**Required.**

Human-readable description of the product.

---

## `status`

```yaml
status: active
```

**Required.**

Lifecycle status of the data product.

As with domain status, OpenLakeForge currently requires a non-empty string but does not enforce an enum.

---

# `asset_prefix`

```yaml
asset_prefix: marketing_campaign_performance
```

**Required.**

The globally unique runtime identity for the product.

It must match:

```text
^[a-z][a-z0-9_]*$
```

OpenLakeForge derives several runtime names from this value.

For:

```yaml
asset_prefix: marketing_campaign_performance
```

the inventory derives:

| Resource         | Derived value                             |
| ---------------- | ----------------------------------------- |
| Dagster job      | `marketing_campaign_performance_pipeline` |
| Silver namespace | `marketing_campaign_performance_silver`   |
| Gold namespace   | `marketing_campaign_performance_gold`     |

`asset_prefix` values must be globally unique across all domains.

A recommended convention is:

```text
<domain>_<product>
```

This makes collisions unlikely and makes runtime resources easy to identify.

---

# Bronze entities

Each product must declare at least one Bronze entity.

```yaml
bronze:
  - name: campaigns
    path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns
    description: Raw campaign data.
```

Bronze represents the raw landing layer owned by ingestion.

---

## `bronze[].name`

```yaml
name: campaigns
```

**Required.**

Logical entity name.

Bronze entity names must be unique inside the product.

The descriptor does not define the entity's schema. Technical validation belongs to the Floe contract.

---

## `bronze[].path`

```yaml
path: s3://lakehouse-bronze/marketing/campaign_performance/campaigns
```

**Required.**

Declared Bronze location for the entity.

The descriptor requires a non-empty string.

Keep this value logical and portable. Do not embed credentials, provider-specific endpoints, account IDs, or environment-specific connection information.

Runtime ingestion and Floe configuration determine how the path is resolved in a particular environment.

---

## `bronze[].description`

```yaml
description: Raw campaign data.
```

Optional human-readable description.

---

# Silver tables

Each product must declare at least one Silver table:

```yaml
silver_tables:
  tables:
    - name: campaigns
      description: Validated campaign data.
```

Silver tables represent technically validated Iceberg tables produced by Floe.

---

## `silver_tables.tables[].name`

```yaml
name: campaigns
```

**Required.**

Logical Silver table name.

Names must be unique inside the product's Silver table list.

---

## `silver_tables.tables[].description`

```yaml
description: Validated campaign data.
```

Optional human-readable description.

---

## Do not specify physical schemas

This is deliberately invalid:

```yaml
silver_tables:
  schema: lakehouse_dev.marketing_campaign_performance_silver
  tables:
    - name: campaigns
```

OpenLakeForge derives the Silver namespace from:

```text
<asset_prefix>_silver
```

The catalog/database component is supplied by the active provider contract.

Similarly, table entries must not contain:

```yaml
fqn:
```

or:

```yaml
fullyQualifiedName:
```

Physical FQNs would couple the product descriptor to a particular provider or environment.

---

# Gold tables

Each product must declare at least one Gold table:

```yaml
gold_tables:
  tables:
    - name: mart_campaign_performance
      description: Campaign performance by channel.
```

Gold represents business-ready Iceberg tables produced by dbt.

---

## `gold_tables.tables[].name`

```yaml
name: mart_campaign_performance
```

**Required.**

Logical Gold model/table name.

Names must be unique within the product's Gold table list.

The name should correspond to the dbt model materialized by the product.

For example:

```text
models/gold/mart_campaign_performance.sql
```

corresponds to:

```yaml
gold_tables:
  tables:
    - name: mart_campaign_performance
```

---

## Derived Gold relation

With:

```yaml
asset_prefix: marketing_campaign_performance

gold_tables:
  tables:
    - name: mart_campaign_performance
```

OpenLakeForge derives:

```text
marketing_campaign_performance_gold.mart_campaign_performance
```

The physical catalog/database is resolved separately.

For example, a local deployment may expose it through Trino as:

```text
iceberg.marketing_campaign_performance_gold.mart_campaign_performance
```

while the underlying catalog implementation can differ between providers.

---

# Derived product identities

The descriptor deliberately contains logical identities rather than all runtime names.

Consider:

```yaml
name: marketing

data_products:
  - id: campaign_performance
    name: marketing_campaign_performance
    asset_prefix: marketing_campaign_performance
```

OpenLakeForge derives:

| Resource                      | Value                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| Product implementation module | `domains.marketing.pipelines.dagster.campaign_performance`                         |
| Dagster job                   | `marketing_campaign_performance_pipeline`                                          |
| Silver namespace              | `marketing_campaign_performance_silver`                                            |
| Gold namespace                | `marketing_campaign_performance_gold`                                              |
| Superset source directory     | `domains/marketing/reports/superset/campaign_performance`                          |
| Floe manifest key             | `floe/manifests/marketing/campaign_performance/campaign_performance.manifest.json` |
| Floe report prefix            | `floe/reports/marketing/campaign_performance/`                                     |
| dbt artifact prefix           | `run-artifacts/dbt/marketing/campaign_performance/`                                |

The inventory then combines those identities with the active provider contract to resolve physical storage and catalog locations.

---

# Provider-neutrality rules

`domain.yaml` describes **what the data product is**, not where OpenLakeForge is deployed.

Do not encode environment-specific values such as:

```text
EKS cluster names
AKS resource groups
Kubernetes namespaces
AWS account IDs
Azure subscription IDs
S3 endpoints
SeaweedFS endpoints
Polaris URLs
Glue database FQNs
PostgreSQL hosts
credentials
secrets
```

For example, do not write:

```yaml
gold_tables:
  schema: glue.production.marketing_campaign_performance_gold
```

Instead declare:

```yaml
gold_tables:
  tables:
    - name: mart_campaign_performance
```

and let OpenLakeForge derive the physical relation from the active provider contract.

This separation is what allows the same data-product definition to be deployed locally, to AWS, or to Azure.

See:

➡️ [Provider contracts](../architecture/provider-contracts.md)

---

# Identity constraints

The canonical inventory enforces additional constraints beyond basic YAML structure.

## Domain names

Domain names must be unique and must match their containing directory.

```text
domains/sales/domain.yaml
        │
        └── name: sales
```

---

## Product IDs

A product `id` must be unique within its domain.

This is valid:

```text
sales/order_revenue
marketing/order_revenue
```

because the IDs belong to different domains.

---

## Product names

`name` must be globally unique across the complete domain inventory.

Avoid:

```yaml
# domains/sales/domain.yaml
name: revenue

# domains/marketing/domain.yaml
name: revenue
```

---

## Asset prefixes

`asset_prefix` must also be globally unique.

This prevents collisions between:

* Dagster jobs
* Silver namespaces
* Gold namespaces
* runtime artifacts

---

## Table names

Within a single product:

* Bronze entity names must not contain duplicates.
* Silver table names must not contain duplicates.
* Gold table names must not contain duplicates.

---

# Optional metadata and extensions

The `v1alpha2` schema intentionally allows additional fields.

For example, existing descriptors may contain metadata such as:

```yaml
domainType: Source-aligned
owners: []

medallion:
  bronze:
    owner: ingestion
  silver:
    owner: floe
  gold:
    owner: dbt
```

These fields can enrich domain documentation and governance metadata.

However, they are **not part of the minimum canonical inventory contract** unless explicitly documented elsewhere.

Consumers should not assume that arbitrary extension fields affect runtime behavior.

The stable inventory contract is centered on:

```text
domain identity
product identity
asset_prefix
Bronze entities
Silver tables
Gold tables
```

---

# Optional product metadata

The schema also accepts optional product fields such as:

```yaml
domain:
domains:
assets:
```

These may be used for additional logical metadata.

When using `assets`, each item must be a logical asset object:

```yaml
assets:
  - name: customer
    type: table
```

Do not specify physical FQNs:

```yaml
assets:
  - name: customer
    fqn: production.catalog.customer
```

`type`, when provided, is currently limited to:

```text
table
```

These optional fields are not required for normal `v1alpha2` product discovery.

---

# Complete example

```yaml
apiVersion: openlakeforge.io/v1alpha2
kind: Domain

name: sales
displayName: Sales
description: Data products owned by the Sales domain.
status: active

domainType: Source-aligned
owners: []

medallion:
  bronze:
    owner: ingestion
    description: Raw immutable landing zone.
  silver:
    owner: floe
    description: Technically validated Iceberg tables.
  gold:
    owner: dbt
    description: Business-ready marts.

data_products:
  - id: order_revenue
    name: sales_order_revenue
    displayName: Sales Order Revenue
    description: Revenue and margin analytics for sales orders.
    status: active

    asset_prefix: sales_order_revenue

    bronze:
      - name: orders
        path: s3://lakehouse-bronze/sales/order_revenue/orders
        description: Raw order headers.

      - name: order_lines
        path: s3://lakehouse-bronze/sales/order_revenue/order_lines
        description: Raw order lines.

      - name: products
        path: s3://lakehouse-bronze/sales/order_revenue/products
        description: Raw product catalog.

    silver_tables:
      tables:
        - name: orders
          description: Validated order headers.

        - name: order_lines
          description: Validated order lines.

        - name: products
          description: Validated product dimension.

    gold_tables:
      tables:
        - name: mart_order_revenue_by_day
          description: Daily sales revenue.

        - name: mart_order_revenue_margin_by_product
          description: Revenue and gross margin by product.
```

---

# Relationship with product files

The descriptor defines the inventory, while the product implementation remains under the domain capability directories.

For:

```yaml
name: sales

data_products:
  - id: order_revenue
```

the expected implementation is typically:

```text
domains/sales/
├── domain.yaml
│
├── examples/
│   └── raw/
│       └── order_revenue/
│
├── extract/
│   └── dlt/
│       └── order_revenue.py
│
├── contracts/
│   └── floe/
│       └── order_revenue.yml
│
├── transformations/
│   └── dbt/
│       └── order_revenue/
│
├── pipelines/
│   └── dagster/
│       └── order_revenue.py
│
└── reports/
    └── superset/
        └── order_revenue/
```

The descriptor does not replace those implementation files.

It provides the common logical inventory they share.

---

# Validation

Run descriptor and provider-contract validation from the repository root:

```bash
make check-contracts
```

This checks the descriptors through both:

1. the canonical OpenLakeForge domain model
2. the versioned JSON Schema

The JSON Schema is available at:

```text
docs/schema/domain.schema.json
```

For broader repository validation:

```bash
make check-structure
make check-contracts
make check-dbt
```

or run the complete release validation:

```bash
make release-check
```

---

# Common validation errors

## Domain does not match directory

Given:

```text
domains/marketing/domain.yaml
```

this is invalid:

```yaml
name: sales
```

Use:

```yaml
name: marketing
```

---

## Invalid identifier

Invalid:

```yaml
id: campaign-performance
```

Valid:

```yaml
id: campaign_performance
```

---

## Duplicate asset prefix

Two products cannot declare:

```yaml
asset_prefix: sales_revenue
```

even when they belong to different domains.

`asset_prefix` is globally unique.

---

## Empty table groups

Invalid:

```yaml
silver_tables:
  tables: []
```

Every `v1alpha2` product must expose at least one Silver table and one Gold table.

---

## Physical FQN in descriptor

Invalid:

```yaml
gold_tables:
  tables:
    - name: revenue
      fullyQualifiedName: glue.production.revenue
```

Use only the logical name:

```yaml
gold_tables:
  tables:
    - name: revenue
```

---

# Changing a descriptor

Some descriptor changes affect runtime identity and should be treated as migrations rather than cosmetic edits.

In particular, be careful when changing:

```text
domain name
product id
product name
asset_prefix
Silver table names
Gold table names
```

Changing `asset_prefix`, for example, changes derived:

```text
Dagster job names
Silver namespaces
Gold namespaces
runtime artifact paths
```

For deployed environments, plan these changes as data/catalog migrations rather than simple metadata updates.

Changes to:

```text
displayName
description
status
```

are generally metadata-only.

---

# Related documentation

* [Build your first data product](../getting-started/first-data-product.md)
* [Domain JSON Schema](../schema/domain.schema.json)
* [v1alpha1 → v1alpha2 migration](../migrations/domain-v1alpha1-to-v1alpha2.md)
* [Provider contracts](../architecture/provider-contracts.md)
* [Architecture overview](../architecture/overview.md)
* [`olf` CLI documentation](../../tools/olf/README.md)
