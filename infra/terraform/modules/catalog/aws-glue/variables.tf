variable "region" {
  description = "AWS region for Glue."
  type        = string
}

variable "account_id" {
  description = "AWS account ID that owns the Glue Data Catalog."
  type        = string
}

variable "catalog_name" {
  description = "Logical OpenLakeForge catalog name. Used only in single-stage compatibility mode (stage_catalogs empty)."
  type        = string
  default     = "lakehouse_dev"
}

variable "trino_catalog_name" {
  description = "Trino catalog name. Used only in single-stage compatibility mode (stage_catalogs empty)."
  type        = string
  default     = "iceberg"
}

variable "stage_catalogs" {
  description = "Per-stage physical-database-name prefixes within the account's shared default Glue catalog (see outputs.tf). Empty keeps the legacy single-stage (account default) interface for v0.2 migration."
  type = map(object({
    catalog_name = string
  }))
  default = {}
}
