# Sandbox/account-specific configuration for the groupeonepoint AWS lab account.
# Passed explicitly via `terraform -var-file=sandbox.tfvars` (see scripts/aws/stack/*.sh).
# The limited- IAM naming is inherited from the foundation cluster_name via the
# foundation contract, so only the mandatory tags need to be set here.

# Mandatory tags on every resource (case-sensitive). Untagged resources may be
# deleted without notice. Env must be one of PROD/DEV/POC; IaC one of Terraform/CloudFormation/Manual.
default_tags = {
  Project   = "openlakeforge"
  Owner     = "a.metwalli@groupeonepoint.com"
  Requester = "a.metwalli@groupeonepoint.com"
  Env       = "POC"
  IaC       = "Terraform"
}
