terraform {
  required_version = ">= 1.6.0"
}

locals {
  repo_root                   = abspath("${path.root}/../../../..")
  default_cluster_config_path = "${local.repo_root}/infra/kind/local/kind-cluster.yaml"
  cluster_config_path         = var.cluster_config_path != null ? abspath(pathexpand(var.cluster_config_path)) : local.default_cluster_config_path
  kubeconfig_path             = var.kubeconfig_path != null ? abspath(pathexpand(var.kubeconfig_path)) : "${local.repo_root}/.tmp/kubeconfigs/local.yaml"
  kube_context                = "kind-${var.cluster_name}"
}

resource "terraform_data" "kind_cluster" {
  input = {
    cluster_name          = var.cluster_name
    cluster_config_path   = local.cluster_config_path
    cluster_config_sha256 = filesha256(local.cluster_config_path)
    kube_context          = local.kube_context
    kubeconfig_path       = local.kubeconfig_path
    repo_root             = local.repo_root
  }

  triggers_replace = [
    var.cluster_name,
    filesha256(local.cluster_config_path),
  ]

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail

      if ! kubectl version --client >/dev/null 2>&1; then
        echo "ERROR: kubectl is not executable. Install a native Linux kubectl binary and retry." >&2
        exit 1
      fi

      if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker daemon is not running or not accessible." >&2
        exit 1
      fi

      if [[ ! -f "$CLUSTER_CONFIG" ]]; then
        echo "ERROR: kind cluster config not found: $CLUSTER_CONFIG" >&2
        exit 1
      fi

      mkdir -p "$(dirname "$KUBECONFIG_PATH")"

      if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
        if [[ "$RESET_EXISTING_CLUSTER" == "true" ]]; then
          echo "==> Deleting existing kind cluster '$CLUSTER_NAME'..."
          kind delete cluster --name "$CLUSTER_NAME"
        else
          echo "Kind cluster '$CLUSTER_NAME' already exists. Reusing it."
          kind export kubeconfig --name "$CLUSTER_NAME" --kubeconfig "$KUBECONFIG_PATH"
          kubectl --kubeconfig "$KUBECONFIG_PATH" cluster-info --context "kind-$CLUSTER_NAME"
          exit 0
        fi
      fi

      echo "==> Creating kind cluster '$CLUSTER_NAME'..."
      kind create cluster \
        --name "$CLUSTER_NAME" \
        --config "$CLUSTER_CONFIG" \
        --kubeconfig "$KUBECONFIG_PATH" \
        --wait "$KIND_WAIT_TIMEOUT"

      kubectl --kubeconfig "$KUBECONFIG_PATH" get nodes --context "kind-$CLUSTER_NAME"
    EOT

    interpreter = ["/usr/bin/env", "bash", "-c"]

    environment = {
      CLUSTER_CONFIG         = local.cluster_config_path
      CLUSTER_NAME           = var.cluster_name
      KIND_WAIT_TIMEOUT      = var.kind_wait_timeout
      KUBECONFIG_PATH        = local.kubeconfig_path
      RESET_EXISTING_CLUSTER = tostring(var.reset_existing_cluster)
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -euo pipefail

      if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
        echo "==> Deleting kind cluster '$CLUSTER_NAME'..."
        kind delete cluster --name "$CLUSTER_NAME"
      else
        echo "Kind cluster '$CLUSTER_NAME' does not exist."
      fi
    EOT

    interpreter = ["/usr/bin/env", "bash", "-c"]

    environment = {
      CLUSTER_NAME = self.input.cluster_name
    }
  }
}
