data "terraform_remote_state" "aws_foundation" {
  backend = "local"

  config = {
    path = local.foundation_state_path
  }
}

locals {
  aws_provider_name     = "aws"
  foundation_state_path = var.foundation_state_path != null ? abspath(pathexpand(var.foundation_state_path)) : abspath("${path.root}/../../foundations/aws-eks/terraform.tfstate")

  foundation_contract = merge(data.terraform_remote_state.aws_foundation.outputs.foundation_contract, {
    provider              = local.aws_provider_name
    implementation        = "foundation.eks"
    adapter               = "foundation.eks"
    cluster_type          = "eks"
    network_model         = "aws-vpc-cni"
    platform_state_model  = "separate-terraform-root"
    platform_apply_order  = "foundation-before-platform"
    supported_environment = "aws-poc"
  })

  kubernetes_platform_contract = {
    provider             = local.aws_provider_name
    implementation       = "kubernetes.eks"
    adapter              = "platform.kubernetes.eks"
    namespace            = var.namespace
    kube_context         = coalesce(try(local.foundation_contract.kube_context, null), var.kube_context)
    kubeconfig_path      = coalesce(try(local.foundation_contract.kubeconfig_path, null), local.kubeconfig_path)
    cluster_name         = try(local.foundation_contract.cluster_name, "eks-openlakeforge-poc")
    aws_region           = local.aws_region
    platform_apply_model = "foundation-state-kube-context"
    workload_identity    = "aws-pod-identity"
  }

  storage_contract = merge(module.s3.contract, {
    provider             = local.aws_provider_name
    implementation       = "storage.aws_s3"
    adapter              = "storage.aws_s3"
    logical_name         = "lakehouse_storage"
    protocol             = "s3"
    auth_mode            = "aws-pod-identity"
    secret_delivery_mode = "none"
    workload_identity    = true
    ssl_mode             = "required"
    ingress_mode         = "aws-service-endpoint"
    local_only           = false
    poc_only             = true
  })

  metadata_database_contract = merge(module.rds_postgresql.contract, {
    provider             = local.aws_provider_name
    implementation       = "metadata_database.aws_rds_postgresql"
    adapter              = "metadata_database.aws_rds_postgresql"
    engine               = "postgresql"
    logical_name         = "platform_metadata"
    auth_mode            = "static-password-secret"
    secret_delivery_mode = "kubernetes-secret-env"
    ssl_mode             = "required"
    endpoint             = "${module.rds_postgresql.contract.host}:${module.rds_postgresql.contract.port}"
    local_only           = false
    poc_only             = true
  })

  catalog_contract = merge(module.glue.contract, {
    provider                   = local.aws_provider_name
    implementation             = "catalog.aws_glue"
    adapter                    = "catalog.aws_glue"
    logical_name               = "iceberg_catalog"
    catalog_provider           = "aws-glue"
    catalog_type               = "glue"
    catalog_name               = var.catalog_name
    runtime_profile            = "aws-glue-rest"
    trino_catalog_name         = "iceberg"
    default_warehouse_location = "s3://${local.storage_contract.silver_bucket_name}"
    catalog_namespace_model    = local.catalog_namespace_model
    catalog_namespaces         = local.catalog_namespaces
    silver_namespaces          = local.catalog_silver_namespaces
    gold_namespaces            = local.catalog_gold_namespaces
    auth_mode                  = "aws-sigv4-pod-identity"
    secret_delivery_mode       = "none"
    ssl_mode                   = "required"
    endpoint                   = module.glue.contract.rest_uri
    ingress_mode               = "aws-service-endpoint"
    local_only                 = false
    poc_only                   = true
    implemented_catalog_types  = ["glue"]
    future_catalog_types       = ["rest"]
    future_adapter_shapes      = ["catalog.aws_glue", "catalog.aws_glue_iceberg_rest"]
    trino_support              = ["glue"]
    dagster_support            = ["glue"]
    floe_support               = ["glue"]
    dbt_support                = ["glue"]
    openmetadata_support       = ["glue"]
    catalog_database_fqn       = "aws_glue.${var.catalog_name}"
    silver_schema_fqns = {
      for product, namespace in local.catalog_silver_namespaces : product => "aws_glue.${var.catalog_name}.${namespace}"
    }
    gold_schema_fqns = {
      for product, namespace in local.catalog_gold_namespaces : product => "aws_glue.${var.catalog_name}.${namespace}"
    }
  })

  governance_contract = merge(module.openmetadata.contract, {
    provider       = local.aws_provider_name
    implementation = "governance.openmetadata_on_eks"
    adapter        = "governance.openmetadata_on_eks"
    logical_name   = "governance_catalog"
    auth_mode      = "local-development"
    endpoint       = "http://${module.openmetadata.contract.service_name}:${module.openmetadata.contract.http_port}"
    ingress_mode   = "cluster-internal"
    local_only     = false
    poc_only       = true
  })

  reporting_contract = merge(module.superset.contract, {
    provider       = local.aws_provider_name
    implementation = "reporting.superset_on_eks"
    adapter        = "reporting.superset_on_eks"
    logical_name   = "bi_reporting"
    auth_mode      = "local-development"
    endpoint       = "http://${module.superset.contract.service_name}:${module.superset.contract.http_port}"
    ingress_mode   = "cluster-internal"
    local_only     = false
    poc_only       = true
  })

  query_contract = {
    provider            = local.aws_provider_name
    implementation      = "query.trino_on_eks"
    adapter             = "query.trino_on_eks"
    logical_name        = "sql_query"
    service_name        = "trino"
    http_port           = 8080
    endpoint            = "http://trino:8080"
    catalog_name        = local.catalog_contract.trino_catalog_name
    supported_catalogs  = ["glue"]
    active_catalog_type = local.catalog_contract.catalog_type
    storage_ref         = local.storage_contract.logical_name
    catalog_ref         = local.catalog_contract.logical_name
    ingress_mode        = "cluster-internal"
    future_adapter_shapes = [
      "query.athena",
    ]
  }

  orchestration_contract = {
    provider       = local.aws_provider_name
    implementation = "orchestration.dagster_on_eks"
    adapter        = "orchestration.dagster_on_eks"
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
    supported_catalogs        = ["glue"]
    active_catalog_type       = local.catalog_contract.catalog_type
    storage_ref               = local.storage_contract.logical_name
    catalog_ref               = local.catalog_contract.logical_name
    artifact_bucket_ref       = "ops_artifacts"
    local_only                = false
    poc_only                  = true
  }

  artifact_registry_contract = {
    provider                  = local.aws_provider_name
    implementation            = "artifacts.aws_ecr"
    adapter                   = "artifacts.aws_ecr"
    logical_name              = "runtime_images"
    project_code_image        = "${var.project_code_image_repository}:${var.project_code_image_tag}"
    project_code_image_policy = var.project_code_image_pull_policy
    superset_image            = "${var.superset_image_repository}:${var.superset_image_tag}"
    superset_image_policy     = var.superset_image_pull_policy
    distribution_mode         = "registry-push"
    target_cluster            = local.kubernetes_platform_contract.cluster_name
    local_only                = false
    poc_only                  = true
  }

  artifact_bucket_contract = {
    provider                 = local.aws_provider_name
    implementation           = "artifacts.aws_s3_bucket"
    adapter                  = "artifacts.aws_s3_bucket"
    logical_name             = "ops_artifacts"
    bucket_name              = local.storage_contract.ops_bucket_name
    artifact_base_uri        = local.artifact_base_uri
    access_mode              = "remote"
    base_uri                 = local.floe_manifest_base_uri
    floe_manifest_base_uri   = local.floe_manifest_base_uri
    floe_report_base_uri     = local.floe_report_base_uri
    log_base_uri             = local.log_base_uri
    run_artifact_base_uri    = local.run_artifact_base_uri
    manifest_uris            = local.product_floe_manifest_uris
    distribution_mode        = "aws-s3-upload"
    storage_ref              = local.storage_contract.logical_name
    credentials_secret_name  = null
    access_key_id_key        = null
    secret_access_key_key    = null
    local_upload_access_mode = "aws-cli"
    local_only               = false
    poc_only                 = true
  }

  artifact_contract = merge(local.artifact_registry_contract, {
    implementation             = "artifacts.aws_ecr_and_s3"
    adapter                    = "artifacts.aws_ecr_and_s3"
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
    provider              = local.aws_provider_name
    implementation        = "secrets.kubernetes_secret_on_eks"
    adapter               = "secrets.kubernetes_secret_on_eks"
    backend               = "kubernetes"
    delivery_mode         = "env-from-secret"
    rotation_mode         = "manual-poc"
    references_only       = true
    local_only            = false
    poc_only              = true
    future_adapter_shapes = ["secrets.aws_secrets_manager_external_secrets"]
  }

  identity_contract = {
    provider          = local.aws_provider_name
    implementation    = "identity.aws_pod_identity"
    adapter           = "identity.aws_pod_identity"
    auth_mode         = "basic-poc"
    oidc_enabled      = false
    oidc_issuer_url   = try(local.foundation_contract.oidc_issuer_url, null)
    workload_identity = "aws-pod-identity"
    local_only        = false
    poc_only          = true
  }

  access_contract = {
    provider              = local.aws_provider_name
    implementation        = "access.kubectl_port_forward"
    adapter               = "access.kubectl_port_forward"
    ingress_mode          = "port-forward"
    internal_access_mode  = "cluster-dns"
    external_access_mode  = "localhost-port-forward"
    tls_mode              = "none-poc"
    local_only            = false
    poc_only              = true
    future_adapter_shapes = ["access.aws_load_balancer_controller", "access.private_dns"]
  }

  observability_contract = {
    provider              = local.aws_provider_name
    implementation        = "observability.object_log_archive_on_eks"
    adapter               = "observability.object_log_archive_on_eks"
    metrics_enabled       = false
    tracing_enabled       = false
    logs_mode             = "s3-object-archive"
    log_base_uri          = local.log_base_uri
    compute_log_uri       = "${local.log_base_uri}/dagster/compute"
    kubernetes_log_uri    = "${local.log_base_uri}/k8s"
    artifact_bucket_ref   = local.artifact_bucket_contract.logical_name
    local_only            = false
    poc_only              = true
    future_adapter_shapes = ["observability.cloudwatch", "observability.managed_prometheus"]
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
    error_message = "The AWS POC platform kube context must come from the AWS foundation contract."
  }
}

check "aws_contract_adapters_are_explicit" {
  assert {
    condition = alltrue([
      local.foundation_contract.provider == local.aws_provider_name && local.foundation_contract.implementation != "" && local.foundation_contract.adapter != "",
      local.kubernetes_platform_contract.provider == local.aws_provider_name && local.kubernetes_platform_contract.implementation != "" && local.kubernetes_platform_contract.adapter != "",
      local.storage_contract.provider == local.aws_provider_name && local.storage_contract.implementation != "" && local.storage_contract.adapter != "",
      local.metadata_database_contract.provider == local.aws_provider_name && local.metadata_database_contract.implementation != "" && local.metadata_database_contract.adapter != "",
      local.catalog_contract.provider == local.aws_provider_name && local.catalog_contract.implementation != "" && local.catalog_contract.adapter != "",
      local.query_contract.provider == local.aws_provider_name && local.query_contract.implementation != "" && local.query_contract.adapter != "",
      local.orchestration_contract.provider == local.aws_provider_name && local.orchestration_contract.implementation != "" && local.orchestration_contract.adapter != "",
      local.governance_contract.provider == local.aws_provider_name && local.governance_contract.implementation != "" && local.governance_contract.adapter != "",
      local.reporting_contract.provider == local.aws_provider_name && local.reporting_contract.implementation != "" && local.reporting_contract.adapter != "",
      local.artifact_registry_contract.provider == local.aws_provider_name && local.artifact_registry_contract.implementation != "" && local.artifact_registry_contract.adapter != "",
      local.artifact_bucket_contract.provider == local.aws_provider_name && local.artifact_bucket_contract.implementation != "" && local.artifact_bucket_contract.adapter != "",
      local.secrets_contract.provider == local.aws_provider_name && local.secrets_contract.implementation != "" && local.secrets_contract.adapter != "",
      local.identity_contract.provider == local.aws_provider_name && local.identity_contract.implementation != "" && local.identity_contract.adapter != "",
      local.access_contract.provider == local.aws_provider_name && local.access_contract.implementation != "" && local.access_contract.adapter != "",
      local.observability_contract.provider == local.aws_provider_name && local.observability_contract.implementation != "" && local.observability_contract.adapter != "",
    ])
    error_message = "Every AWS POC provider contract must declare provider, implementation, and adapter."
  }
}

check "aws_poc_uses_managed_services" {
  assert {
    condition = alltrue([
      local.storage_contract.implementation == "storage.aws_s3",
      local.metadata_database_contract.implementation == "metadata_database.aws_rds_postgresql",
      local.catalog_contract.implementation == "catalog.aws_glue",
      local.artifact_registry_contract.implementation == "artifacts.aws_ecr",
      local.artifact_bucket_contract.distribution_mode == "aws-s3-upload",
    ])
    error_message = "The AWS POC must use S3, RDS PostgreSQL, Glue, ECR, and S3 artifact upload."
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
