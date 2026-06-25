output "aws_region" {
  description = "AWS region."
  value       = var.aws_region
}

output "aws_account_id" {
  description = "AWS account ID."
  value       = data.aws_caller_identity.current.account_id
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.this.name
}

output "kube_context" {
  description = "Expected kubeconfig context after aws eks update-kubeconfig --alias."
  value       = aws_eks_cluster.this.name
}

output "kubeconfig_path" {
  description = "Kubeconfig path used by platform Terraform."
  value       = local.kubeconfig_path
}

output "vpc_id" {
  description = "POC VPC ID."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "POC VPC CIDR block."
  value       = aws_vpc.this.cidr_block
}

output "subnet_ids" {
  description = "EKS subnet IDs."
  value       = values(aws_subnet.public)[*].id
}

output "oidc_issuer_url" {
  description = "EKS OIDC issuer URL."
  value       = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for IRSA roles."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_provider_url_without_scheme" {
  description = "OIDC provider URL without https://, used in IRSA trust policies."
  value       = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

output "project_code_ecr_repository_url" {
  description = "ECR repository URL for the project-code runtime image."
  value       = aws_ecr_repository.project_code.repository_url
}

output "superset_ecr_repository_url" {
  description = "ECR repository URL for the custom Superset runtime image."
  value       = aws_ecr_repository.superset.repository_url
}

output "foundation_contract" {
  description = "Provider-neutral AWS EKS foundation contract."
  value = {
    provider                         = "aws"
    implementation                   = "eks"
    adapter                          = "foundation.eks"
    cluster_name                     = aws_eks_cluster.this.name
    kube_context                     = aws_eks_cluster.this.name
    kubeconfig_path                  = local.kubeconfig_path
    aws_region                       = var.aws_region
    aws_account_id                   = data.aws_caller_identity.current.account_id
    cluster_type                     = "eks"
    vpc_id                           = aws_vpc.this.id
    vpc_cidr_block                   = aws_vpc.this.cidr_block
    subnet_ids                       = values(aws_subnet.public)[*].id
    oidc_issuer_enabled              = true
    oidc_issuer_url                  = aws_eks_cluster.this.identity[0].oidc[0].issuer
    oidc_provider_arn                = aws_iam_openid_connect_provider.this.arn
    oidc_provider_url_without_scheme = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
    workload_identity_enabled        = true
    ecr_project_code_repository_url  = aws_ecr_repository.project_code.repository_url
    ecr_superset_repository_url      = aws_ecr_repository.superset.repository_url
  }
}
