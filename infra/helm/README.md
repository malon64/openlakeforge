# Helm

The local stack consumes upstream Helm charts through Terraform `helm_release`
resources. Static, non-secret local chart values live in YAML files here.
Terraform modules layer dynamic values on top for generated credentials, service
contract endpoints, Secret references, and bootstrap run markers.

## Structure

```text
infra/helm/
└── values/
    └── local/
        ├── seaweedfs.yaml
        ├── polaris.yaml
        ├── trino.yaml
        ├── superset.yaml
        └── dagster.yaml
```

## Local charts

- **SeaweedFS** (`seaweedfs/seaweedfs`)
  Chart source: https://seaweedfs.github.io/seaweedfs/helm
- **Apache Polaris** (`polaris/polaris`)
  Chart source: https://downloads.apache.org/polaris/helm-chart
- **Trino** (`trino/trino`)
  Chart source: https://trinodb.github.io/charts
- **Apache Superset** (`superset/superset`)
  Chart source: http://apache.github.io/superset/
- **Dagster** (`dagster/dagster`)
  Chart source: https://dagster-io.github.io/helm

## Workflow

```bash
make local-foundation-up
make local-infra-up
make local-artifacts-deploy
make local-up
make local-forward
make local-down
make local-foundation-down
```
