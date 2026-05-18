# OpenLakeForge v1 Architecture

This diagram is the editable Mermaid source of the current v1 target
architecture. It mirrors the original visual architecture reference in
`docs/assets/openlakeforge_archi.png`, while keeping the architecture in a
reviewable text format.

```mermaid
flowchart LR
  subgraph sources["1. Sources"]
    direction TB
    src_db["Databases"]
    src_api["APIs"]
    src_saas["SaaS Apps"]
    src_files["Files / Logs"]
    src_iot["IoT / Events"]
    src_stream["Streaming<br/>(future)"]
  end

  subgraph ingest["2. Extraction & Ingestion"]
    direction TB
    dlt["dlt<br/>Lightweight ELT pipelines"]
    airbyte["Airbyte<br/>Pre-built connectors"]
    python["Python<br/>Custom extractors"]
  end

  subgraph foundation["3. Storage & Open Table Foundation"]
    direction TB
    seaweed["SeaweedFS<br/>S3-compatible object storage"]

    subgraph table_foundation["Open Table Foundation"]
      direction LR
      iceberg["Apache Iceberg<br/>Open table format"]
      polaris["Apache Polaris<br/>REST catalog"]
      iceberg <--> polaris
    end

    subgraph zones["Data Zones in Object Storage"]
      direction LR
      bronze["Bronze<br/>Raw immutable"]
      silver["Silver<br/>Validated and conformed<br/>(Floe output)"]
      gold["Gold<br/>Business-ready marts<br/>(dbt models)"]
    end

    seaweed --> zones
    zones --> iceberg
    polaris --> zones
  end

  subgraph quality["4. Data Contract & Quality"]
    direction TB
    floe["Floe<br/>Data contracts and quality"]
    floe_flow["Bronze -> Silver Iceberg<br/>via Polaris REST catalog"]
    floe --> floe_flow
  end

  subgraph transform["5. Transformation"]
    direction TB
    dbt["dbt<br/>Transform Silver -> Gold"]
    duckdb_dbt["dbt-duckdb<br/>Local developer execution"]
    dbt --> duckdb_dbt
  end

  subgraph query["6. Analytics & Query Layer"]
    direction TB
    trino["Trino<br/>Distributed SQL query engine"]
    duckdb["DuckDB<br/>Local query and preview"]
    trino_features["ANSI SQL<br/>Federated queries<br/>High concurrency<br/>Performance and scale"]
    trino --> trino_features
  end

  subgraph interfaces["7. Consumption & Interfaces"]
    direction TB
    superset["Apache Superset<br/>Dashboards and SQL Lab"]
    dagster["Dagster<br/>Asset orchestration"]
    notebooks["Python / SQL clients<br/>Developer workflows"]
  end

  subgraph governance["8. Governance & Catalog"]
    direction TB
    openmetadata["OpenMetadata<br/>Data catalog and governance"]
    marquez["Marquez<br/>OpenLineage metadata store"]
    governance_features["Discovery<br/>Lineage<br/>Glossary and tags<br/>Owners and teams<br/>Data quality"]
  end

  subgraph platform["Platform Services on Kubernetes"]
    direction LR

    subgraph iac["Infrastructure as Code"]
      direction TB
      terraform["Terraform<br/>Provisioning and contracts"]
      helm["Helm<br/>Package management"]
      kind["kind<br/>Local Kubernetes"]
    end

    subgraph observability["Observability"]
      direction TB
      prometheus["Prometheus<br/>Metrics"]
      grafana["Grafana<br/>Dashboards"]
      loki["Loki<br/>Logs"]
      alertmanager["Alertmanager<br/>Alerts"]
    end

    subgraph lineage["Lineage & Telemetry"]
      direction TB
      openlineage["OpenLineage<br/>Lineage protocol"]
      otel["OpenTelemetry<br/>Traces and metrics"]
    end

    subgraph security["Security & Authentication"]
      direction TB
      keycloak["Keycloak<br/>OIDC / OAuth2"]
      cert_manager["cert-manager<br/>TLS certificates"]
      vault["Vault<br/>Secrets management"]
    end

    subgraph reliability["Backup & Reliability"]
      direction TB
      velero["Velero<br/>Backup and restore"]
      k8s["Kubernetes<br/>Native HA and scheduling"]
    end
  end

  sources --> ingest
  ingest --> bronze
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
  openmetadata --> governance_features
  marquez --> governance_features
  trino --> openmetadata
  floe --> openmetadata
  dbt --> openmetadata

  terraform -. deploys .-> seaweed
  terraform -. deploys .-> polaris
  terraform -. deploys .-> trino
  helm -. packages .-> seaweed
  helm -. packages .-> polaris
  helm -. packages .-> trino
  kind -. runs local stack .-> platform
```

The complete Mermaid source with styling classes is kept in
`openlakeforge-v1-architecture.mmd`.
