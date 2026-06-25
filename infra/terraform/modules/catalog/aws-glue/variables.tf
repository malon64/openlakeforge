variable "region" {
  description = "AWS region for Glue."
  type        = string
}

variable "account_id" {
  description = "AWS account ID that owns the Glue Data Catalog."
  type        = string
}

variable "catalog_name" {
  description = "Logical OpenLakeForge catalog name."
  type        = string
  default     = "lakehouse_dev"
}

variable "catalog_namespaces" {
  description = "Product/layer Glue databases to create."
  type = list(object({
    name     = string
    location = string
  }))
}

variable "trino_catalog_name" {
  description = "Trino catalog name."
  type        = string
  default     = "iceberg"
}
