# ADR 0016: EKS Pod Identity instead of IRSA for the AWS POC

## Status

Accepted. Amends the identity decision in
[ADR 0015](0015-aws-eks-managed-services-poc.md).

## Context

ADR 0015 chose `identity.aws_irsa` (IAM Roles for Service Accounts) for workload
access to S3 and Glue. IRSA requires an IAM OIDC identity provider created from
the EKS cluster issuer (`aws_iam_openid_connect_provider`).

The AWS POC runs in a shared lab sandbox (groupeonepoint, account
`883553345052`) governed by Service Control Policies. Two sandbox guardrails
collide with IRSA:

- IAM roles and related resources must be named with a `limited-` prefix.
- `iam:CreateOpenIDConnectProvider` is explicitly denied, and an OIDC provider
  has no name to which the `limited-` prefix could be applied.

Resource-scoped `iam simulate-principal-policy` confirmed the OIDC provider
creation is denied even with an exact ARN, while `iam:CreateRole`,
`iam:AttachRolePolicy`, `iam:PassRole`, and `eks:*` succeed on `limited-`
resources. So IRSA cannot be provisioned here, but per-service IAM roles can.

## Decision

Use **EKS Pod Identity** for workload identity instead of IRSA.

- Foundation installs the `eks-pod-identity-agent` add-on and drops the
  `aws_iam_openid_connect_provider` / `tls_certificate` resources.
- Each workload role (the managed EBS CSI driver role in the foundation; the
  shared `*-openlakeforge-workloads` role in the environment) trusts the
  `pods.eks.amazonaws.com` service principal with `sts:AssumeRole` and
  `sts:TagSession`.
- Service accounts are bound to roles with `aws_eks_pod_identity_association`
  resources instead of the `eks.amazonaws.com/role-arn` annotation. No OIDC
  provider, issuer URL, or `sub`/`aud` trust conditions are needed.
- The identity contract becomes `identity.aws_pod_identity`
  (`workload_identity = "aws-pod-identity"`, `oidc_enabled = false`); storage and
  catalog auth modes become `aws-pod-identity` / `aws-sigv4-pod-identity`. The
  foundation contract exposes `workload_identity_type = "eks-pod-identity"` and
  no longer exports `oidc_provider_arn`.

This keeps the "each service has its own basic role" model intended for the POC
and is the AWS-recommended successor to IRSA, so it aligns with the future
centralized-identity direction rather than being throwaway.

## Consequences

- The POC provisions entirely within the sandbox guardrails with no IAM org
  exception request.
- Pods consume credentials through the Pod Identity agent DaemonSet; existing
  pods need a rollout restart the first time an association is added.
- Athena and the broader provider-contract shape from ADR 0015 are unchanged;
  only the identity binding mechanism differs.
- Sandbox-specific configuration (the `limited-` cluster name and mandatory
  `Project`/`Owner`/`Requester`/`Env`/`IaC` tags) now lives in
  `sandbox.tfvars` files consumed via `-var-file`, not in module variable
  defaults, so the modules stay portable across accounts.
