locals {
  labels = {
    "app.kubernetes.io/name"       = "postgresql"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "storage"
  }

  pod_labels = {
    "app.kubernetes.io/name"     = "postgresql"
    "app.kubernetes.io/instance" = var.release_name
  }

  host = "${var.release_name}.${var.namespace}.svc.cluster.local"
  port = 5432

  bootstrap_script = templatefile("${path.module}/templates/init.sh.tftpl", {
    enable_openmetadata = var.enable_openmetadata
    enable_superset     = var.enable_superset
  })

  bootstrap_hash = substr(sha256(jsonencode({
    script               = local.bootstrap_script
    dagster_db_name      = var.dagster_db_name
    dagster_db_user      = var.dagster_db_user
    openmetadata_db_name = var.openmetadata_db_name
    openmetadata_db_user = var.openmetadata_db_user
    enable_openmetadata  = var.enable_openmetadata
    superset_db_name     = var.superset_db_name
    superset_db_user     = var.superset_db_user
    enable_superset      = var.enable_superset
    polaris_db_name      = var.polaris_db_name
    polaris_db_user      = var.polaris_db_user
  })), 0, 12)
}
