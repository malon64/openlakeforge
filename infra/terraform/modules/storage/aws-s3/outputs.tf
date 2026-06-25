output "contract" {
  description = "S3 storage contract implemented by AWS S3."
  value = {
    endpoint                = null
    virtual_host_endpoint   = null
    region                  = var.region
    bucket_name             = local.buckets.bronze
    bucket_names            = local.bucket_names
    bronze_bucket_name      = local.buckets.bronze
    silver_bucket_name      = local.buckets.silver
    gold_bucket_name        = local.buckets.gold
    ops_bucket_name         = local.buckets.ops
    path_style_access       = false
    credentials_secret_name = null
    access_key_id_key       = null
    secret_access_key_key   = null
    s3_service_name         = null
    s3_service_port         = null
  }
}

output "bucket_arns" {
  description = "Bucket ARNs keyed by medallion/artifact role."
  value = {
    for key, bucket in aws_s3_bucket.this : key => bucket.arn
  }
}

output "bucket_names" {
  description = "Bucket names keyed by medallion/artifact role."
  value       = local.buckets
}
