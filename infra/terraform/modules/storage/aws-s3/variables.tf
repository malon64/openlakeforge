variable "bucket_name_prefix" {
  description = "Prefix for generated AWS S3 buckets. A short random suffix is appended."
  type        = string
  default     = "openlakeforge-poc"
}

variable "region" {
  description = "AWS region used by S3 clients."
  type        = string
}

variable "bronze_bucket_name" {
  description = "Optional explicit Bronze bucket name. Null uses bucket_name_prefix with a random suffix."
  type        = string
  default     = null
}

variable "silver_bucket_name" {
  description = "Optional explicit Silver bucket name. Null uses bucket_name_prefix with a random suffix."
  type        = string
  default     = null
}

variable "gold_bucket_name" {
  description = "Optional explicit Gold bucket name. Null uses bucket_name_prefix with a random suffix."
  type        = string
  default     = null
}

variable "ops_bucket_name" {
  description = "Optional explicit operational artifact bucket name. Null uses bucket_name_prefix with a random suffix."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Whether Terraform may delete non-empty POC buckets."
  type        = bool
  default     = true
}
