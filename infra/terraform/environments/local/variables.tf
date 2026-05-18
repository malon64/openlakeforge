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

variable "s3_region" {
  description = "S3 region used by local S3-compatible storage clients."
  type        = string
  default     = "us-east-1"
}
