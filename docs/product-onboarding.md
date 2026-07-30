# Product Onboarding

Adding a data product to OpenLakeForge touches only `domains/<domain>/**`. No
shared Terraform, Helm, or platform code changes. The platform derives every
physical name, job, namespace, manifest key, Dagster code location, and
end-to-end expectation from the domain descriptors.

`domains/marketing/campaign_performance` is the reference: it was created
entirely by `olf product scaffold`, in a domain that did not previously exist.

## What is derived, and from what

Everything below comes from `domains/<domain>/domain.yaml`. Never write physical
names into the descriptor — the validator rejects `fqn`/`fullyQualifiedName`.

| Derived value | Convention |
| --- | --- |
| Asset prefix | `<domain>_<product>` |
| Dagster job | `<domain>_<product>_pipeline` |
| Silver namespace | `<domain>_<product>_silver` |
| Gold namespace | `<domain>_<product>_gold` |
| Floe manifest key | `floe/manifests/<domain>/<product>/<product>.manifest.json` |
| Dagster code location | `<domain>-dagster` loading `domains.<domain>.definitions` |
| Dashboard slug | slugified product `displayName` |

Consumers of that derivation: `tools/olf/olf/descriptors.py` (`discover_domains`,
`discover_products`), `tools/olf/olf/e2e.py`, `infra/terraform/modules/domains`,
`scripts/test/check-structure.sh`, and `scripts/test/check-project-code.sh`.

## Onboarding a product

### 1. Write a scaffold spec

The spec is a scratch input — it is not committed. Minimal shape:

```yaml
domain: marketing                      # new or existing
domain_display_name: Marketing         # only used when the domain is new
domain_description: Marketing domain.
owner: marketing
product: campaign_performance
product_display_name: Marketing Campaign Performance
product_description: Curated campaign spend and conversion marts.

sources:                               # one per Bronze CSV -> Silver table
  - name: campaigns
    description: Raw CSV marketing campaigns.
    primary_key: [campaign_id]
    columns:
      - {name: campaign_id, type: string}
      - {name: budget_amount, type: double}
    example_rows:
      - ["CMP-001", "25000.00"]

marts:                                 # one per Gold dbt model
  - name: mart_campaign_spend_by_channel
    description: Daily spend by channel.
    dttm_col: spend_date               # optional; must be a declared column
    columns:
      - {name: spend_date, type: date}
      - {name: channel, type: string}
      - {name: spend_amount, type: double}
    metrics:
      - {name: sum__spend_amount, expression: SUM(spend_amount)}
    chart:                             # optional Superset chart
      name: Campaign Spend by Channel
      viz_type: echarts_timeseries_bar # bar | line | pie | table only
      x_axis: spend_date
      groupby: [channel]
      metrics: [sum__spend_amount]
    sql: |
      select ... from {{ source('silver', 'ad_spend') }}
```

Column `type` values map to Iceberg/Superset types: `string`, `integer`, `long`,
`double`, `decimal`, `date`, `timestamp`, `boolean`.

Charts may only reference columns and metrics the same mart declares, and only
the four Superset viz types above. The scaffold rejects violations up front, with
the same rule `check-structure.sh` enforces on the generated bundle.

### 2. Scaffold

```bash
uv run --project tools/olf olf product scaffold --spec /tmp/my-product.spec.yaml
```

Existing files are skipped unless you pass `--force`; `domain.yaml` and
`definitions.py` are always rewritten because they are the discovery inputs.
Generated per product:

```
domains/<domain>/domain.yaml                  # created, or product appended
domains/<domain>/{__init__.py,README.md,definitions.py}
domains/<domain>/contracts/floe/<product>.yml
domains/<domain>/examples/raw/<product>/*.csv
domains/<domain>/extract/dlt/<product>.py
domains/<domain>/pipelines/dagster/<product>.py
domains/<domain>/transformations/dbt/<product>/...
domains/<domain>/reports/superset/<product>/...
```

### 3. Generate the Floe manifest

Required, and not something the scaffold can do: the manifest is a generated
build artifact produced by the pinned Floe version.

```bash
make floe-manifest
```

Needs Docker (or the pinned Floe CLI). Until it runs, the product's Dagster code
location cannot load, and `make check-project-code` reports it as `PENDING`.
Commit the generated `contracts/floe/manifests/<product>.manifest.json`.

### 4. Validate

```bash
make check-structure check-contracts check-infra check-project-code check-dbt
```

```bash
uv run --project tools/olf pytest tools/olf/tests
```

Then deploy and run the full suite:

```bash
make local-up && make local-e2e
```

The e2e suite discovers the new product automatically — its job, Gold marts,
Silver tables, Superset dashboard, and OpenMetadata data product are all
asserted without any per-product code.

## Removing a product

1. Delete `domains/<domain>/transformations/dbt/<product>/`,
   `reports/superset/<product>/`, `examples/raw/<product>/`,
   `extract/dlt/<product>.py`, `pipelines/dagster/<product>.py`,
   `contracts/floe/<product>.yml`, and its generated manifest.
2. Remove the product's entry from `data_products` in `domains/<domain>/domain.yaml`.
3. Remove the `<product>` import and `.defs` entry from
   `domains/<domain>/definitions.py`. Delete the whole `domains/<domain>/`
   directory if it was the last product in that domain.
4. Re-run the validation commands above. Discovery, Terraform, and the e2e
   expectations drop the product with no shared-code change.

Terraform no longer creates the product's Polaris/Glue namespaces on the next
apply, but it does not delete existing Silver/Gold data or Superset assets —
remove those out of band if the removal is meant to be destructive.
