variable "namespace" {
  description = "Kubernetes namespace where Polaris is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "polaris"
}

variable "chart_repository" {
  description = "Polaris Helm chart repository."
  type        = string
  default     = "https://downloads.apache.org/polaris/helm-chart"
}

variable "chart_version" {
  description = "Polaris Helm chart version."
  type        = string
  default     = "1.4.1"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
}

variable "catalog_name" {
  description = "Polaris catalog name."
  type        = string
}

variable "principal_name" {
  description = "Polaris service principal name for Trino."
  type        = string
}

variable "principal_role" {
  description = "Polaris principal role granted to Trino."
  type        = string
}

variable "catalog_role" {
  description = "Polaris catalog role granted to the Trino principal role."
  type        = string
}

variable "floe_principal_name" {
  description = "Polaris service principal name for Floe."
  type        = string
  default     = "floe"
}

variable "floe_principal_role" {
  description = "Polaris principal role granted to Floe."
  type        = string
  default     = "data-writer"
}

variable "floe_catalog_role" {
  description = "Polaris catalog role granted to the Floe principal role."
  type        = string
  default     = "catalog-writer"
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

variable "bootstrap_secret_name" {
  description = "Kubernetes Secret containing Polaris root bootstrap credentials."
  type        = string
  default     = "polaris-bootstrap-credentials"
}

variable "trino_credentials_secret_name" {
  description = "Kubernetes Secret written by the bootstrap job with Trino OAuth credentials."
  type        = string
  default     = "polaris-trino-creds"
}

variable "floe_credentials_secret_name" {
  description = "Kubernetes Secret written by the bootstrap job with Floe OAuth credentials."
  type        = string
  default     = "polaris-floe-creds"
}

variable "bootstrap_job_image" {
  description = "Image used by the Polaris bootstrap Kubernetes Job."
  type        = string
  default     = "alpine/k8s:1.30.0"
}
