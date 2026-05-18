# Helm

Helm assets contain chart values used by each environment target.

## Structure

```text
infra/helm/
├── charts/          # vendored or custom charts (none yet in iteration 1)
└── values/
    ├── seaweedfs.yaml # SeaweedFS S3 object storage
    ├── polaris.yaml # Apache Polaris Iceberg REST catalog
    └── trino.yaml   # Trino SQL query engine
```

## Iteration 1 — local kind cluster

All three services deploy to a single `lakehouse` namespace.
The `scripts/local/setup.sh` orchestrates the full install sequence:

1. **SeaweedFS** (`seaweedfs/seaweedfs`) — S3-compatible object storage  
   Chart source: https://seaweedfs.github.io/seaweedfs/helm

2. **Apache Polaris** (`polaris/polaris`) — Iceberg REST catalog  
   Chart source: https://downloads.apache.org/polaris/helm-chart

3. **Trino** (`trino/trino`) — analytics SQL engine  
   Chart source: https://trinodb.github.io/charts

### Quick start

```bash
# Deploy everything (requires an existing kind cluster)
make local-up

# Port-forward all services to localhost
make local-forward
# Trino UI:    http://localhost:8080
# Polaris API: http://localhost:8181/api/catalog
# SeaweedFS S3: http://localhost:9000

# Status
make local-status

# Tear down (leaves the cluster intact)
make local-down
```

### Secrets and generated files

`setup.sh` generates `/tmp/trino-iceberg-generated.yaml` at deploy time.
This file contains SeaweedFS S3 credentials and Polaris OAuth2 credentials and
is **never committed to git**.

SeaweedFS S3 credentials are stored in the `seaweedfs-s3-creds` Kubernetes Secret
(created by `bootstrap-seaweedfs.sh`) and injected into Polaris via `extraEnv`.

## Future iterations

- Persistent Polaris metastore (PostgreSQL via CloudNativePG)
- Separate namespaces per component
- External Secrets Operator for credential management
- Terraform-managed cloud targets (AWS, GCP, Azure)
