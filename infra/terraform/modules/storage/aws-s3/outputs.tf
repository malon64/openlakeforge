output "stage_contracts" {
  description = "Stage-owned S3 bucket bindings for provider-contract construction. Each stage's three physical buckets are its own."
  value = {
    for stage, names in local.stage_bucket_names : stage => {
      bronze_bucket_name = names.bronze
      silver_bucket_name = names.silver
      gold_bucket_name   = names.gold
    }
  }
}

output "stage_bucket_arns" {
  description = "Bucket ARNs per stage, keyed by medallion layer. Used to scope each stage's IAM policy to only its own buckets."
  value = {
    for stage, names in local.stage_bucket_names : stage => {
      bronze = aws_s3_bucket.this["${stage}-bronze"].arn
      silver = aws_s3_bucket.this["${stage}-silver"].arn
      gold   = aws_s3_bucket.this["${stage}-gold"].arn
    }
  }
}

output "ops_bucket_name" {
  description = "Shared operational artifact bucket name."
  value       = local.ops_bucket_name
}

output "ops_bucket_arn" {
  description = "Shared operational artifact bucket ARN."
  value       = aws_s3_bucket.this["ops"].arn
}

output "bucket_names" {
  description = "Every physical bucket name this module created."
  value       = local.bucket_names
}
