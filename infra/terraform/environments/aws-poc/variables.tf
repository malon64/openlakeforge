variable "namespace" {
  description = "Kubernetes namespace for the AWS EKS POC lakehouse stack."
  type        = string
  default     = "lakehouse"
}

variable "aws_region" {
  description = "AWS region for provider resources."
  type        = string
  default     = "eu-west-1"
}

variable "default_tags" {
  description = "Tags applied to every taggable resource via the provider default_tags block. Account-mandated tags (Project/Owner/Requester/Env/IaC) are supplied via a .tfvars file. Casing is significant."
  type        = map(string)
  default     = {}
}

variable "kubeconfig_path" {
  description = "Optional kubeconfig path. Defaults to the path emitted by the AWS foundation root."
  type        = string
  default     = null
}

variable "kube_context" {
  description = "Fallback kubeconfig context for the AWS EKS foundation cluster."
  type        = string
  default     = "eks-openlakeforge-poc"
}

variable "foundation_state_path" {
  description = "Local Terraform state path for the AWS EKS foundation root."
  type        = string
  default     = null
}

variable "catalog_name" {
  description = "Logical OpenLakeForge catalog name."
  type        = string
  default     = "lakehouse_dev"
}

variable "bucket_name_prefix" {
  description = "Prefix used for generated AWS S3 buckets."
  type        = string
  default     = "openlakeforge-poc"
}

variable "bronze_bucket_name" {
  description = "Optional explicit Bronze S3 bucket name."
  type        = string
  default     = null
}

variable "silver_bucket_name" {
  description = "Optional explicit Silver S3 bucket name."
  type        = string
  default     = null
}

variable "gold_bucket_name" {
  description = "Optional explicit Gold S3 bucket name."
  type        = string
  default     = null
}

variable "ops_bucket_name" {
  description = "Optional explicit operational artifact S3 bucket name."
  type        = string
  default     = null
}

variable "project_code_image_repository" {
  description = "Project-code image repository used by the Dagster code server and run pods."
  type        = string
  default     = "ghcr.io/openlakeforge/project-code"
}

variable "project_code_image_tag" {
  description = "Project-code image tag used by the Dagster code server and run pods."
  type        = string
  default     = "aws-poc"
}

variable "project_code_image_pull_policy" {
  description = "Project-code image pull policy used by the Dagster code server and run pods."
  type        = string
  default     = "Always"
}

variable "project_code_image_revision" {
  description = "Project-code image revision used to force Dagster pod rollouts when the tag is reused."
  type        = string
  default     = "manual"
}

variable "superset_image_repository" {
  description = "Superset image repository used by the AWS POC Superset Helm release."
  type        = string
  default     = "ghcr.io/openlakeforge/superset"
}

variable "superset_image_tag" {
  description = "Superset image tag used by the AWS POC Superset Helm release."
  type        = string
  default     = "aws-poc"
}

variable "superset_image_pull_policy" {
  description = "Superset image pull policy used by the AWS POC Superset Helm release."
  type        = string
  default     = "Always"
}

variable "trino_chart_package_path" {
  description = "Optional local Trino Helm chart package used by aws-up to avoid transient chart download failures."
  type        = string
  default     = null
}

variable "dagster_chart_package_path" {
  description = "Optional local Dagster Helm chart package used by aws-up to avoid remote schema validation failures."
  type        = string
  default     = null
}

variable "rds_instance_class" {
  description = "RDS PostgreSQL instance class for the AWS POC."
  type        = string
  default     = "db.t4g.micro"
}
