variable "namespace" {
  description = "Kubernetes namespace where Superset is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "superset"
}

variable "chart_repository" {
  description = "Superset Helm chart repository."
  type        = string
  default     = "http://apache.github.io/superset/"
}

variable "chart_version" {
  description = "Superset Helm chart version."
  type        = string
  default     = "0.15.5"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
}

variable "image_repository" {
  description = "Superset image repository with OpenLakeForge runtime drivers."
  type        = string
  default     = "ghcr.io/openlakeforge/superset"
}

variable "image_tag" {
  description = "Superset image tag."
  type        = string
  default     = "local"
}

variable "image_pull_policy" {
  description = "Superset image pull policy."
  type        = string
  default     = "Never"
}

variable "admin_username" {
  description = "Local Superset admin username."
  type        = string
  default     = "admin"
}

variable "admin_password" {
  description = "Local Superset admin password."
  type        = string
  default     = "admin"
  sensitive   = true
}

variable "admin_email" {
  description = "Local Superset admin email."
  type        = string
  default     = "admin@openlakeforge.local"
}

variable "http_port" {
  description = "Superset HTTP service port."
  type        = number
  default     = 8088
}

variable "reports_mount_path" {
  description = "Path where dynamic Superset report bundles are mounted in Superset pods."
  type        = string
  default     = "/app/openlakeforge/reports"
}

variable "reports_storage_size" {
  description = "Persistent volume size for dynamic Superset report bundles."
  type        = string
  default     = "1Gi"
}

variable "reports_storage_class_name" {
  description = "Optional Kubernetes StorageClass for Superset report bundles. Null uses the cluster default."
  type        = string
  default     = null
}

variable "postgresql_contract" {
  description = "Shared PostgreSQL contract from the postgresql module."
  type = object({
    host                             = string
    port                             = number
    superset_db_name                 = string
    superset_db_user                 = string
    superset_credentials_secret_name = string
  })
}
