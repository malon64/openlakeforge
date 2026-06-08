variable "namespace" {
  description = "Kubernetes namespace where Trino is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "trino"
}

variable "chart_repository" {
  description = "Trino Helm chart repository."
  type        = string
  default     = "https://trinodb.github.io/charts"
}

variable "chart_version" {
  description = "Trino Helm chart version."
  type        = string
  default     = "1.42.2"
}

variable "chart_package_path" {
  description = "Optional local Trino Helm chart package path. When set, Terraform installs this package instead of downloading from chart_repository."
  type        = string
  default     = null
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
}

variable "storage_contract" {
  description = "Storage contract output from the SeaweedFS module."
  type = object({
    endpoint                = string
    region                  = string
    path_style_access       = bool
    credentials_secret_name = string
  })
}

variable "catalog_contract" {
  description = "Catalog contract output from the Polaris module."
  type = object({
    rest_uri                      = string
    token_uri                     = string
    warehouse                     = string
    oauth_scope                   = string
    trino_credentials_secret_name = string
    bootstrap_run_id              = string
  })
}

variable "catalog_bootstrap_revision" {
  description = "Content revision used to force Trino pod rollouts when Polaris bootstrap credentials or grants change."
  type        = string
}
