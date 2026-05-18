variable "namespace" {
  description = "Kubernetes namespace where SeaweedFS is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "seaweedfs"
}

variable "chart_repository" {
  description = "SeaweedFS Helm chart repository."
  type        = string
  default     = "https://seaweedfs.github.io/seaweedfs/helm"
}

variable "chart_version" {
  description = "SeaweedFS Helm chart version."
  type        = string
  default     = "4.23.0"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
}

variable "image_tag" {
  description = "SeaweedFS image tag used by the chart and bucket jobs."
  type        = string
  default     = "4.23"
}

variable "bucket_names" {
  description = "S3 buckets to create in SeaweedFS."
  type        = list(string)
}

variable "region" {
  description = "S3 region advertised to clients."
  type        = string
}

variable "credentials_secret_name" {
  description = "Kubernetes Secret name containing S3 credentials for downstream clients."
  type        = string
  default     = "seaweedfs-s3-creds"
}

variable "s3_port" {
  description = "SeaweedFS S3 service port."
  type        = number
  default     = 8333
}
