variable "namespace" {
  description = "Kubernetes namespace where OpenMetadata is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "openmetadata"
}

variable "chart_repository" {
  description = "OpenMetadata Helm chart repository."
  type        = string
  default     = "https://helm.open-metadata.org"
}

variable "chart_version" {
  description = "OpenMetadata Helm chart version."
  type        = string
  default     = "1.12.10"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file for the openmetadata chart."
  type        = string
}

variable "deps_values_file" {
  description = "Path to the Helm values file for the openmetadata-dependencies chart (OpenSearch only; MySQL and Airflow disabled)."
  type        = string
}

variable "deps_chart_version" {
  description = "openmetadata-dependencies Helm chart version. Should match chart_version."
  type        = string
  default     = "1.12.10"
}

variable "postgresql_contract" {
  description = "Metadata PostgreSQL contract consumed by OpenMetadata."
  type = object({
    host                                 = string
    port                                 = number
    openmetadata_db_name                 = string
    openmetadata_db_user                 = string
    openmetadata_credentials_secret_name = string
    provider                             = optional(string)
    implementation                       = optional(string)
    auth_mode                            = optional(string)
    ssl_mode                             = optional(string)
    endpoint                             = optional(string)
  })
}

variable "bootstrap_job_image" {
  description = "Image used by the OpenMetadata bootstrap Kubernetes Job."
  type        = string
  default     = "alpine/k8s:1.30.0"
}

variable "admin_email" {
  description = "OpenMetadata admin user email."
  type        = string
  default     = "admin@open-metadata.org"
}

variable "admin_password" {
  description = "OpenMetadata admin user password."
  type        = string
  default     = "admin"
  sensitive   = true
}

variable "om_http_port" {
  description = "OpenMetadata HTTP service port."
  type        = number
  default     = 8585
}

variable "ingestion_bot_secret_name" {
  description = "Kubernetes Secret written by the bootstrap job containing the ingestion-bot JWT."
  type        = string
  default     = "openmetadata-ingestion-bot"
}

variable "ingestion_bot_jwt_key" {
  description = "Key within the ingestion-bot secret that holds the JWT token."
  type        = string
  default     = "OPENMETADATA_INGESTION_BOT_JWT"
}

variable "catalog_contract" {
  description = "Iceberg catalog contract consumed by OpenMetadata. This module currently uses the REST/Polaris fields for catalog discovery."
  type = object({
    rest_uri                   = string
    token_uri                  = string
    warehouse                  = string
    oauth_scope                = string
    om_credentials_secret_name = string
    om_client_id_key           = string
    om_client_secret_key       = string
    catalog_type               = optional(string)
    catalog_provider           = optional(string)
    catalog_name               = optional(string)
    runtime_profile            = optional(string)
    trino_catalog_name         = optional(string)
    default_warehouse_location = optional(string)
    glue_catalog_id            = optional(string)
    glue_region                = optional(string)
    provider                   = optional(string)
    implementation             = optional(string)
    auth_mode                  = optional(string)
    ssl_mode                   = optional(string)
    endpoint                   = optional(string)
    ingress_mode               = optional(string)
  })
}

variable "storage_contract" {
  description = "S3-compatible storage contract consumed by OpenMetadata."
  type = object({
    virtual_host_endpoint   = string
    region                  = string
    bucket_name             = string
    credentials_secret_name = string
    access_key_id_key       = string
    secret_access_key_key   = string
    endpoint                = optional(string)
    path_style_access       = optional(bool)
    provider                = optional(string)
    implementation          = optional(string)
    auth_mode               = optional(string)
    ssl_mode                = optional(string)
    ingress_mode            = optional(string)
  })
}

variable "catalog_database_name" {
  description = "OpenMetadata database name to seed under the Polaris database service before catalog refresh."
  type        = string
  default     = "sales_dev"
}

variable "catalog_schema_names" {
  description = "OpenMetadata database schema names to seed before catalog refresh."
  type        = list(string)
  default     = ["silver", "gold"]
}

variable "catalog_refresh_schedule" {
  description = "Cron schedule for the independent OpenMetadata Polaris catalog refresh job."
  type        = string
  default     = "0 * * * *"
}

variable "catalog_refresh_enabled" {
  description = "Whether to run an independent scheduled OpenMetadata catalog refresh for Polaris."
  type        = bool
  default     = true
}

variable "dagster_webserver_url" {
  description = "Cluster-internal URL of the Dagster webserver for OM pipeline metadata ingestion."
  type        = string
  default     = "http://dagster-dagster-webserver:80"
}

variable "superset_url" {
  description = "Cluster-internal URL of the Superset instance for OM dashboard metadata ingestion."
  type        = string
  default     = "http://superset:8088"
}
