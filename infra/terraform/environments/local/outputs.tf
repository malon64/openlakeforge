output "storage_contract" {
  description = "Non-secret S3 contract consumed by Polaris and Trino."
  value       = module.seaweedfs.contract
}

output "catalog_contract" {
  description = "Non-secret Polaris REST catalog contract consumed by Trino."
  value       = module.polaris.contract
}
