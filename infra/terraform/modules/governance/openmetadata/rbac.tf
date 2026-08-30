# ServiceAccount + RBAC for the bootstrap job
resource "kubernetes_service_account_v1" "bootstrap" {
  metadata {
    name        = "openmetadata-bootstrap"
    namespace   = var.namespace
    labels      = local.labels
    annotations = var.service_account_annotations
  }
}

resource "kubernetes_role_v1" "bootstrap" {
  metadata {
    name      = "openmetadata-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }

  rule {
    api_groups = [""]
    resources  = ["secrets"]
    verbs      = ["create", "delete", "get", "patch", "update"]
  }
}

resource "kubernetes_role_binding_v1" "bootstrap" {
  metadata {
    name      = "openmetadata-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.bootstrap.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.bootstrap.metadata[0].name
    namespace = var.namespace
  }
}

# A Role only grants access inside its own namespace, so replicating the
# ingestion-bot Secret into a stage namespace needs one there too -- and so
# does revoking it from a stage that has since turned governance off, which is
# why the set spans both.
resource "kubernetes_role_v1" "bootstrap_workload" {
  for_each = toset([for namespace in concat(var.workload_namespaces, var.revoked_namespaces) : namespace if namespace != var.namespace])

  metadata {
    name      = "openmetadata-bootstrap"
    namespace = each.value
    labels    = local.labels
  }

  rule {
    api_groups = [""]
    resources  = ["secrets"]
    verbs      = ["create", "delete", "get", "patch", "update"]
  }
}

resource "kubernetes_role_binding_v1" "bootstrap_workload" {
  for_each = kubernetes_role_v1.bootstrap_workload

  metadata {
    name      = "openmetadata-bootstrap"
    namespace = each.key
    labels    = local.labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = each.value.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.bootstrap.metadata[0].name
    namespace = var.namespace
  }
}
