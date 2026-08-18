resource "kubernetes_service_v1" "postgresql" {
  metadata {
    name      = var.release_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    selector = local.pod_labels

    port {
      port        = 5432
      target_port = "5432"
      name        = "postgresql"
    }

    type = "ClusterIP"
  }
}
