.PHONY: help tree check-structure check-infra check-project-code project-code-image project-code-load local-cluster local-destroy-cluster local-up local-down local-status local-forward local-dagster-smoke

NAMESPACE ?= lakehouse
PROJECT_CODE_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/project-code
PROJECT_CODE_IMAGE_TAG ?= local
PROJECT_CODE_IMAGE_PULL_POLICY ?= IfNotPresent

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' '  make check-infra      Validate Terraform and render Helm values'
	@printf '%s\n' '  make check-project-code  Validate the project-code Dagster package'
	@printf '%s\n' ''
	@printf '%s\n' 'Local stack:'
	@printf '%s\n' '  make local-cluster    Create the kind cluster (WSL + kind required)'
	@printf '%s\n' '  make project-code-image  Build ghcr.io/openlakeforge/project-code:local'
	@printf '%s\n' '  make project-code-load   Load the project-code image into kind'
	@printf '%s\n' '  make local-destroy-cluster  Delete the local kind cluster'
	@printf '%s\n' '  make local-up         Terraform-apply SeaweedFS + Polaris + Trino + Dagster'
	@printf '%s\n' '  make local-down       Terraform-destroy the local stack'
	@printf '%s\n' '  make local-status     Show pod and service status in the lakehouse namespace'
	@printf '%s\n' '  make local-forward    Port-forward all services to localhost'
	@printf '%s\n' '  make local-dagster-smoke  Launch the Iteration 2 Dagster smoke job'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/check-structure.sh

check-infra:
	@bash scripts/check-infra.sh

check-project-code:
	@bash scripts/check-project-code.sh

project-code-image:
	@PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/build-project-code-image.sh

project-code-load:
	@PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/load-project-code-image.sh

local-cluster:
	@bash scripts/local/create-cluster.sh

local-destroy-cluster:
	@bash scripts/local/destroy-cluster.sh

local-up:
	@NAMESPACE=$(NAMESPACE) PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) PROJECT_CODE_IMAGE_PULL_POLICY=$(PROJECT_CODE_IMAGE_PULL_POLICY) bash scripts/local/setup.sh

local-down:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/teardown.sh

local-status:
	@echo "=== Pods ===" && kubectl get pods -n $(NAMESPACE)
	@echo "" && echo "=== Services ===" && kubectl get svc -n $(NAMESPACE)
	@echo "" && echo "=== PVCs ===" && kubectl get pvc -n $(NAMESPACE)

local-forward:
	@echo "Starting port-forwards (Ctrl-C to stop all)..."
	@set -e; \
	dagster_pod="$$(kubectl get pods -n $(NAMESPACE) -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep 'dagster-webserver' | head -n 1)"; \
	kubectl port-forward svc/seaweedfs-s3 9000:8333 -n $(NAMESPACE) & \
	seaweedfs_pid=$$!; \
	kubectl port-forward svc/polaris 8181:8181 -n $(NAMESPACE) & \
	polaris_pid=$$!; \
	kubectl port-forward svc/trino 8080:8080 -n $(NAMESPACE) & \
	trino_pid=$$!; \
	kubectl port-forward pod/$$dagster_pod 3000:80 -n $(NAMESPACE) & \
	dagster_pid=$$!; \
	trap 'kill $$seaweedfs_pid $$polaris_pid $$trino_pid $$dagster_pid 2>/dev/null || true' INT TERM EXIT; \
	wait

local-dagster-smoke:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/dagster-smoke.sh
