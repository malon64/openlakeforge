# Helm

Helm assets contain non-secret chart values used by each environment target.
Terraform overlays dynamic service contracts, endpoints, and Secret references.

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

All three services deploy to a single `lakehouse` namespace. Terraform
orchestrates the full install sequence:

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

### Secrets and service contracts

Terraform generates local development credentials and stores them in Kubernetes
Secrets:

- `seaweedfs-s3-creds`
- `polaris-bootstrap-credentials`
- `polaris-trino-creds`

Trino catalog files use environment-variable secret substitution and should not
contain literal credential values.

## Future iterations

- Persistent Polaris metastore (PostgreSQL via CloudNativePG)
- Separate namespaces per component
- External Secrets Operator for credential management
- Terraform-managed cloud targets (AWS, GCP, Azure)
