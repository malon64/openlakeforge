.PHONY: help tree check-structure check-components check-contracts check-infra check-project-code check-dbt check-lockfiles release-check release-bundle floe-manifest floe-manifest-upload dbt-parse project-code-image project-code-load superset-image superset-load superset-reports-deploy superset-reports-export openmetadata-metadata-deploy local-foundation-up local-foundation-down local-platform-up local-platform-down local-artifacts-deploy local-up local-down local-status local-forward local-prefetch local-e2e local-slim-platform-up local-slim-artifacts-deploy local-slim-up local-slim-e2e local-slim-smoke local-slim-down azure-foundation-up azure-platform-up azure-platform-down azure-artifacts-deploy azure-up azure-status azure-forward azure-e2e azure-down azure-foundation-down aws-foundation-up aws-platform-up aws-platform-down aws-artifacts-deploy aws-up aws-status aws-forward aws-e2e aws-down aws-foundation-down

NAMESPACE ?= lakehouse
CLUSTER_NAME ?= openlakeforge-local
KUBE_CONTEXT ?= kind-$(CLUSTER_NAME)
LOCAL_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/local.yaml
LOCAL_TFVARS_FILE ?=
PROJECT_CODE_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/project-code
PROJECT_CODE_IMAGE_TAG ?= local
PROJECT_CODE_IMAGE_PULL_POLICY ?= Never
ENABLE_GOVERNANCE ?= true
ENABLE_ANALYTICS ?= true
E2E_SUITE ?= full
SMOKE_TIMEOUT_SECONDS ?= 2700
SUPERSET_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/superset
SUPERSET_IMAGE_TAG ?= local
SUPERSET_IMAGE_PULL_POLICY ?= Never
AZURE_CLUSTER_NAME ?= aks-openlakeforge-poc
AZURE_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/azure.yaml
AZURE_NODE_COUNT ?= 3
AZURE_ACR_NAME_PREFIX ?= openlakeforgepoc
AZURE_IMAGE_TAG ?= azure-$(shell git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
AZURE_PROJECT_CODE_IMAGE_REPOSITORY ?=
AZURE_PROJECT_CODE_IMAGE_TAG ?= $(AZURE_IMAGE_TAG)
AZURE_SUPERSET_IMAGE_REPOSITORY ?=
AZURE_SUPERSET_IMAGE_TAG ?= $(AZURE_IMAGE_TAG)
AWS_REGION ?= eu-west-1
# Runtime cluster name / kube-context. Must match cluster_name in
# infra/terraform/foundations/aws-eks/sandbox.tfvars (sandbox requires a limited- prefix).
AWS_CLUSTER_NAME ?= limited-eks-openlakeforge-poc
AWS_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/aws.yaml
AWS_NODE_DESIRED_SIZE ?= 3
AWS_NODE_MIN_SIZE ?= 1
AWS_NODE_MAX_SIZE ?= 4
AWS_NODE_INSTANCE_TYPES ?= m7i.large
AWS_IMAGE_TAG ?= aws-$(shell git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
AWS_PROJECT_CODE_IMAGE_REPOSITORY ?=
AWS_PROJECT_CODE_IMAGE_TAG ?= $(AWS_IMAGE_TAG)
AWS_SUPERSET_IMAGE_REPOSITORY ?=
AWS_SUPERSET_IMAGE_TAG ?= $(AWS_IMAGE_TAG)
AZURE_OLF_FLAGS = --provider azure --namespace $(NAMESPACE) --kubeconfig-path "$(AZURE_KUBECONFIG_PATH)"
AZURE_ENV = AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) AZURE_NODE_COUNT=$(AZURE_NODE_COUNT) AZURE_ACR_NAME_PREFIX=$(AZURE_ACR_NAME_PREFIX) AZURE_TFVARS_FILE="$(AZURE_TFVARS_FILE)" AZURE_IMAGE_TAG=$(AZURE_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AZURE_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AZURE_PROJECT_CODE_IMAGE_TAG)" SUPERSET_IMAGE_REPOSITORY="$(AZURE_SUPERSET_IMAGE_REPOSITORY)" SUPERSET_IMAGE_TAG="$(AZURE_SUPERSET_IMAGE_TAG)"
AWS_OLF_FLAGS = --provider aws --namespace $(NAMESPACE) --kubeconfig-path "$(AWS_KUBECONFIG_PATH)"
AWS_ENV = AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) AWS_NODE_DESIRED_SIZE=$(AWS_NODE_DESIRED_SIZE) AWS_NODE_MIN_SIZE=$(AWS_NODE_MIN_SIZE) AWS_NODE_MAX_SIZE=$(AWS_NODE_MAX_SIZE) AWS_NODE_INSTANCE_TYPES=$(AWS_NODE_INSTANCE_TYPES) AWS_IMAGE_TAG=$(AWS_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AWS_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AWS_PROJECT_CODE_IMAGE_TAG)" SUPERSET_IMAGE_REPOSITORY="$(AWS_SUPERSET_IMAGE_REPOSITORY)" SUPERSET_IMAGE_TAG="$(AWS_SUPERSET_IMAGE_TAG)"

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' '  make check-components  Validate release catalog and immutable inputs'
	@printf '%s\n' '  make check-contracts  Validate provider contract compatibility'
	@printf '%s\n' '  make check-infra      Validate Terraform and render Helm values'
	@printf '%s\n' '  make check-project-code  Validate the project-code Dagster package'
	@printf '%s\n' '  make check-dbt        Validate all product dbt-trino projects'
	@printf '%s\n' '  make check-lockfiles  Validate Python lockfiles are in sync with their pyproject.toml'
	@printf '%s\n' '  make release-check    Validate release readiness (all check-* targets plus olf release check)'
	@printf '%s\n' '  make release-bundle   Build a local release bundle (manifest, checksums, matrix, SBOMs) for inspection'
	@printf '%s\n' '  make floe-manifest   Generate domain Floe Dagster manifests'
	@printf '%s\n' '  make floe-manifest-upload  Upload domain Floe manifests to the local ops bucket'
	@printf '%s\n' '  make dbt-parse       Generate product dbt manifests'
	@printf '%s\n' '  make superset-reports-deploy  Deploy product Superset report assets'
	@printf '%s\n' '  make superset-reports-export  Export edited Superset report assets'
	@printf '%s\n' '  make openmetadata-metadata-deploy  Deploy OpenMetadata domain/data-product assets'
	@printf '%s\n' ''
	@printf '%s\n' 'Local stack:'
	@printf '%s\n' '  make local-foundation-up    Terraform-create the local kind foundation'
	@printf '%s\n' '  make project-code-image  Build ghcr.io/openlakeforge/project-code:local'
	@printf '%s\n' '  make project-code-load   Load the project-code image into kind'
	@printf '%s\n' '  make superset-image   Build ghcr.io/openlakeforge/superset:local'
	@printf '%s\n' '  make superset-load    Load the Superset image into kind'
	@printf '%s\n' '  make local-foundation-down  Terraform-destroy the local kind foundation'
	@printf '%s\n' '  make local-prefetch    Pre-pull heavy images (OpenSearch, OM ingestion, Superset helpers) into kind'
	@printf '%s\n' '  make local-platform-up  Apply local lakehouse platform services'
	@printf '%s\n' '  make local-platform-down  Terraform-destroy local lakehouse platform services'
	@printf '%s\n' '  make local-artifacts-deploy  Deploy dynamic local/CD artifacts'
	@printf '%s\n' '  make local-up         Full wrapper: foundation, prefetch, platform, artifacts'
	@printf '%s\n' '  make local-slim-up    Slim wrapper: full data path without OpenMetadata or Superset'
	@printf '%s\n' '  make local-slim-e2e   Validate the slim profile (reports disabled assertions)'
	@printf '%s\n' '  make local-slim-smoke Deploy the slim profile and validate one product within 45 minutes'
	@printf '%s\n' '  make local-down       Full teardown wrapper: platform, foundation'
	@printf '%s\n' '  make local-status     Show pod and service status in the configured namespace'
	@printf '%s\n' '  make local-forward    Port-forward all services to localhost (Dagster :3000, Superset :8088, OpenMetadata :8585, Trino :8080, Polaris :8181, S3 :9000, SeaweedFS Filer :8888, Master :9333)'
	@printf '%s\n' '  make local-e2e        Run local end-to-end validation through olf'
	@printf '%s\n' ''
	@printf '%s\n' 'Azure AKS POC stack:'
	@printf '%s\n' '  make azure-foundation-up    Terraform-create the Azure AKS and ACR foundation'
	@printf '%s\n' '  make azure-platform-up      Build/push Superset image, then apply AKS platform services'
	@printf '%s\n' '  make azure-platform-down    Terraform-destroy AKS platform services, leaving AKS/ACR'
	@printf '%s\n' '  make azure-artifacts-deploy Deploy Floe manifests, project-code image, Superset reports, and OpenMetadata metadata'
	@printf '%s\n' '  make azure-up               Full wrapper: foundation, platform, artifacts'
	@printf '%s\n' '  make azure-status           Show pod and service status in the configured namespace'
	@printf '%s\n' '  make azure-forward          Port-forward all Azure POC services to localhost'
	@printf '%s\n' '  make azure-e2e              Run Azure POC end-to-end validation'
	@printf '%s\n' '  make azure-down             Full teardown wrapper: platform, foundation'
	@printf '%s\n' '  make azure-foundation-down  Terraform-destroy the Azure AKS and ACR foundation'
	@printf '%s\n' ''
	@printf '%s\n' 'AWS EKS managed-services POC stack:'
	@printf '%s\n' '  make aws-foundation-up      Terraform-create AWS VPC, EKS, ECR, and IRSA foundation'
	@printf '%s\n' '  make aws-platform-up        Build/push Superset image, then apply EKS platform services'
	@printf '%s\n' '  make aws-platform-down      Terraform-destroy AWS platform services, leaving EKS/ECR'
	@printf '%s\n' '  make aws-artifacts-deploy   Deploy Floe manifests, project-code image, Superset reports, and OpenMetadata metadata'
	@printf '%s\n' '  make aws-up                 Full wrapper: foundation, platform, artifacts'
	@printf '%s\n' '  make aws-status             Show pod and service status in the configured namespace'
	@printf '%s\n' '  make aws-forward            Port-forward AWS POC services to localhost'
	@printf '%s\n' '  make aws-e2e                Run AWS POC end-to-end validation'
	@printf '%s\n' '  make aws-down               Full teardown wrapper: platform, foundation'
	@printf '%s\n' '  make aws-foundation-down    Terraform-destroy AWS EKS, ECR, and VPC resources'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/test/check-structure.sh

check-components:
	@bash scripts/test/check-components.sh

check-contracts:
	@uv run --project tools/olf --locked olf contracts check --repo-root .

check-infra:
	@bash scripts/test/check-infra.sh

check-project-code:
	@bash scripts/test/check-project-code.sh

check-dbt:
	@bash scripts/test/check-dbt.sh

check-lockfiles:
	@bash scripts/test/check-lockfiles.sh

release-check: check-structure check-components check-contracts check-infra check-project-code check-dbt check-lockfiles
	@uv run --project tools/olf --locked olf release check

release-bundle:
	@bash scripts/release/build-bundle.sh

floe-manifest:
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/floe-manifest.sh

floe-manifest-upload:
	@NAMESPACE=$(NAMESPACE) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" bash scripts/artifacts/olf.sh artifacts upload-manifests --via port-forward

dbt-parse:
	@bash scripts/artifacts/dbt-parse.sh

project-code-image:
	@PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/images/build-project-code.sh

project-code-load:
	@CLUSTER_NAME=$(CLUSTER_NAME) PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/images/load-project-code.sh

superset-image:
	@SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) bash scripts/local/images/build-superset.sh

superset-load:
	@CLUSTER_NAME=$(CLUSTER_NAME) SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) bash scripts/local/images/load-superset.sh

superset-reports-deploy:
	@NAMESPACE=$(NAMESPACE) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" bash scripts/artifacts/olf.sh superset deploy-reports

superset-reports-export:
	@NAMESPACE=$(NAMESPACE) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" bash scripts/artifacts/olf.sh superset export-reports

openmetadata-metadata-deploy:
	@NAMESPACE=$(NAMESPACE) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" bash scripts/artifacts/olf.sh openmetadata deploy-metadata

# All local-* deployment lifecycle targets below are thin delegates to the
# Python `olf` deployment engine (tools/olf/olf/deployment) - Make holds no
# Terraform/Docker/kubectl/Helm orchestration logic itself. `--profile`
# (full/slim) drives Full-vs-Slim behavior (image selection, governance/
# analytics layers, and Terraform var-file selection) through typed
# configuration; `LOCAL_TFVARS_FILE`, if set, still overrides the tfvars file.
OLF_BIN ?= uv run --project tools/olf --locked olf
LOCAL_PROFILE ?= full
LOCAL_OLF_FLAGS = --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path "$(LOCAL_KUBECONFIG_PATH)"
LOCAL_VAR_FILE_FLAG = $(if $(LOCAL_TFVARS_FILE),--var-file "$(LOCAL_TFVARS_FILE)",)
LOCAL_IMAGE_ENV = PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) PROJECT_CODE_IMAGE_PULL_POLICY=$(PROJECT_CODE_IMAGE_PULL_POLICY) SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) SUPERSET_IMAGE_PULL_POLICY=$(SUPERSET_IMAGE_PULL_POLICY)

local-foundation-up:
	@$(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase foundation

local-foundation-down:
	@$(OLF_BIN) destroy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase foundation

local-platform-up:
	@$(LOCAL_IMAGE_ENV) $(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase platform $(LOCAL_VAR_FILE_FLAG)

local-artifacts-deploy:
	@$(LOCAL_IMAGE_ENV) $(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase artifacts

local-up:
	@$(LOCAL_IMAGE_ENV) $(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile full $(LOCAL_VAR_FILE_FLAG)

local-slim-platform-up:
	@$(MAKE) local-platform-up LOCAL_PROFILE=slim

local-slim-artifacts-deploy:
	@$(MAKE) local-artifacts-deploy LOCAL_PROFILE=slim

local-slim-up:
	@$(LOCAL_IMAGE_ENV) $(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile slim $(LOCAL_VAR_FILE_FLAG)

local-slim-e2e:
	@$(MAKE) local-e2e ENABLE_GOVERNANCE=false ENABLE_ANALYTICS=false E2E_SUITE=$(E2E_SUITE)

local-slim-smoke:
	@uv run --project tools/olf --locked olf smoke run --timeout-seconds $(SMOKE_TIMEOUT_SECONDS)

local-slim-down:
	@$(MAKE) local-down LOCAL_PROFILE=slim

local-down:
	@$(OLF_BIN) destroy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) $(LOCAL_VAR_FILE_FLAG)

local-platform-down:
	@$(OLF_BIN) destroy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase platform $(LOCAL_VAR_FILE_FLAG)

local-status:
	@$(OLF_BIN) status $(LOCAL_OLF_FLAGS)

local-prefetch:
	@$(OLF_BIN) deploy $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE) --phase prefetch

local-forward:
	@$(OLF_BIN) forward $(LOCAL_OLF_FLAGS) --profile $(LOCAL_PROFILE)

local-e2e:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/local $(OLF_BIN) e2e run --env local --suite $(E2E_SUITE)

azure-foundation-up:
	@$(AZURE_ENV) $(OLF_BIN) deploy $(AZURE_OLF_FLAGS) --phase foundation

azure-platform-up:
	@$(AZURE_ENV) $(OLF_BIN) deploy $(AZURE_OLF_FLAGS) --phase platform

azure-artifacts-deploy:
	@$(AZURE_ENV) $(OLF_BIN) deploy $(AZURE_OLF_FLAGS) --phase artifacts

azure-up:
	@$(AZURE_ENV) $(OLF_BIN) deploy $(AZURE_OLF_FLAGS)

azure-status:
	@$(AZURE_ENV) $(OLF_BIN) status $(AZURE_OLF_FLAGS)

azure-forward:
	@$(AZURE_ENV) $(OLF_BIN) forward $(AZURE_OLF_FLAGS)

azure-e2e:
	@NAMESPACE=$(NAMESPACE) AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) KUBECONFIG="$(AZURE_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/azure-poc $(OLF_BIN) e2e run --env azure

azure-down:
	@$(AZURE_ENV) $(OLF_BIN) destroy $(AZURE_OLF_FLAGS)

azure-platform-down:
	@$(AZURE_ENV) $(OLF_BIN) destroy $(AZURE_OLF_FLAGS) --phase platform

azure-foundation-down:
	@$(AZURE_ENV) $(OLF_BIN) destroy $(AZURE_OLF_FLAGS) --phase foundation

aws-foundation-up:
	@$(AWS_ENV) $(OLF_BIN) deploy $(AWS_OLF_FLAGS) --phase foundation

aws-platform-up:
	@$(AWS_ENV) $(OLF_BIN) deploy $(AWS_OLF_FLAGS) --phase platform

aws-artifacts-deploy:
	@$(AWS_ENV) $(OLF_BIN) deploy $(AWS_OLF_FLAGS) --phase artifacts

aws-up:
	@$(AWS_ENV) $(OLF_BIN) deploy $(AWS_OLF_FLAGS)

aws-status:
	@$(AWS_ENV) $(OLF_BIN) status $(AWS_OLF_FLAGS)

aws-forward:
	@$(AWS_ENV) $(OLF_BIN) forward $(AWS_OLF_FLAGS)

aws-e2e:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) KUBECONFIG="$(AWS_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/aws-poc $(OLF_BIN) e2e run --env aws

aws-down:
	@$(AWS_ENV) $(OLF_BIN) destroy $(AWS_OLF_FLAGS)

aws-platform-down:
	@$(AWS_ENV) $(OLF_BIN) destroy $(AWS_OLF_FLAGS) --phase platform

aws-foundation-down:
	@$(AWS_ENV) $(OLF_BIN) destroy $(AWS_OLF_FLAGS) --phase foundation
