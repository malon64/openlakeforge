# Data-only discovery of domains/<domain>/domain.yaml descriptors.
#
# This is the Terraform mirror of tools/olf/olf/descriptors.py's
# discover_domains()/discover_products(): the single source of truth for
# "what products exist" so environment roots stop hardcoding the seed
# products. Physical names are DERIVED from the logical descriptor using the
# same convention as the Python discovery module:
#   asset_prefix = product.asset_prefix (if set) else "<domain>_<product id>"
#   silver/gold namespace = "<asset_prefix>_silver" / "<asset_prefix>_gold"
#   job name               = "<asset_prefix>_pipeline"
#   manifest relative path = "<domain>/<product id>/<product id>.manifest.json"
#   code location           = one per domain: "<domain-with-dashes>-dagster"
#
# Adding a product only requires a new domains/<domain>/domain.yaml (or
# `olf product scaffold`); no environment root needs to change.

locals {
  domain_yaml_files = sort(fileset(var.domains_root, "*/domain.yaml"))
  domain_documents  = [for relative_path in local.domain_yaml_files : yamldecode(file("${var.domains_root}/${relative_path}"))]

  products_flat = flatten([
    for doc in local.domain_documents : [
      for product in doc.data_products : {
        domain       = doc.name
        id           = product.id
        asset_prefix = try(product.asset_prefix, "${doc.name}_${product.id}")
      }
    ]
  ])
}

output "domain_names" {
  description = "Every discovered domain name, sorted."
  value       = sort([for doc in local.domain_documents : doc.name])
}

output "products" {
  description = "Every discovered product with its derived physical names."
  value = [
    for product in local.products_flat : {
      domain                 = product.domain
      id                     = product.id
      asset_prefix           = product.asset_prefix
      job_name               = "${product.asset_prefix}_pipeline"
      silver_namespace       = "${product.asset_prefix}_silver"
      gold_namespace         = "${product.asset_prefix}_gold"
      manifest_relative_path = "${product.domain}/${product.id}/${product.id}.manifest.json"
    }
  ]
}

output "product_floe_manifest_relative_paths" {
  description = "asset_prefix -> \"<domain>/<product>/<product>.manifest.json\", relative to the Floe manifest base URI."
  value = {
    for product in local.products_flat :
    product.asset_prefix => "${product.domain}/${product.id}/${product.id}.manifest.json"
  }
}

output "catalog_product_namespaces" {
  description = "asset_prefix -> { silver, gold } catalog namespace names."
  value = {
    for product in local.products_flat : product.asset_prefix => {
      silver = "${product.asset_prefix}_silver"
      gold   = "${product.asset_prefix}_gold"
    }
  }
}

output "code_locations" {
  description = "One Dagster code location per discovered domain."
  value = [
    for domain_name in sort([for doc in local.domain_documents : doc.name]) : {
      name               = "${replace(domain_name, "_", "-")}-dagster"
      definitions_module = "domains.${domain_name}.definitions"
    }
  ]
}
