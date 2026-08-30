locals {
  labels = {
    "app.kubernetes.io/name"       = "polaris"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "catalog"
  }

  root_client_id = "root"
  realm          = "POLARIS"
  # Namespace-qualified: stage-scoped consumers resolve this from their own
  # namespace, where a bare service name would not resolve.
  rest_uri    = "http://${var.release_name}.${var.namespace}:8181/api/catalog"
  token_uri   = "http://${var.release_name}.${var.namespace}:8181/api/catalog/v1/oauth/tokens"
  oauth_scope = "PRINCIPAL_ROLE:ALL"
  bronze_bucket_name = (
    var.storage_contract.bronze_bucket_name != null
    ? var.storage_contract.bronze_bucket_name
    : var.storage_contract.bucket_name
  )
  silver_bucket_name = (
    var.storage_contract.silver_bucket_name != null
    ? var.storage_contract.silver_bucket_name
    : var.storage_contract.bucket_name
  )
  gold_bucket_name = (
    var.storage_contract.gold_bucket_name != null
    ? var.storage_contract.gold_bucket_name
    : var.storage_contract.bucket_name
  )
  # The Job template is immutable, so a changed workload-namespace set has to
  # produce a new Job name for the credential replicas to be created.
  bootstrap_job_name = "polaris-bootstrap-${helm_release.polaris.metadata.revision}"

  # Keyed on the bootstrap job as well as the namespace set: that job
  # mints the credentials being copied, so when it re-runs and replaces
  # them, the replicas have to be rewritten or every stage namespace
  # keeps a token the service no longer accepts.
  workload_revision = substr(sha256(join(",", concat(sort(var.workload_namespaces), [local.bootstrap_job_name]))), 0, 8)

  bootstrap_annotations = {
    "openlakeforge.io/polaris-release-revision" = tostring(helm_release.polaris.metadata.revision)
    "openlakeforge.io/bootstrap-revision"       = var.bootstrap_revision
  }
}
