output "resource_group_name" {
  description = "Azure resource group name."
  value       = local.resource_group_name
}

output "location" {
  description = "Azure region."
  value       = local.resource_group_location
}

output "cluster_name" {
  description = "AKS cluster name."
  value       = azurerm_kubernetes_cluster.this.name
}

output "kube_context" {
  description = "Expected kubeconfig context after az aks get-credentials."
  value       = azurerm_kubernetes_cluster.this.name
}

output "kubeconfig_path" {
  description = "Kubeconfig path used by platform Terraform."
  value       = local.kubeconfig_path
}

output "acr_name" {
  description = "Azure Container Registry name."
  value       = azurerm_container_registry.this.name
}

output "acr_login_server" {
  description = "Azure Container Registry login server."
  value       = azurerm_container_registry.this.login_server
}

output "oidc_issuer_url" {
  description = "AKS OIDC issuer URL for future Workload Identity integration."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "foundation_contract" {
  description = "Provider-neutral Azure AKS foundation contract."
  value = {
    provider                  = "azure"
    implementation            = "aks"
    adapter                   = "foundation.aks"
    cluster_name              = azurerm_kubernetes_cluster.this.name
    kube_context              = azurerm_kubernetes_cluster.this.name
    kubeconfig_path           = local.kubeconfig_path
    resource_group_name       = local.resource_group_name
    location                  = local.resource_group_location
    cluster_type              = "aks"
    acr_name                  = azurerm_container_registry.this.name
    acr_login_server          = azurerm_container_registry.this.login_server
    oidc_issuer_enabled       = true
    oidc_issuer_url           = azurerm_kubernetes_cluster.this.oidc_issuer_url
    workload_identity_enabled = true
  }
}
