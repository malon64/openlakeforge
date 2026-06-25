resource "random_string" "bucket_suffix" {
  length  = 8
  lower   = true
  numeric = true
  special = false
  upper   = false
}

locals {
  buckets = {
    bronze = coalesce(var.bronze_bucket_name, "${var.bucket_name_prefix}-bronze-${random_string.bucket_suffix.result}")
    silver = coalesce(var.silver_bucket_name, "${var.bucket_name_prefix}-silver-${random_string.bucket_suffix.result}")
    gold   = coalesce(var.gold_bucket_name, "${var.bucket_name_prefix}-gold-${random_string.bucket_suffix.result}")
    ops    = coalesce(var.ops_bucket_name, "${var.bucket_name_prefix}-ops-${random_string.bucket_suffix.result}")
  }

  bucket_names = [
    local.buckets.bronze,
    local.buckets.silver,
    local.buckets.gold,
    local.buckets.ops,
  ]
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = var.force_destroy

  tags = {
    Project     = "openlakeforge"
    Environment = "aws-poc"
    Layer       = each.key
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
