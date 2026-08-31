resource "random_string" "bucket_suffix" {
  length  = 8
  lower   = true
  numeric = true
  special = false
  upper   = false
}

locals {
  ops_bucket_name = coalesce(var.ops_bucket_name, "${var.bucket_name_prefix}-ops-${random_string.bucket_suffix.result}")

  # Per-stage bucket names always carry the shared random suffix: S3 bucket
  # names are unique across all of AWS, not just this account, so the
  # deterministic `<profile>-<stage>-<layer>` identity alone (correct as the
  # logical/contract name) is not safe as the physical one.
  stage_bucket_names = {
    for stage, binding in var.stage_buckets : stage => {
      bronze = "${binding.bronze_bucket_name}-${random_string.bucket_suffix.result}"
      silver = "${binding.silver_bucket_name}-${random_string.bucket_suffix.result}"
      gold   = "${binding.gold_bucket_name}-${random_string.bucket_suffix.result}"
    }
  }

  all_buckets = merge(
    { for stage, names in local.stage_bucket_names : "${stage}-bronze" => names.bronze },
    { for stage, names in local.stage_bucket_names : "${stage}-silver" => names.silver },
    { for stage, names in local.stage_bucket_names : "${stage}-gold" => names.gold },
    { ops = local.ops_bucket_name },
  )

  bucket_names = values(local.all_buckets)
}

resource "aws_s3_bucket" "this" {
  for_each = local.all_buckets

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
