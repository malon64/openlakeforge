output "contract" {
  description = "S3-compatible storage contract for local lakehouse services."
  value = {
    endpoint                = "http://${local.s3_service_name}:${var.s3_port}"
    region                  = var.region
    bucket_name             = var.bucket_names[0]
    bucket_names            = var.bucket_names
    path_style_access       = true
    credentials_secret_name = kubernetes_secret_v1.s3_credentials.metadata[0].name
    access_key_id_key       = "AWS_ACCESS_KEY_ID"
    secret_access_key_key   = "AWS_SECRET_ACCESS_KEY"
    s3_service_name         = local.s3_service_name
    s3_service_port         = var.s3_port
  }

  depends_on = [
    kubernetes_job_v1.bucket,
  ]
}

output "s3_access_key_id" {
  description = "Generated local S3 access key. Exposed only for debugging."
  value       = local.access_key_id
  sensitive   = true
}

output "s3_secret_key" {
  description = "Generated local S3 secret key. Exposed only for debugging."
  value       = random_password.s3_secret_key.result
  sensitive   = true
}
