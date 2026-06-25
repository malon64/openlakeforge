variable "namespace" {
  description = "Kubernetes namespace where application DB password Secrets are created."
  type        = string
}

variable "name_prefix" {
  description = "Name prefix for RDS and Kubernetes resources."
  type        = string
  default     = "openlakeforge"
}

variable "vpc_id" {
  description = "VPC ID for the RDS security group."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the RDS subnet group."
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to connect to PostgreSQL."
  type        = list(string)
}

variable "engine_version" {
  description = "RDS PostgreSQL engine version."
  type        = string
  default     = "16.4"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "RDS allocated storage in GiB."
  type        = number
  default     = 20
}

variable "master_username" {
  description = "RDS master username used only by the bootstrap job."
  type        = string
  default     = "openlakeforge_admin"
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
  description = "Kubernetes Secret holding the Dagster PostgreSQL password."
  type        = string
  default     = "dagster-postgresql-secret"
}

variable "openmetadata_db_name" {
  description = "PostgreSQL database name for OpenMetadata."
  type        = string
  default     = "openmetadata"
}

variable "openmetadata_db_user" {
  description = "PostgreSQL user for OpenMetadata."
  type        = string
  default     = "openmetadata"
}

variable "openmetadata_credentials_secret_name" {
  description = "Kubernetes Secret holding the OpenMetadata PostgreSQL password."
  type        = string
  default     = "openmetadata-postgresql"
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
  default     = "superset-postgresql"
}
