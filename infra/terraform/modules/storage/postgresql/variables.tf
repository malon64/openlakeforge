variable "namespace" {
  description = "Kubernetes namespace where PostgreSQL is deployed."
  type        = string
}

variable "release_name" {
  description = "Name prefix for Kubernetes resources."
  type        = string
  default     = "postgresql"
}

variable "storage_size" {
  description = "Persistent volume size for PostgreSQL data."
  type        = string
  default     = "5Gi"
}

variable "storage_class_name" {
  description = "Optional Kubernetes StorageClass for PostgreSQL data. Null uses the cluster default."
  type        = string
  default     = null
}

variable "dagster_db_name" {
  description = "PostgreSQL database name for Dagster."
  type        = string
  default     = "dagster"
}

variable "dagster_db_user" {
  description = "PostgreSQL user for Dagster."
  type        = string
  default     = "dagster"
}

variable "dagster_credentials_secret_name" {
  description = "Kubernetes Secret holding the Dagster PostgreSQL password. Key 'postgresql-password' (Dagster Helm chart convention)."
  type        = string
  default     = "postgresql-dagster-creds"
}

variable "openmetadata_db_name" {
  description = "PostgreSQL database name for OpenMetadata."
  type        = string
  default     = "openmetadata_db"
}

variable "openmetadata_db_user" {
  description = "PostgreSQL user for OpenMetadata."
  type        = string
  default     = "openmetadata_user"
}

variable "openmetadata_credentials_secret_name" {
  description = "Kubernetes Secret holding the OpenMetadata PostgreSQL password."
  type        = string
  default     = "postgresql-openmetadata-creds"
}

variable "superset_db_name" {
  description = "PostgreSQL database name for Superset."
  type        = string
  default     = "superset"
}

variable "superset_db_user" {
  description = "PostgreSQL user for Superset."
  type        = string
  default     = "superset"
}

variable "superset_credentials_secret_name" {
  description = "Kubernetes Secret holding the Superset PostgreSQL password."
  type        = string
  default     = "postgresql-superset-creds"
}
