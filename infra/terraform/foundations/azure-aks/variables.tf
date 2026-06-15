variable "resource_group_name" {
  description = "Azure resource group for the AKS POC foundation."
  type        = string
  default     = "rg-openlakeforge-azure-poc"
}

variable "location" {
  description = "Azure region for the AKS POC foundation."
  type        = string
  default     = "westeurope"
}

variable "cluster_name" {
  description = "AKS cluster name."
  type        = string
  default     = "aks-openlakeforge-poc"
}

variable "dns_prefix" {
  description = "Optional AKS DNS prefix. Defaults to cluster_name."
  type        = string
  default     = null
}

variable "kubernetes_version" {
  description = "Optional AKS Kubernetes version. Null uses the Azure default for the region."
  type        = string
  default     = null
}

variable "node_count" {
  description = "Default AKS node pool size."
  type        = number
  default     = 3
}

variable "node_vm_size" {
  description = "Default AKS node pool VM size."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "node_os_disk_size_gb" {
  description = "Default AKS node OS disk size in GiB."
  type        = number
  default     = 128
}

variable "acr_name_prefix" {
  description = "ACR name prefix. A random suffix is appended because ACR names are globally unique."
  type        = string
  default     = "openlakeforgepoc"
}

variable "acr_sku" {
  description = "ACR SKU for the POC registry."
  type        = string
  default     = "Basic"
}

variable "kubeconfig_path" {
  description = "Kubeconfig path populated by az aks get-credentials. Defaults to ~/.kube/config."
  type        = string
  default     = null
}
