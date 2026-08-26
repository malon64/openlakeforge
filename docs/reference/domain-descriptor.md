# Lakehouse and Source descriptor reference

OpenLakeForge's business metadata is declared through two versioned descriptor kinds:

```text
lakehouse_code/lakehouse.yaml              one per repository — domains, products, dashboards
lakehouse_code/bronze/<source>/source.yaml  one per Bronze source — the resources it exposes
```

Together they are the **provider-neutral source of truth for source, domain, and
data-product identity**. OpenLakeForge uses them to discover:

* sources and the Bronze resources they expose
* domains and the products they own
* Bronze resources, Silver tables, and Gold tables
* Dagster job identities
* catalog namespaces
* runtime artifact locations
* governance metadata

Infrastructure-specific values such as Kubernetes resources, catalog implementations,
storage endpoints, and physical credentials do **not** belong in either descriptor. Those
are resolved separately through OpenLakeForge provider contracts.

See [ADR 0026](../adr/0026-medallion-ownership-and-catalog-namespace-contract.md) for the
architectural reasoning behind this ownership split.

---

## Current API version

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
```

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Source
```

`v1alpha3` is required by the canonical OpenLakeForge lakehouse inventory. The
earlier legacy `v1alpha1`/`v1alpha2` `Domain` format and its parsers were
removed once `lakehouse_code/lakehouse.yaml` fully replaced `domains/*/domain.yaml`
(ADR 0026); see [ADR 0021](../adr/0021-domain-descriptor-v1alpha2-inventory.md)
and [ADR 0026](../adr/0026-medallion-ownership-and-catalog-namespace-contract.md)
for that now-historical migration.

`olf init --empty` intentionally writes a transitional `lakehouse.yaml` with
empty `sources` and `domains` lists. It is accepted only by the source/domain/
product scaffolding workflow; `olf check`, deployment, and end-to-end commands
remain strict until scaffolding has created the first runnable product.

---

## Ownership model

```text
Bronze  = source-aligned    lakehouse_code/bronze/<source>/
Silver  = domain-aligned    lakehouse_code/silver/<domain>/
Gold    = product-aligned   lakehouse_code/gold/<product>/
```

A domain declares each Silver table's Source resource mapping. Products select the
domain Silver tables they consume; they do not own ingestion or Bronze paths.

---

# Minimal example

A `Source` with one resource:

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: marketing_platform
displayName: Marketing Platform
description: Marketing campaign performance source system.
status: active
resources:
  - name: campaigns
    description: Raw CSV marketing campaign performance data.
```

A `Lakehouse` with one domain and one product consuming it:

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: openlakeforge
displayName: OpenLakeForge
description: Example lakehouse.
status: active
sources:
  - marketing_platform
domains:
  - name: marketing
    displayName: Marketing
    description: Marketing analytics data products.
    status: active
    silver_tables:
      tables:
        - name: campaigns
          source: marketing_platform
          resource: campaigns
          description: Validated campaign data.
    products:
      - id: campaign_performance
        displayName: Campaign Performance
        description: Campaign spend and conversion performance.
        status: active
        silver_inputs:
          - campaigns
        gold_tables:
          tables:
            - name: mart_campaign_performance
              description: Campaign performance aggregated by channel.
dashboards: []
```

This pair is enough for OpenLakeForge to discover the logical source, domain, and
product, and derive their runtime identities.

---

# `Source` fields

## `apiVersion` / `kind`

**Required.** Must be `openlakeforge.io/v1alpha3` / `Source`.

## `name`

```yaml
name: marketing_platform
```

**Required.** The stable machine-readable source identifier. Must match
`^[a-z][a-z0-9_]*$`, and must match the directory containing the descriptor: for
`lakehouse_code/bronze/marketing_platform/source.yaml`, `name` must be
`marketing_platform`. Source names must be unique across the lakehouse.

## `displayName`, `description`, `status`

**Required.** Human-readable name, description (may be empty), and lifecycle status
string (no fixed enum; typical values are `planned`, `active`, `deprecated`).

## `resources`

```yaml
resources:
  - name: campaigns
    description: Raw CSV marketing campaign performance data.
```

**Required, non-empty.** The Bronze resources this source exposes. Each entry needs a
non-empty `name` (unique within the source, matching `^[a-z][a-z0-9_]*$`) and an
optional `description`. The descriptor does not define the resource's schema — that
belongs to the Floe contract of whichever domain(s) consume it. It also does not contain
a physical path: provider-specific storage paths such as `s3://...` must not be required
in the Source descriptor.

---

# `Lakehouse` fields

## `apiVersion` / `kind`

**Required.** Must be `openlakeforge.io/v1alpha3` / `Lakehouse`.

## `name`, `displayName`, `description`, `status`

**Required.** `name` matches `^[a-z][a-z0-9_]*$`; the others are human-readable, with
`status` an unrestricted lifecycle string as above.

## `sources`

```yaml
sources:
  - crm
  - erp
```

**Required, non-empty, no duplicates.** The set of Bronze source names discovered under
`lakehouse_code/bronze/*/source.yaml`. This list must exactly match the set of
discovered source descriptors — every declared source needs a matching `source.yaml`,
and every `source.yaml` found on disk must be declared here.

## `domains`

**Required, non-empty array.** Each domain owns:

- `name` (required, `^[a-z][a-z0-9_]*$`, unique across the lakehouse)
- `displayName`, `description`, `status` (required, as above)
- `silver_tables` (required domain-owned Source-to-Silver mappings)
- `products` (required array, may be empty — see below)

## `domains[].silver_tables`

```yaml
silver_tables:
  tables:
    - name: campaigns
      source: marketing_platform
      resource: campaigns
      description: Validated campaign data.
```

**Required, non-empty.** Each unique `name` is a domain-owned Silver table.
`source` must reference a declared Source and `resource` must reference one of that
Source's resources. All three identities match `^[a-z][a-z0-9_]*$`.

## `domains[].products`

**Required array; may be empty.** A domain may declare Silver tables ahead of any
product consuming them — for example, seeding an `hr` domain from a Workday source
before deciding which product(s) will use it. `olf domain new` always creates a
domain this way; run `olf product new <domain>/<product>` afterward to add its
first product. A Silver table with no product consuming it yet is still a valid,
loadable part of the inventory: it gets its `<domain>_silver` namespace and its
Floe Silver assets, just no product job selecting it yet.

This is per-domain only: the lakehouse as a whole must still declare at least one
product somewhere, even while individual domains are product-less. Deployment
tooling (local smoke, `check-dbt.sh`, full e2e) all assume at least one product
exists, so a lakehouse where every domain has `products: []` fails descriptor
validation.

Each product entry is:

```yaml
- id: campaign_performance
  displayName: Campaign Performance
  description: Campaign spend and conversion performance.
  status: active
  silver_inputs:
    - campaigns
  gold_tables:
    tables:
      - name: mart_campaign_performance
        description: Campaign performance aggregated by channel.
```

### `id`

**Required.** Stable product identifier, matching `^[a-z][a-z0-9_]*$`, **globally unique
across the whole lakehouse** (not just within its domain — this is the product's only
identity; there is no separate `name`/`asset_prefix` field in v1alpha3). It is used to
discover product implementation files:

```text
id: campaign_performance
```

maps to:

```text
lakehouse_code/silver/<domain>/contracts/floe/<domain>.yml
lakehouse_code/gold/campaign_performance/dbt/
lakehouse_code/pipelines/dagster/campaign_performance.py
```

### `displayName`, `description`, `status`

**Required.** Human-readable, as above.

### `silver_inputs`

```yaml
silver_inputs:
  - campaigns
```

**Required, non-empty, unique array.** Every entry references a table declared in the
enclosing domain's `silver_tables`. The inventory resolves those tables back through
their Source resources for product job selection and downstream publication.

Two products can select the same Silver table without duplicating ingestion or creating
duplicate assets — `order_revenue` and `customer_health` both select `sales.accounts`,
which maps to `crm.accounts`.

### `gold_tables`

```yaml
gold_tables:
  tables:
    - name: mart_campaign_performance
      description: Campaign performance aggregated by channel.
```

**Required, non-empty `tables` array.** Each table entry needs a non-empty `name`
(unique within the product) and optional `description`. Table entries
must **not** contain `fqn` or `fullyQualifiedName` — physical FQNs would couple the
descriptor to a particular provider or environment — and the group itself must not
contain a `schema` key (a deliberately-rejected place someone might be tempted to hardcode
a physical namespace).

---

## `dashboards`

```yaml
dashboards:
  - name: sales_order_revenue
    products: [order_revenue, customer_health]
```

**Required array (may be empty).** Each entry needs `name` (unique, `^[a-z][a-z0-9_]*$`)
and `products` (a non-empty, unique array of declared product IDs). Dashboards are
consumption-aligned, living under
`lakehouse_code/dashboards/superset/<dashboard>/`, independent of any domain or product
code directory.

---

# Derived namespaces and identities

Given the `sales` domain with `order_revenue` and `customer_health` products (the real
seed data), OpenLakeForge derives:

| Resource | Value |
| --- | --- |
| Dagster job (order_revenue) | `order_revenue_pipeline` |
| Dagster module | `lakehouse_code.pipelines.dagster.order_revenue` |
| Bronze namespace (source `crm`) | `crm_bronze` |
| Silver namespace (domain `sales`) | `sales_silver` — **shared by `order_revenue` and `customer_health`** |
| Gold namespace (`order_revenue`) | `order_revenue_gold` |
| Superset source directory (via its dashboard) | `lakehouse_code/dashboards/superset/sales_order_revenue` |
| Floe manifest key | `floe/manifests/sales/sales.manifest.json` |
| Floe report prefix | `floe/reports/sales/` |
| dbt artifact prefix | `run-artifacts/dbt/sales/order_revenue/` |

The catalog namespace contract is deliberately flat (single-level), so it works
identically for Polaris and AWS Glue:

```text
<stage_catalog>.<source>_bronze.<resource>
<stage_catalog>.<domain>_silver.<table>
<stage_catalog>.<product>_gold.<table>
```

For example, in the `dev` stage:

```text
lakehouse_dev.crm_bronze.orders
lakehouse_dev.sales_silver.orders
lakehouse_dev.order_revenue_gold.mart_order_revenue_by_day
```

Ownership follows the medallion layer, not one blanket identity:

```text
Source  -> <source>_bronze
Domain  -> <domain>_silver
Product -> <product>_gold
```

**Silver namespaces are never derived from a product identity.** This is the core fix
this descriptor version makes over v1alpha2, where every product got its own
`<asset_prefix>_silver` namespace even when two products logically belonged to the same
domain.

The inventory then combines these logical identities with the active provider contract
to resolve physical storage and catalog locations. See
[Provider contracts](../architecture/provider-contracts.md).

---

# Provider-neutrality rules

Neither `lakehouse.yaml` nor any `source.yaml` describes *where* OpenLakeForge is
deployed. Do not encode environment-specific values such as EKS cluster names, AKS
resource groups, Kubernetes namespaces, AWS/Azure account or subscription IDs, S3 or
SeaweedFS endpoints, Polaris URLs, Glue database FQNs, PostgreSQL hosts, credentials, or
secrets.

For example, do not write:

```yaml
gold_tables:
  schema: glue.production.order_revenue_gold
```

Instead declare only the logical table name and let OpenLakeForge derive the physical
relation from the active provider contract. This separation is what allows the same
lakehouse definition to be deployed locally, to AWS, or to Azure.

---

# Identity constraints

The canonical inventory enforces, beyond basic YAML structure:

- **Source names** must be unique and must match their `source.yaml`'s containing
  directory; the lakehouse's `sources` list must exactly match discovered descriptors.
- **Domain names** must be unique across the lakehouse.
- **Product IDs** must be unique across the *entire* lakehouse (not just within their
  domain — there is no per-domain product namespace in v1alpha3).
- **Dashboard names** must be unique, and each `product` reference must resolve to a
  declared product ID.
- Within one domain, Silver mappings are unique and resolve to declared Source resources.
  Within one product, `silver_inputs` and Gold table names are unique.

---

# Complete example

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: openlakeforge
displayName: OpenLakeForge
description: Seed lakehouse for the OpenLakeForge v1 proof-of-concept data path.
status: planned
sources:
  - crm
  - erp
domains:
  - name: sales
    displayName: Sales
    description: Sales proof-of-concept domain.
    status: planned
    silver_tables:
      tables:
        - {name: orders, source: crm, resource: orders}
        - {name: order_lines, source: crm, resource: order_lines}
        - {name: products, source: crm, resource: products}
        - {name: channels, source: crm, resource: channels}
        - {name: promotions, source: crm, resource: promotions}
        - {name: accounts, source: crm, resource: accounts}
        - {name: subscriptions, source: crm, resource: subscriptions}
        - {name: support_tickets, source: crm, resource: support_tickets}
        - {name: nps_responses, source: crm, resource: nps_responses}
    products:
      - id: order_revenue
        displayName: Sales Order Revenue
        description: Curated revenue, channel, discount, and product margin marts.
        status: planned
        silver_inputs: [orders, order_lines, products, channels, promotions, accounts]
        gold_tables:
          tables:
            - name: mart_order_revenue_by_day
              description: Daily order revenue, units, and discount amount by region.
            - name: mart_order_revenue_by_channel
              description: Net revenue and discount amount by sales channel and promotion type.
            - name: mart_order_revenue_margin_by_product
              description: Product revenue, cost, and gross margin for fulfilled order lines.
            - name: mart_order_revenue_by_account_segment
              description: Net revenue and order count by account segment and region.
      - id: customer_health
        displayName: Sales Customer Health
        description: Curated customer health, churn risk, support SLA, and NPS marts.
        status: planned
        silver_inputs: [accounts, subscriptions, support_tickets, nps_responses]
        gold_tables:
          tables:
            - name: mart_customer_health_score
              description: Account-level health score using subscription, support, and NPS signals.
            - name: mart_churn_risk_by_segment
              description: Churn risk and ARR exposure grouped by account segment and region.
            - name: mart_support_sla_by_customer
              description: Support volume, resolution hours, and SLA rate by customer and priority.
dashboards:
  - name: sales_order_revenue
    products: [order_revenue]
  - name: sales_customer_health
    products: [customer_health]
```

`order_revenue` and `customer_health` both consume `crm.accounts` and both validate into
the same `sales_silver` namespace — that is the shared-Bronze, domain-owned-Silver
pattern this descriptor version exists to make possible.

Its companion `lakehouse_code/bronze/crm/source.yaml`:

```yaml
apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: Customer relationship management source system shared by the Sales data products.
status: planned
resources:
  - name: accounts
    description: Raw CSV customer accounts.
  - name: channels
    description: Raw CSV sales channel dimension.
  # ...one entry per resource any Sales product consumes...
```

---

# Relationship with implementation files

The descriptors define the inventory; implementation lives under the medallion-owned
directories, not nested under a domain:

```text
lakehouse_code/
├── lakehouse.yaml
├── bronze/
│   └── crm/
│       ├── source.yaml
│       ├── examples/*.csv
│       └── dlt/crm.py
├── silver/
│   └── sales/
│       └── contracts/floe/
│           ├── sales.yml
│           └── manifests/sales.manifest.json
├── gold/
│   ├── order_revenue/dbt/
│   └── customer_health/dbt/
├── dashboards/
│   └── superset/
│       ├── order_revenue/
│       └── customer_health/
└── pipelines/
    └── dagster/
        ├── order_revenue.py
        └── customer_health.py
```

The descriptors do not replace those implementation files — they provide the common
logical inventory the files share.

---

# Validation

Run descriptor and provider-contract validation from the repository root:

```bash
make check-contracts
```

This checks `lakehouse_code/lakehouse.yaml` and every
`lakehouse_code/bronze/*/source.yaml` through both the canonical OpenLakeForge model and
their versioned JSON Schemas:

```text
docs/schema/lakehouse.schema.json
docs/schema/source.schema.json
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

## Source list doesn't match discovered descriptors

Invalid: `lakehouse.yaml` declares `sources: [crm, erp]` but only
`lakehouse_code/bronze/crm/source.yaml` exists on disk (or vice versa — an undeclared
`source.yaml` exists). Both sides must match exactly.

## Source directory mismatch

Given `lakehouse_code/bronze/marketing_platform/source.yaml`, this is invalid:

```yaml
name: marketing
```

Use `name: marketing_platform`.

## Invalid identifier

Invalid: `id: campaign-performance`. Valid: `id: campaign_performance`.

## Duplicate product ID

Two products cannot both declare `id: order_revenue`, even across different domains —
product IDs are globally unique in v1alpha3.

## Silver mapping resource not declared by its source

Invalid: a domain Silver table maps to `{source: crm, resource: made_up_resource}` when
`crm/source.yaml` has no `made_up_resource` entry.

## Empty table groups

Invalid: `silver_tables: {tables: []}`. Every domain must expose at least one Silver
table, and every product must select at least one Silver input and declare one Gold table.

## Physical FQN in descriptor

Invalid:

```yaml
gold_tables:
  tables:
    - name: revenue
      fullyQualifiedName: glue.production.revenue
```

Use only the logical name.

---

# Changing a descriptor

Some descriptor changes affect runtime identity and should be treated as migrations
rather than cosmetic edits. Be careful when changing:

```text
source name
domain name
product id
Silver table names
Gold table names
```

Changing a domain `name`, for example, changes the derived Silver namespace for every
product in that domain. Changing a product `id` changes its Gold namespace and every
derived artifact path. For deployed environments, plan these changes as data/catalog
migrations rather than simple metadata updates.

Changes to `displayName`, `description`, and `status` are generally metadata-only.

---

# Related documentation

* [Build your first data product](../getting-started/first-data-product.md)
* [ADR 0026 — medallion ownership and catalog namespace contract](../adr/0026-medallion-ownership-and-catalog-namespace-contract.md)
* [Lakehouse JSON Schema](../schema/lakehouse.schema.json)
* [Source JSON Schema](../schema/source.schema.json)
* [Provider contracts](../architecture/provider-contracts.md)
* [Architecture overview](../architecture/overview.md)
* [`olf` CLI documentation](../../tools/olf/README.md)
