variable "cluster_name" {
  description = "Name of the local kind cluster managed by the foundation root."
  type        = string
  default     = "openlakeforge-local"
}

variable "cluster_config_path" {
  description = "Path to the kind cluster configuration file. Defaults to infra/kind/local/kind-cluster.yaml."
  type        = string
  default     = null
}

variable "kubeconfig_path" {
  description = "Kubeconfig path populated by kind. Defaults to ~/.kube/config."
  type        = string
  default     = null
}

variable "kind_wait_timeout" {
  description = "Timeout passed to kind create cluster --wait."
  type        = string
  default     = "120s"
}

variable "reset_existing_cluster" {
  description = "When true during initial creation, delete any existing kind cluster with the same name before recreating it."
  type        = bool
  default     = false
}
