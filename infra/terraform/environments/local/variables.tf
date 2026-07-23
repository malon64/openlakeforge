variable "namespace" {
  description = "Kubernetes namespace for the local lakehouse stack."
  type        = string
  default     = "lakehouse"
}

variable "kubeconfig_path" {
  description = "Optional kubeconfig path. Defaults to the repository-local .tmp/kubeconfigs/local.yaml."
  type        = string
  default     = null
}

variable "kube_context" {
  description = "Fallback kubeconfig context for the local foundation cluster when the foundation state is not inspected by wrapper scripts."
  type        = string
  default     = "kind-openlakeforge-local"
}

variable "foundation_state_path" {
  description = "Local Terraform state path for the local cluster foundation root."
  type        = string
  default     = null
}

variable "catalog_name" {
  description = "Polaris catalog and Trino Iceberg warehouse name."
  type        = string
  default     = "lakehouse_dev"
}

variable "bronze_bucket_name" {
  description = "S3 bucket for the Bronze raw landing zone (immutable CSV files)."
  type        = string
  default     = "lakehouse-bronze"
}

variable "silver_bucket_name" {
  description = "S3 bucket for the Silver layer (Floe-validated Iceberg tables)."
  type        = string
  default     = "lakehouse-silver"
}

variable "gold_bucket_name" {
  description = "S3 bucket for the Gold layer (dbt business-ready Iceberg marts)."
  type        = string
  default     = "lakehouse-gold"
}

variable "ops_bucket_name" {
  description = "S3 bucket used for local operational artifacts: manifests, logs, reports, and run artifacts."
  type        = string
  default     = "openlakeforge-ops"
}

variable "s3_region" {
  description = "S3 region used by local S3-compatible storage clients."
  type        = string
  default     = "us-east-1"
}

variable "project_code_image_repository" {
  description = "Project-code image repository used by the Dagster code server and run pods."
  type        = string
  default     = "ghcr.io/openlakeforge/project-code"
}

variable "project_code_image_tag" {
  description = "Project-code image tag used by the Dagster code server and run pods."
  type        = string
  default     = "local"
}

variable "project_code_image_pull_policy" {
  description = "Project-code image pull policy used by the Dagster code server and run pods."
  type        = string
  default     = "Never"
}

variable "project_code_image_revision" {
  description = "Local project-code image revision used to force Dagster pod rollouts when the tag is reused."
  type        = string
  default     = "manual"
}

variable "superset_image_repository" {
  description = "Superset image repository used by the local Superset Helm release."
  type        = string
  default     = "ghcr.io/openlakeforge/superset"
}

variable "superset_image_tag" {
  description = "Superset image tag used by the local Superset Helm release."
  type        = string
  default     = "local"
}

variable "superset_image_pull_policy" {
  description = "Superset image pull policy used by the local Superset Helm release."
  type        = string
  default     = "Never"
}

variable "trino_chart_package_path" {
  description = "Optional local Trino Helm chart package used by local-up to avoid transient GitHub chart download failures."
  type        = string
  default     = null
}

variable "polaris_bootstrap_generation" {
  description = "Generation value recorded on the Polaris bootstrap job after local-up detects stale in-memory Polaris service principals."
  type        = string
  default     = "manual"
}
