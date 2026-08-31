variable "bucket_name_prefix" {
  description = "Prefix for generated AWS S3 buckets. A short random suffix is appended for global-uniqueness."
  type        = string
  default     = "openlakeforge-poc"
}

variable "region" {
  description = "AWS region used by S3 clients."
  type        = string
}

variable "ops_bucket_name" {
  description = "Optional explicit operational artifact bucket name. Null uses bucket_name_prefix with a random suffix."
  type        = string
  default     = null
}

variable "stage_buckets" {
  description = "Stage-owned medallion bucket names, one entry per enabled stage. Each stage's three buckets are wholly its own - no other stage's identity, name, or object key can collide with them."
  type = map(object({
    bronze_bucket_name = string
    silver_bucket_name = string
    gold_bucket_name   = string
  }))
  default = {}
}

variable "force_destroy" {
  description = "Whether Terraform may delete non-empty POC buckets."
  type        = bool
  default     = true
}
