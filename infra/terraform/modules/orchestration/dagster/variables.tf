variable "namespace" {
  description = "Kubernetes namespace where Dagster is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "dagster"
}

variable "chart_repository" {
  description = "Dagster Helm chart repository."
  type        = string
  default     = "https://dagster-io.github.io/helm"
}

variable "chart_version" {
  description = "Dagster Helm chart version. Keep this aligned with the project-code Dagster Python package."
  type        = string
  default     = "1.13.6"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
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
  default     = "IfNotPresent"
}

variable "project_code_image_revision" {
  description = "Project-code image revision used to force Dagster pod rollouts when the tag is reused."
  type        = string
  default     = "manual"
}

variable "code_location_name" {
  description = "Dagster user-code deployment and code location name."
  type        = string
  default     = "sales-dagster"
}

variable "definitions_module" {
  description = "Python module exposing Dagster Definitions."
  type        = string
  default     = "domains.sales.pipelines.dagster.definitions"
}

variable "floe_manifest_uri" {
  description = "S3 URI of the generated Floe manifest loaded by Dagster."
  type        = string
}

variable "floe_manifest_revision" {
  description = "Content revision of the generated Floe manifest used to force Dagster code-server rollouts."
  type        = string
  default     = "manual"
}

variable "storage_contract" {
  description = "Storage contract output from the SeaweedFS module."
  type = object({
    endpoint                = string
    region                  = string
    bucket_name             = string
    path_style_access       = bool
    credentials_secret_name = string
    access_key_id_key       = string
    secret_access_key_key   = string
  })
}

variable "catalog_contract" {
  description = "Polaris REST catalog contract output from the Polaris module."
  type = object({
    rest_uri                     = string
    token_uri                    = string
    warehouse                    = string
    oauth_scope                  = string
    floe_credentials_secret_name = string
    floe_client_id_key           = string
    floe_client_secret_key       = string
    dbt_credentials_secret_name  = string
    dbt_client_id_key            = string
    dbt_client_secret_key        = string
  })
}

variable "postgresql_contract" {
  description = "Shared PostgreSQL contract from the postgresql module."
  type = object({
    host                            = string
    port                            = number
    dagster_db_name                 = string
    dagster_db_user                 = string
    dagster_credentials_secret_name = string
  })
}

variable "governance_contract" {
  description = "OpenMetadata governance contract — provides the OpenLineage endpoint and ingestion-bot JWT for Floe and dbt run pods."
  type = object({
    openlineage_url           = string
    lineage_endpoint          = string
    ingestion_bot_secret_name = string
    ingestion_bot_jwt_key     = string
  })
}
