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
  description = "S3-compatible storage contract consumed by Trino."
  type = object({
    endpoint                = optional(string)
    region                  = string
    path_style_access       = optional(bool)
    credentials_secret_name = optional(string)
    provider                = optional(string)
    implementation          = optional(string)
    auth_mode               = optional(string)
    ssl_mode                = optional(string)
    ingress_mode            = optional(string)
  })
}

variable "catalog_contract" {
  description = "Iceberg catalog contract consumed by Trino. Current local uses REST/Polaris; future provider profiles may use Glue."
  type = object({
    rest_uri                      = optional(string)
    token_uri                     = optional(string)
    warehouse                     = optional(string)
    oauth_scope                   = optional(string)
    trino_credentials_secret_name = optional(string)
    trino_client_id_key           = optional(string)
    trino_client_secret_key       = optional(string)
    bootstrap_run_id              = optional(string)
    catalog_type                  = optional(string)
    catalog_provider              = optional(string)
    catalog_name                  = optional(string)
    runtime_profile               = optional(string)
    trino_catalog_name            = optional(string)
    default_warehouse_location    = optional(string)
    glue_catalog_id               = optional(string)
    glue_region                   = optional(string)
    provider                      = optional(string)
    implementation                = optional(string)
    auth_mode                     = optional(string)
    ssl_mode                      = optional(string)
    endpoint                      = optional(string)
    ingress_mode                  = optional(string)
  })
}

variable "catalog_bootstrap_revision" {
  description = "Content revision used to force Trino pod rollouts when Polaris bootstrap credentials or grants change."
  type        = string
}
