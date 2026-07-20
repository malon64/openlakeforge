.PHONY: help tree check-structure check-components check-contracts check-infra check-project-code check-dbt floe-manifest floe-manifest-upload dbt-parse project-code-image project-code-load superset-image superset-load superset-reports-deploy superset-reports-export openmetadata-metadata-deploy local-foundation-up local-foundation-down local-platform-up local-platform-down local-artifacts-deploy local-up local-down local-status local-forward local-prefetch local-e2e azure-foundation-up azure-platform-up azure-platform-down azure-artifacts-deploy azure-up azure-forward azure-e2e azure-down azure-foundation-down aws-foundation-up aws-platform-up aws-platform-down aws-artifacts-deploy aws-up aws-forward aws-e2e aws-down aws-foundation-down

NAMESPACE ?= lakehouse
CLUSTER_NAME ?= openlakeforge-local
KUBE_CONTEXT ?= kind-$(CLUSTER_NAME)
LOCAL_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/local.yaml
PROJECT_CODE_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/project-code
PROJECT_CODE_IMAGE_TAG ?= local
PROJECT_CODE_IMAGE_PULL_POLICY ?= Never
SUPERSET_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/superset
SUPERSET_IMAGE_TAG ?= local
SUPERSET_IMAGE_PULL_POLICY ?= Never
AZURE_CLUSTER_NAME ?= aks-openlakeforge-poc
AZURE_KUBE_CONTEXT ?= $(AZURE_CLUSTER_NAME)
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
AWS_KUBE_CONTEXT ?= $(AWS_CLUSTER_NAME)
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

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' '  make check-components  Validate release catalog and immutable inputs'
	@printf '%s\n' '  make check-contracts  Validate provider contract compatibility'
	@printf '%s\n' '  make check-infra      Validate Terraform and render Helm values'
	@printf '%s\n' '  make check-project-code  Validate the project-code Dagster package'
	@printf '%s\n' '  make check-dbt        Validate all product dbt-trino projects'
	@printf '%s\n' '  make floe-manifest   Generate product Floe Dagster manifests'
	@printf '%s\n' '  make floe-manifest-upload  Upload product Floe manifests to the local ops bucket'
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
	@bash scripts/test/check-contracts.sh

check-infra:
	@bash scripts/test/check-infra.sh

check-project-code:
	@bash scripts/test/check-project-code.sh

check-dbt:
	@bash scripts/test/check-dbt.sh

floe-manifest:
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/floe-manifest.sh

floe-manifest-upload:
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/olf.sh artifacts upload-manifests --via port-forward

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
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/olf.sh superset deploy-reports

superset-reports-export:
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/olf.sh superset export-reports

openmetadata-metadata-deploy:
	@NAMESPACE=$(NAMESPACE) bash scripts/artifacts/olf.sh openmetadata deploy-metadata

local-foundation-up:
	@CLUSTER_NAME=$(CLUSTER_NAME) KUBECONFIG_PATH="$(LOCAL_KUBECONFIG_PATH)" bash scripts/local/foundation/up.sh

local-foundation-down:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG_PATH="$(LOCAL_KUBECONFIG_PATH)" bash scripts/local/foundation/down.sh

local-platform-up:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG_PATH="$(LOCAL_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=local PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) PROJECT_CODE_IMAGE_PULL_POLICY=$(PROJECT_CODE_IMAGE_PULL_POLICY) SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) SUPERSET_IMAGE_PULL_POLICY=$(SUPERSET_IMAGE_PULL_POLICY) bash scripts/local/stack/platform-up.sh

local-artifacts-deploy:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG_PATH="$(LOCAL_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=local PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/stack/deploy-artifacts.sh

local-up:
	@$(MAKE) local-foundation-up
	@$(MAKE) local-prefetch
	@$(MAKE) local-platform-up
	@$(MAKE) local-artifacts-deploy

local-down:
	@$(MAKE) local-platform-down
	@$(MAKE) local-foundation-down

local-platform-down:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG_PATH="$(LOCAL_KUBECONFIG_PATH)" bash scripts/local/stack/teardown.sh

local-status:
	@echo "=== Pods ===" && KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" kubectl --context $(KUBE_CONTEXT) get pods -n $(NAMESPACE)
	@echo "" && echo "=== Services ===" && KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" kubectl --context $(KUBE_CONTEXT) get svc -n $(NAMESPACE)
	@echo "" && echo "=== PVCs ===" && KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" kubectl --context $(KUBE_CONTEXT) get pvc -n $(NAMESPACE)

local-prefetch:
	@echo "Pre-pulling heavy images into kind to avoid Helm timeouts..."
	@CLUSTER_NAME=$(CLUSTER_NAME) bash scripts/local/cluster/prefetch-images.sh

local-forward:
	@echo "Starting port-forwards (Ctrl-C to stop all)..."
	@echo "  Dagster UI:       http://localhost:3000"
	@echo "  Superset UI:      http://localhost:8088  (admin / admin)"
	@echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
	@echo "  Trino UI:         http://localhost:8080"
	@echo "  Polaris API:      http://localhost:8181"
	@echo "  SeaweedFS S3:     http://localhost:9000"
	@echo "  SeaweedFS Filer:  http://localhost:8888"
	@echo "  SeaweedFS Master: http://localhost:9333"
	@set -e; export KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)"; \
	context="$(KUBE_CONTEXT)"; \
	dagster_pod="$$(kubectl --context $$context get pods -n $(NAMESPACE) -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep 'dagster-webserver' | head -n 1)"; \
	kubectl --context $$context port-forward svc/seaweedfs-s3 9000:8333 -n $(NAMESPACE) & \
	seaweedfs_pid=$$!; \
	kubectl --context $$context port-forward svc/polaris 8181:8181 -n $(NAMESPACE) & \
	polaris_pid=$$!; \
	kubectl --context $$context port-forward svc/trino 8080:8080 -n $(NAMESPACE) & \
	trino_pid=$$!; \
	kubectl --context $$context port-forward pod/$$dagster_pod 3000:80 -n $(NAMESPACE) & \
	dagster_pid=$$!; \
	kubectl --context $$context port-forward svc/superset 8088:8088 -n $(NAMESPACE) & \
	superset_pid=$$!; \
	kubectl --context $$context port-forward svc/openmetadata 8585:8585 -n $(NAMESPACE) & \
	om_pid=$$!; \
	kubectl --context $$context port-forward svc/seaweedfs-filer-client 8888:8888 -n $(NAMESPACE) & \
	seaweedfs_filer_pid=$$!; \
	kubectl --context $$context port-forward svc/seaweedfs-master 9333:9333 -n $(NAMESPACE) & \
	seaweedfs_master_pid=$$!; \
	trap 'kill $$seaweedfs_pid $$polaris_pid $$trino_pid $$dagster_pid $$superset_pid $$om_pid $$seaweedfs_filer_pid $$seaweedfs_master_pid 2>/dev/null || true' INT TERM EXIT; \
	wait

local-e2e:
	@NAMESPACE=$(NAMESPACE) CLUSTER_NAME=$(CLUSTER_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) KUBECONFIG="$(LOCAL_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/local bash scripts/artifacts/olf.sh e2e run --env local

azure-foundation-up:
	@AZURE_TFVARS_FILE="$(AZURE_TFVARS_FILE)" AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) AZURE_NODE_COUNT=$(AZURE_NODE_COUNT) AZURE_ACR_NAME_PREFIX=$(AZURE_ACR_NAME_PREFIX) KUBECONFIG_PATH="$(AZURE_KUBECONFIG_PATH)" bash scripts/azure/foundation/up.sh

azure-platform-up:
	@NAMESPACE=$(NAMESPACE) AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) KUBE_CONTEXT=$(AZURE_KUBE_CONTEXT) KUBECONFIG_PATH="$(AZURE_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=azure AZURE_IMAGE_TAG=$(AZURE_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AZURE_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AZURE_PROJECT_CODE_IMAGE_TAG)" PROJECT_CODE_IMAGE_PULL_POLICY=Always SUPERSET_IMAGE_REPOSITORY="$(AZURE_SUPERSET_IMAGE_REPOSITORY)" SUPERSET_IMAGE_TAG="$(AZURE_SUPERSET_IMAGE_TAG)" SUPERSET_IMAGE_PULL_POLICY=Always bash scripts/azure/stack/platform-up.sh

azure-artifacts-deploy:
	@NAMESPACE=$(NAMESPACE) AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) KUBE_CONTEXT=$(AZURE_KUBE_CONTEXT) KUBECONFIG_PATH="$(AZURE_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=azure AZURE_IMAGE_TAG=$(AZURE_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AZURE_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AZURE_PROJECT_CODE_IMAGE_TAG)" bash scripts/azure/stack/deploy-artifacts.sh

azure-up:
	@set -e; export KUBECONFIG="$(AZURE_KUBECONFIG_PATH)"; \
	image_tag="$(AZURE_IMAGE_TAG)"; \
	$(MAKE) azure-foundation-up AZURE_IMAGE_TAG="$$image_tag"; \
	$(MAKE) azure-platform-up AZURE_IMAGE_TAG="$$image_tag"; \
	$(MAKE) azure-artifacts-deploy AZURE_IMAGE_TAG="$$image_tag"

azure-forward:
	@echo "Starting Azure POC port-forwards (Ctrl-C to stop all)..."
	@echo "  Dagster UI:       http://localhost:3000"
	@echo "  Superset UI:      http://localhost:8088  (admin / admin)"
	@echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
	@echo "  Trino UI:         http://localhost:8080"
	@echo "  Polaris API:      http://localhost:8181"
	@echo "  SeaweedFS S3:     http://localhost:9000"
	@set -e; \
	context="$(AZURE_KUBE_CONTEXT)"; \
	kubectl --context $$context port-forward svc/seaweedfs-s3 9000:8333 -n $(NAMESPACE) & \
	seaweedfs_pid=$$!; \
	kubectl --context $$context port-forward svc/polaris 8181:8181 -n $(NAMESPACE) & \
	polaris_pid=$$!; \
	kubectl --context $$context port-forward svc/trino 8080:8080 -n $(NAMESPACE) & \
	trino_pid=$$!; \
	kubectl --context $$context port-forward svc/dagster-dagster-webserver 3000:80 -n $(NAMESPACE) & \
	dagster_pid=$$!; \
	kubectl --context $$context port-forward svc/superset 8088:8088 -n $(NAMESPACE) & \
	superset_pid=$$!; \
	kubectl --context $$context port-forward svc/openmetadata 8585:8585 -n $(NAMESPACE) & \
	om_pid=$$!; \
	trap 'kill $$seaweedfs_pid $$polaris_pid $$trino_pid $$dagster_pid $$superset_pid $$om_pid 2>/dev/null || true' INT TERM EXIT; \
	wait

azure-e2e:
	@NAMESPACE=$(NAMESPACE) AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) KUBE_CONTEXT=$(AZURE_KUBE_CONTEXT) KUBECONFIG="$(AZURE_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/azure-poc bash scripts/artifacts/olf.sh e2e run --env azure

azure-down:
	@$(MAKE) azure-platform-down
	@$(MAKE) azure-foundation-down

azure-platform-down:
	@NAMESPACE=$(NAMESPACE) AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) KUBE_CONTEXT=$(AZURE_KUBE_CONTEXT) KUBECONFIG_PATH="$(AZURE_KUBECONFIG_PATH)" bash scripts/azure/stack/teardown.sh

azure-foundation-down:
	@NAMESPACE=$(NAMESPACE) AZURE_TFVARS_FILE="$(AZURE_TFVARS_FILE)" AZURE_CLUSTER_NAME=$(AZURE_CLUSTER_NAME) AZURE_NODE_COUNT=$(AZURE_NODE_COUNT) AZURE_ACR_NAME_PREFIX=$(AZURE_ACR_NAME_PREFIX) KUBE_CONTEXT=$(AZURE_KUBE_CONTEXT) KUBECONFIG_PATH="$(AZURE_KUBECONFIG_PATH)" bash scripts/azure/foundation/down.sh

aws-foundation-up:
	@AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) AWS_NODE_DESIRED_SIZE=$(AWS_NODE_DESIRED_SIZE) AWS_NODE_MIN_SIZE=$(AWS_NODE_MIN_SIZE) AWS_NODE_MAX_SIZE=$(AWS_NODE_MAX_SIZE) AWS_NODE_INSTANCE_TYPES=$(AWS_NODE_INSTANCE_TYPES) KUBECONFIG_PATH="$(AWS_KUBECONFIG_PATH)" bash scripts/aws/foundation/up.sh

aws-platform-up:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) KUBE_CONTEXT=$(AWS_KUBE_CONTEXT) KUBECONFIG_PATH="$(AWS_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=aws AWS_IMAGE_TAG=$(AWS_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AWS_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AWS_PROJECT_CODE_IMAGE_TAG)" PROJECT_CODE_IMAGE_PULL_POLICY=Always SUPERSET_IMAGE_REPOSITORY="$(AWS_SUPERSET_IMAGE_REPOSITORY)" SUPERSET_IMAGE_TAG="$(AWS_SUPERSET_IMAGE_TAG)" SUPERSET_IMAGE_PULL_POLICY=Always bash scripts/aws/stack/platform-up.sh

aws-artifacts-deploy:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) KUBE_CONTEXT=$(AWS_KUBE_CONTEXT) KUBECONFIG_PATH="$(AWS_KUBECONFIG_PATH)" DEPLOYMENT_SCOPE=aws AWS_IMAGE_TAG=$(AWS_IMAGE_TAG) PROJECT_CODE_IMAGE_REPOSITORY="$(AWS_PROJECT_CODE_IMAGE_REPOSITORY)" PROJECT_CODE_IMAGE_TAG="$(AWS_PROJECT_CODE_IMAGE_TAG)" bash scripts/aws/stack/deploy-artifacts.sh

aws-up:
	@set -e; export KUBECONFIG="$(AWS_KUBECONFIG_PATH)"; \
	image_tag="$(AWS_IMAGE_TAG)"; \
	$(MAKE) aws-foundation-up AWS_IMAGE_TAG="$$image_tag"; \
	$(MAKE) aws-platform-up AWS_IMAGE_TAG="$$image_tag"; \
	$(MAKE) aws-artifacts-deploy AWS_IMAGE_TAG="$$image_tag"

aws-forward:
	@echo "Starting AWS POC port-forwards (Ctrl-C to stop all)..."
	@echo "  Dagster UI:       http://localhost:3000"
	@echo "  Superset UI:      http://localhost:8088  (admin / admin)"
	@echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
	@echo "  Trino UI:         http://localhost:8080"
	@set -e; \
	context="$(AWS_KUBE_CONTEXT)"; \
	kubectl --context $$context port-forward svc/trino 8080:8080 -n $(NAMESPACE) & \
	trino_pid=$$!; \
	kubectl --context $$context port-forward svc/dagster-dagster-webserver 3000:80 -n $(NAMESPACE) & \
	dagster_pid=$$!; \
	kubectl --context $$context port-forward svc/superset 8088:8088 -n $(NAMESPACE) & \
	superset_pid=$$!; \
	kubectl --context $$context port-forward svc/openmetadata 8585:8585 -n $(NAMESPACE) & \
	om_pid=$$!; \
	trap 'kill $$trino_pid $$dagster_pid $$superset_pid $$om_pid 2>/dev/null || true' INT TERM EXIT; \
	wait

aws-e2e:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) KUBE_CONTEXT=$(AWS_KUBE_CONTEXT) KUBECONFIG="$(AWS_KUBECONFIG_PATH)" OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR=infra/terraform/environments/aws-poc bash scripts/artifacts/olf.sh e2e run --env aws

aws-down:
	@$(MAKE) aws-platform-down
	@$(MAKE) aws-foundation-down

aws-platform-down:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) KUBE_CONTEXT=$(AWS_KUBE_CONTEXT) KUBECONFIG_PATH="$(AWS_KUBECONFIG_PATH)" bash scripts/aws/stack/teardown.sh

aws-foundation-down:
	@NAMESPACE=$(NAMESPACE) AWS_REGION=$(AWS_REGION) AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) AWS_NODE_DESIRED_SIZE=$(AWS_NODE_DESIRED_SIZE) AWS_NODE_MIN_SIZE=$(AWS_NODE_MIN_SIZE) AWS_NODE_MAX_SIZE=$(AWS_NODE_MAX_SIZE) AWS_NODE_INSTANCE_TYPES=$(AWS_NODE_INSTANCE_TYPES) KUBE_CONTEXT=$(AWS_KUBE_CONTEXT) KUBECONFIG_PATH="$(AWS_KUBECONFIG_PATH)" bash scripts/aws/foundation/down.sh
