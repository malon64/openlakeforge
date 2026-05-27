variable "namespace" {
  description = "Kubernetes namespace for the local lakehouse stack."
  type        = string
  default     = "lakehouse"
}

variable "kubeconfig_path" {
  description = "Optional kubeconfig path. Defaults to the active kubectl context."
  type        = string
  default     = null
}

variable "kube_context" {
  description = "Optional kubeconfig context. Defaults to the active kubectl context."
  type        = string
  default     = null
}

variable "catalog_name" {
  description = "Polaris catalog and Trino Iceberg warehouse name."
  type        = string
  default     = "lakehouse"
}

variable "iceberg_bucket_name" {
  description = "S3 bucket used by the local Iceberg catalog."
  type        = string
  default     = "iceberg-data"
}

variable "code_bucket_name" {
  description = "S3 bucket used for local code and orchestration artifacts."
  type        = string
  default     = "openlakeforge-code"
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
  default     = "IfNotPresent"
}
