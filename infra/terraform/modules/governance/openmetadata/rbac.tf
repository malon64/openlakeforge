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
