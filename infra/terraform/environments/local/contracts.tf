data "terraform_remote_state" "local_foundation" {
  backend = "local"

  config = {
    path = local.foundation_state_path
  }
}

locals {
  local_provider_name   = "local"
  foundation_state_path = var.foundation_state_path != null ? abspath(pathexpand(var.foundation_state_path)) : abspath("${path.root}/../../foundations/local-kind/terraform.tfstate")

  foundation_contract = merge(data.terraform_remote_state.local_foundation.outputs.foundation_contract, {
    provider              = local.local_provider_name
    implementation        = "foundation.kind"
    adapter               = "foundation.kind"
    cluster_type          = "kind"
    network_model         = "docker-bridge"
    platform_state_model  = "separate-terraform-root"
    platform_apply_order  = "foundation-before-platform"
    supported_environment = "local-dev"
  })

  kubernetes_platform_contract = {
    provider             = local.local_provider_name
    implementation       = "kubernetes.kind"
    adapter              = "platform.kubernetes.kind"
    namespace            = var.namespace
    kube_context         = coalesce(try(local.foundation_contract.kube_context, null), var.kube_context)
    kubeconfig_path      = coalesce(try(local.foundation_contract.kubeconfig_path, null), local.kubeconfig_path)
    cluster_name         = try(local.foundation_contract.cluster_name, "openlakeforge-local")
    platform_apply_model = "foundation-state-kube-context"
    workload_identity    = "kubernetes-service-account"
  }

  storage_contract = merge(module.seaweedfs.contract, {
    provider              = local.local_provider_name
    implementation        = "storage.s3_compatible.seaweedfs"
    adapter               = "storage.s3_compatible.seaweedfs"
    logical_name          = "lakehouse_storage"
    protocol              = "s3"
    auth_mode             = "static-access-key-secret"
    secret_delivery_mode  = "kubernetes-secret-env"
    workload_identity     = false
    ssl_mode              = "disabled"
    ingress_mode          = "cluster-internal"
    local_only            = true
    future_adapter_shapes = ["storage.aws_s3"]
    bronze_bucket_name    = var.bronze_bucket_name
    silver_bucket_name    = var.silver_bucket_name
    gold_bucket_name      = var.gold_bucket_name
  })

  metadata_database_contract = merge(module.postgresql.contract, {
    provider              = local.local_provider_name
    implementation        = "metadata_database.postgresql.in_cluster"
    adapter               = "metadata_database.postgresql.in_cluster"
    engine                = "postgresql"
    logical_name          = "platform_metadata"
    auth_mode             = "static-password-secret"
    secret_delivery_mode  = "kubernetes-secret-env"
    ssl_mode              = "disabled"
    endpoint              = "${module.postgresql.contract.host}:${module.postgresql.contract.port}"
    local_only            = true
    future_adapter_shapes = ["metadata_database.aws_rds_postgresql"]
  })

  catalog_contract = merge(module.polaris.contract, {
    provider                   = local.local_provider_name
    implementation             = "catalog.iceberg_rest.polaris"
    adapter                    = "catalog.iceberg_rest.polaris"
    logical_name               = "iceberg_catalog"
    catalog_provider           = "polaris"
    catalog_type               = "rest"
    catalog_name               = var.catalog_name
    runtime_profile            = "polaris-rest"
    trino_catalog_name         = "iceberg"
    default_warehouse_location = "s3://${var.silver_bucket_name}"
    catalog_namespace_model    = local.catalog_namespace_model
    catalog_namespaces         = local.catalog_namespaces
    catalog_schema_names       = [for namespace in local.catalog_namespaces : namespace.name]
    silver_namespaces          = local.catalog_silver_namespaces
    gold_namespaces            = local.catalog_gold_namespaces
    auth_mode                  = "oauth-client-secret"
    secret_delivery_mode       = "kubernetes-secret-env"
    ssl_mode                   = "disabled"
    endpoint                   = module.polaris.contract.rest_uri
    ingress_mode               = "cluster-internal"
    local_only                 = true
    implemented_catalog_types  = ["rest"]
    future_catalog_types       = ["glue"]
    future_adapter_shapes      = ["catalog.aws_glue"]
    trino_support              = ["rest", "glue"]
    dagster_support            = ["rest"]
    floe_support               = ["rest"]
    dbt_support                = ["rest"]
    openmetadata_support       = ["rest"]
    catalog_database_fqn       = "polaris.${var.catalog_name}"
    silver_schema_fqns = {
      for product, namespace in local.catalog_silver_namespaces : product => "polaris.${var.catalog_name}.${namespace}"
    }
    gold_schema_fqns = {
      for product, namespace in local.catalog_gold_namespaces : product => "polaris.${var.catalog_name}.${namespace}"
    }
  })

  governance_contract = merge(module.openmetadata.contract, {
    provider       = local.local_provider_name
    implementation = "governance.openmetadata"
    adapter        = "governance.openmetadata"
    logical_name   = "governance_catalog"
    auth_mode      = "local-development"
    endpoint       = "http://${module.openmetadata.contract.service_name}:${module.openmetadata.contract.http_port}"
    ingress_mode   = "cluster-internal"
    local_only     = true
  })

  reporting_contract = merge(module.superset.contract, {
    provider       = local.local_provider_name
    implementation = "reporting.superset"
    adapter        = "reporting.superset"
    logical_name   = "bi_reporting"
    auth_mode      = "local-development"
    endpoint       = "http://${module.superset.contract.service_name}:${module.superset.contract.http_port}"
    ingress_mode   = "cluster-internal"
    local_only     = true
  })

  query_contract = {
    provider            = local.local_provider_name
    implementation      = "query.trino"
    adapter             = "query.trino"
    logical_name        = "sql_query"
    service_name        = "trino"
    http_port           = 8080
    endpoint            = "http://trino:8080"
    catalog_name        = local.catalog_contract.trino_catalog_name
    supported_catalogs  = ["rest", "glue"]
    active_catalog_type = local.catalog_contract.catalog_type
    storage_ref         = local.storage_contract.logical_name
    catalog_ref         = local.catalog_contract.logical_name
    ingress_mode        = "cluster-internal"
    future_adapter_shapes = [
      "query.trino",
    ]
  }

  orchestration_contract = {
    provider       = local.local_provider_name
    implementation = "orchestration.dagster"
    adapter        = "orchestration.dagster"
    logical_name   = "orchestration"
    service_name   = "dagster-dagster-webserver"
    http_port      = 80
    endpoint       = "http://dagster-dagster-webserver:80"
    code_locations = [
      {
        name               = "sales-dagster"
        definitions_module = "domains.sales.definitions"
      },
      {
        name               = "supply-chain-dagster"
        definitions_module = "domains.supply_chain.definitions"
      },
    ]
    runner                    = "kubernetes-run-launcher"
    project_code_image        = "${var.project_code_image_repository}:${var.project_code_image_tag}"
    project_code_image_policy = var.project_code_image_pull_policy
    floe_manifest_access_mode = "remote"
    floe_manifest_base_uri    = local.floe_manifest_base_uri
    floe_report_base_uri      = local.floe_report_base_uri
    log_base_uri              = local.log_base_uri
    run_artifact_base_uri     = local.run_artifact_base_uri
    supported_catalogs        = ["rest"]
    active_catalog_type       = local.catalog_contract.catalog_type
    storage_ref               = local.storage_contract.logical_name
    catalog_ref               = local.catalog_contract.logical_name
    artifact_bucket_ref       = "ops_artifacts"
    local_only                = true
    future_adapter_shapes     = ["orchestration.dagster"]
  }

  artifact_registry_contract = {
    provider                  = local.local_provider_name
    implementation            = "artifacts.local_kind_image_load"
    adapter                   = "artifacts.local_kind_image_load"
    logical_name              = "runtime_images"
    project_code_image        = "${var.project_code_image_repository}:${var.project_code_image_tag}"
    project_code_image_policy = var.project_code_image_pull_policy
    superset_image            = "${var.superset_image_repository}:${var.superset_image_tag}"
    superset_image_policy     = var.superset_image_pull_policy
    distribution_mode         = "kind-load"
    target_cluster            = local.kubernetes_platform_contract.cluster_name
    local_only                = true
    future_adapter_shapes     = ["artifacts.ecr"]
  }

  artifact_bucket_contract = {
    provider                 = local.local_provider_name
    implementation           = "artifacts.s3_compatible_bucket"
    adapter                  = "artifacts.local_s3_compatible_bucket"
    logical_name             = "ops_artifacts"
    bucket_name              = var.ops_bucket_name
    artifact_base_uri        = local.artifact_base_uri
    access_mode              = "remote"
    base_uri                 = local.floe_manifest_base_uri
    floe_manifest_base_uri   = local.floe_manifest_base_uri
    floe_report_base_uri     = local.floe_report_base_uri
    log_base_uri             = local.log_base_uri
    run_artifact_base_uri    = local.run_artifact_base_uri
    manifest_uris            = local.product_floe_manifest_uris
    distribution_mode        = "s3-compatible-upload"
    storage_ref              = local.storage_contract.logical_name
    credentials_secret_name  = local.storage_contract.credentials_secret_name
    access_key_id_key        = local.storage_contract.access_key_id_key
    secret_access_key_key    = local.storage_contract.secret_access_key_key
    local_upload_access_mode = "kubectl-port-forward"
    local_only               = true
    future_adapter_shapes    = ["artifacts.aws_s3"]
  }

  artifact_contract = merge(local.artifact_registry_contract, {
    implementation             = "artifacts.local_kind_and_s3"
    adapter                    = "artifacts.local_kind_and_s3"
    floe_manifest_access_mode  = local.artifact_bucket_contract.access_mode
    floe_manifest_base_uri     = local.artifact_bucket_contract.base_uri
    floe_manifest_uris         = local.artifact_bucket_contract.manifest_uris
    floe_manifest_distribution = local.artifact_bucket_contract.distribution_mode
    ops_bucket_name            = local.artifact_bucket_contract.bucket_name
    artifact_base_uri          = local.artifact_bucket_contract.artifact_base_uri
    floe_report_base_uri       = local.artifact_bucket_contract.floe_report_base_uri
    log_base_uri               = local.artifact_bucket_contract.log_base_uri
    run_artifact_base_uri      = local.artifact_bucket_contract.run_artifact_base_uri
  })

  secrets_contract = {
    provider              = local.local_provider_name
    implementation        = "secrets.kubernetes_secret"
    adapter               = "secrets.kubernetes_secret"
    backend               = "kubernetes"
    delivery_mode         = "env-from-secret"
    rotation_mode         = "manual-development"
    references_only       = true
    local_only            = true
    future_adapter_shapes = ["secrets.aws_secrets_manager_or_external_secrets"]
  }

  identity_contract = {
    provider              = local.local_provider_name
    implementation        = "identity.local_development_credentials"
    adapter               = "identity.local_development_credentials"
    auth_mode             = "basic-local"
    oidc_enabled          = false
    workload_identity     = "kubernetes-service-account"
    local_only            = true
    future_adapter_shapes = ["identity.oidc", "identity.aws_iam_pod_identity"]
  }

  access_contract = {
    provider              = local.local_provider_name
    implementation        = "access.kubectl_port_forward"
    adapter               = "access.kubectl_port_forward"
    ingress_mode          = "port-forward"
    internal_access_mode  = "cluster-dns"
    external_access_mode  = "localhost-port-forward"
    tls_mode              = "none-development"
    local_only            = true
    future_adapter_shapes = ["access.ingress", "access.load_balancer", "access.private_dns"]
  }

  observability_contract = {
    provider              = local.local_provider_name
    implementation        = "observability.object_log_archive"
    adapter               = "observability.object_log_archive"
    metrics_enabled       = false
    tracing_enabled       = false
    logs_mode             = "s3-compatible-object-archive"
    log_base_uri          = local.log_base_uri
    compute_log_uri       = "${local.log_base_uri}/dagster/compute"
    kubernetes_log_uri    = "${local.log_base_uri}/k8s"
    artifact_bucket_ref   = local.artifact_bucket_contract.logical_name
    local_only            = true
    future_adapter_shapes = ["observability.loki_grafana", "observability.managed_prometheus", "observability.cloudwatch"]
  }

  provider_contracts = {
    foundation          = local.foundation_contract
    kubernetes_platform = local.kubernetes_platform_contract
    cluster             = local.kubernetes_platform_contract
    storage             = local.storage_contract
    metadata_database   = local.metadata_database_contract
    catalog             = local.catalog_contract
    query               = local.query_contract
    orchestration       = local.orchestration_contract
    governance          = local.governance_contract
    reporting           = local.reporting_contract
    artifact_registry   = local.artifact_registry_contract
    artifact_bucket     = local.artifact_bucket_contract
    artifacts           = local.artifact_contract
    secrets             = local.secrets_contract
    identity            = local.identity_contract
    access              = local.access_contract
    observability       = local.observability_contract
  }
}

check "foundation_contract_matches_platform_context" {
  assert {
    condition     = local.kubernetes_platform_contract.kube_context == local.foundation_contract.kube_context
    error_message = "The local platform kube context must come from the local foundation contract."
  }
}

check "local_contract_adapters_are_explicit" {
  assert {
    condition = alltrue([
      local.foundation_contract.provider == local.local_provider_name && local.foundation_contract.implementation != "" && local.foundation_contract.adapter != "",
      local.kubernetes_platform_contract.provider == local.local_provider_name && local.kubernetes_platform_contract.implementation != "" && local.kubernetes_platform_contract.adapter != "",
      local.storage_contract.provider == local.local_provider_name && local.storage_contract.implementation != "" && local.storage_contract.adapter != "",
      local.metadata_database_contract.provider == local.local_provider_name && local.metadata_database_contract.implementation != "" && local.metadata_database_contract.adapter != "",
      local.catalog_contract.provider == local.local_provider_name && local.catalog_contract.implementation != "" && local.catalog_contract.adapter != "",
      local.query_contract.provider == local.local_provider_name && local.query_contract.implementation != "" && local.query_contract.adapter != "",
      local.orchestration_contract.provider == local.local_provider_name && local.orchestration_contract.implementation != "" && local.orchestration_contract.adapter != "",
      local.governance_contract.provider == local.local_provider_name && local.governance_contract.implementation != "" && local.governance_contract.adapter != "",
      local.reporting_contract.provider == local.local_provider_name && local.reporting_contract.implementation != "" && local.reporting_contract.adapter != "",
      local.artifact_registry_contract.provider == local.local_provider_name && local.artifact_registry_contract.implementation != "" && local.artifact_registry_contract.adapter != "",
      local.artifact_bucket_contract.provider == local.local_provider_name && local.artifact_bucket_contract.implementation != "" && local.artifact_bucket_contract.adapter != "",
      local.secrets_contract.provider == local.local_provider_name && local.secrets_contract.implementation != "" && local.secrets_contract.adapter != "",
      local.identity_contract.provider == local.local_provider_name && local.identity_contract.implementation != "" && local.identity_contract.adapter != "",
      local.access_contract.provider == local.local_provider_name && local.access_contract.implementation != "" && local.access_contract.adapter != "",
      local.observability_contract.provider == local.local_provider_name && local.observability_contract.implementation != "" && local.observability_contract.adapter != "",
    ])
    error_message = "Every local provider contract must declare provider, implementation, and adapter."
  }
}

check "catalog_contract_consumer_support" {
  assert {
    condition = alltrue([
      contains(local.catalog_contract.trino_support, local.catalog_contract.catalog_type),
      contains(local.catalog_contract.dagster_support, local.catalog_contract.catalog_type),
      contains(local.catalog_contract.floe_support, local.catalog_contract.catalog_type),
      contains(local.catalog_contract.dbt_support, local.catalog_contract.catalog_type),
      contains(local.catalog_contract.openmetadata_support, local.catalog_contract.catalog_type),
    ])
    error_message = "The active catalog_type must be declared as supported by Trino, Dagster, Floe, dbt, and OpenMetadata."
  }
}

check "openmetadata_catalog_fqn_uses_lakehouse_database" {
  assert {
    condition     = local.catalog_contract.catalog_database_fqn == "polaris.${var.catalog_name}" && local.catalog_contract.catalog_name != "default"
    error_message = "OpenMetadata catalog assets must resolve under polaris.<catalog_name>, not polaris.default."
  }
}
