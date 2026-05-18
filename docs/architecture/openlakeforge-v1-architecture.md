# OpenLakeForge v1 Architecture

This diagram is the editable Mermaid source of the current v1 target
architecture. It mirrors the original visual architecture reference in
`docs/assets/openlakeforge_archi.png`, while keeping the architecture in a
reviewable text format.

```mermaid
flowchart LR
  subgraph sources [1. Sources]
    direction TB
    src_db[Databases]
    src_api[APIs]
    src_saas[SaaS Applications]
    src_files[Files and Logs]
    src_iot[IoT and Events]
    src_stream[Streaming - future]
  end

  subgraph ingest [2. Extraction and Ingestion]
    direction TB
    dlt[dlt - ELT pipelines]
    airbyte[Airbyte - connectors]
    python[Python - custom extractors]
  end

  subgraph foundation [3. Storage and Open Table Foundation]
    direction TB
    seaweed[SeaweedFS - S3 compatible object storage]

    subgraph table_foundation [Open Table Foundation]
      direction LR
      iceberg[Apache Iceberg - open table format]
      polaris[Apache Polaris - REST catalog]
      iceberg <--> polaris
    end

    subgraph zones [Data Zones in Object Storage]
      direction LR
      bronze[Bronze - raw immutable]
      silver[Silver - validated and conformed]
      gold[Gold - business ready marts]
    end

    seaweed --> bronze
    seaweed --> silver
    seaweed --> gold
    bronze --> iceberg
    silver --> iceberg
    gold --> iceberg
    polaris --> iceberg
  end

  subgraph quality [4. Data Contract and Quality]
    direction TB
    floe[Floe - contracts and quality]
    floe_flow[Bronze to Silver Iceberg via Polaris]
    floe --> floe_flow
  end

  subgraph transform [5. Transformation]
    direction TB
    dbt[dbt - Silver to Gold transformations]
    duckdb_dbt[dbt-duckdb - local developer execution]
    dbt --> duckdb_dbt
  end

  subgraph query [6. Analytics and Query Layer]
    direction TB
    trino[Trino - distributed SQL]
    duckdb[DuckDB - local query and preview]
    trino_features[ANSI SQL, federation, concurrency, scale]
    trino --> trino_features
  end

  subgraph interfaces [7. Consumption and Interfaces]
    direction TB
    superset[Apache Superset - dashboards and SQL Lab]
    dagster[Dagster - asset orchestration]
    notebooks[Python and SQL clients]
  end

  subgraph governance [8. Governance and Catalog]
    direction TB
    openmetadata[OpenMetadata - catalog and governance]
    marquez[Marquez - OpenLineage metadata store]
    governance_features[Discovery, lineage, owners, quality]
  end

  subgraph platform [Platform Services on Kubernetes]
    direction LR
    subgraph iac [Infrastructure as Code]
      direction TB
      terraform[Terraform]
      helm[Helm]
      kind[kind local Kubernetes]
    end

    subgraph observability [Observability]
      direction TB
      prometheus[Prometheus]
      grafana[Grafana]
      loki[Loki]
      alertmanager[Alertmanager]
    end

    subgraph lineage [Lineage and Telemetry]
      direction TB
      openlineage[OpenLineage]
      otel[OpenTelemetry]
    end

    subgraph security [Security and Authentication]
      direction TB
      keycloak[Keycloak]
      cert_manager[cert-manager]
      vault[Vault]
    end

    subgraph reliability [Backup and Reliability]
      direction TB
      velero[Velero]
      k8s[Kubernetes]
    end
  end

  src_db --> dlt
  src_api --> dlt
  src_saas --> airbyte
  src_files --> python
  src_iot --> python
  src_stream --> airbyte

  dlt --> bronze
  airbyte --> bronze
  python --> bronze

  bronze --> floe
  floe_flow --> silver
  silver --> dbt
  dbt --> gold
  silver --> trino
  gold --> trino
  trino --> superset
  trino --> notebooks

  dagster --> dlt
  dagster --> floe
  dagster --> dbt
  dagster --> openlineage
  openlineage --> marquez
  marquez --> governance_features
  openmetadata --> governance_features
  trino --> openmetadata
  floe --> openmetadata
  dbt --> openmetadata

  terraform -. deploys .-> seaweed
  terraform -. deploys .-> polaris
  terraform -. deploys .-> trino
  helm -. packages .-> seaweed
  helm -. packages .-> polaris
  helm -. packages .-> trino
  kind -. runs .-> terraform
```

The complete Mermaid source with styling classes is kept in
`openlakeforge-v1-architecture.mmd`.
