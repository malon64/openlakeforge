# Sandbox/account-specific configuration for the groupeonepoint AWS lab account.
# Passed explicitly via `terraform -var-file=sandbox.tfvars` (see scripts/aws/foundation/*.sh).
# Keep account-mandated naming and tagging here rather than in variable defaults so the
# module stays portable across accounts.

# IAM roles/resources must be prefixed "limited-" in this sandbox; roles are named
# "${cluster_name}-..." so prefixing the cluster name satisfies the guardrail.
cluster_name = "limited-eks-openlakeforge-poc"

# Mandatory tags on every resource (case-sensitive). Untagged resources may be
# deleted without notice. Env must be one of PROD/DEV/POC; IaC one of Terraform/CloudFormation/Manual.
default_tags = {
  Project   = "openlakeforge"
  Owner     = "a.metwalli@groupeonepoint.com"
  Requester = "a.metwalli@groupeonepoint.com"
  Env       = "POC"
  IaC       = "Terraform"
}
