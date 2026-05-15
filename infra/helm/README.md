# Helm

Helm assets will contain charts and values used by the Terraform environments.

Planned structure:

```text
infra/helm/
├── charts/
└── values/
```

Iteration 1 will begin with local `k3d` values for PostgreSQL, Garage, Polaris, and Trino. No Helm charts or values are implemented in Iteration 0.
