.PHONY: help tree check-structure check-infra check-project-code check-dbt floe-manifest floe-manifest-upload dbt-parse project-code-image project-code-load superset-image superset-load superset-reports-deploy superset-reports-export local-cluster local-destroy-cluster local-up local-down local-status local-forward local-prefetch

NAMESPACE ?= lakehouse
PROJECT_CODE_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/project-code
PROJECT_CODE_IMAGE_TAG ?= local
PROJECT_CODE_IMAGE_PULL_POLICY ?= Never
SUPERSET_IMAGE_REPOSITORY ?= ghcr.io/openlakeforge/superset
SUPERSET_IMAGE_TAG ?= local
SUPERSET_IMAGE_PULL_POLICY ?= Never

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' '  make check-infra      Validate Terraform and render Helm values'
	@printf '%s\n' '  make check-project-code  Validate the project-code Dagster package'
	@printf '%s\n' '  make check-dbt        Validate the Sales dbt-duckdb project'
	@printf '%s\n' '  make floe-manifest   Generate the Sales Floe Dagster manifest'
	@printf '%s\n' '  make floe-manifest-upload  Upload the Sales Floe manifest to the local code bucket'
	@printf '%s\n' '  make dbt-parse       Generate the Sales dbt manifest'
	@printf '%s\n' '  make superset-reports-deploy  Deploy Sales Superset report assets'
	@printf '%s\n' '  make superset-reports-export  Export edited Sales Superset report assets'
	@printf '%s\n' ''
	@printf '%s\n' 'Local stack:'
	@printf '%s\n' '  make local-cluster    Create the kind cluster (WSL + kind required)'
	@printf '%s\n' '  make project-code-image  Build ghcr.io/openlakeforge/project-code:local'
	@printf '%s\n' '  make project-code-load   Load the project-code image into kind'
	@printf '%s\n' '  make superset-image   Build ghcr.io/openlakeforge/superset:local'
	@printf '%s\n' '  make superset-load    Load the Superset image into kind'
	@printf '%s\n' '  make local-destroy-cluster  Delete the local kind cluster'
	@printf '%s\n' '  make local-prefetch    Pre-pull heavy images (OpenSearch, OM ingestion, Superset helpers) into kind'
	@printf '%s\n' '  make local-up         Terraform-apply SeaweedFS + Polaris + Trino + OpenMetadata + Superset + Dagster'
	@printf '%s\n' '  make local-down       Terraform-destroy the local stack'
	@printf '%s\n' '  make local-status     Show pod and service status in the configured namespace'
	@printf '%s\n' '  make local-forward    Port-forward all services to localhost (Dagster :3000, Superset :8088, OpenMetadata :8585, Trino :8080, Polaris :8181, S3 :9000)'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/test/check-structure.sh

check-infra:
	@bash scripts/test/check-infra.sh

check-project-code:
	@bash scripts/test/check-project-code.sh

check-dbt:
	@bash scripts/test/check-dbt.sh

floe-manifest:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/floe-manifest.sh

floe-manifest-upload:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/upload-floe-manifest.sh

dbt-parse:
	@bash scripts/local/dbt-parse.sh

project-code-image:
	@PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/build-project-code-image.sh

project-code-load:
	@PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) bash scripts/local/load-project-code-image.sh

superset-image:
	@SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) bash scripts/local/build-superset-image.sh

superset-load:
	@SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) bash scripts/local/load-superset-image.sh

superset-reports-deploy:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/superset-reports-deploy.sh

superset-reports-export:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/superset-reports-export.sh

local-cluster:
	@bash scripts/local/create-cluster.sh

local-destroy-cluster:
	@bash scripts/local/destroy-cluster.sh

local-up:
	@NAMESPACE=$(NAMESPACE) PROJECT_CODE_IMAGE_REPOSITORY=$(PROJECT_CODE_IMAGE_REPOSITORY) PROJECT_CODE_IMAGE_TAG=$(PROJECT_CODE_IMAGE_TAG) PROJECT_CODE_IMAGE_PULL_POLICY=$(PROJECT_CODE_IMAGE_PULL_POLICY) SUPERSET_IMAGE_REPOSITORY=$(SUPERSET_IMAGE_REPOSITORY) SUPERSET_IMAGE_TAG=$(SUPERSET_IMAGE_TAG) SUPERSET_IMAGE_PULL_POLICY=$(SUPERSET_IMAGE_PULL_POLICY) bash scripts/local/setup.sh

local-down:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/teardown.sh

local-status:
	@echo "=== Pods ===" && kubectl get pods -n $(NAMESPACE)
	@echo "" && echo "=== Services ===" && kubectl get svc -n $(NAMESPACE)
	@echo "" && echo "=== PVCs ===" && kubectl get pvc -n $(NAMESPACE)

local-prefetch:
	@echo "Pre-pulling heavy images into kind to avoid Helm timeouts..."
	@bash scripts/local/prefetch-images.sh

local-forward:
	@echo "Starting port-forwards (Ctrl-C to stop all)..."
	@echo "  Dagster UI:       http://localhost:3000"
	@echo "  Superset UI:      http://localhost:8088  (admin / admin)"
	@echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
	@echo "  Trino UI:         http://localhost:8080"
	@echo "  Polaris API:      http://localhost:8181"
	@echo "  SeaweedFS S3:     http://localhost:9000"
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
	kubectl port-forward svc/superset 8088:8088 -n $(NAMESPACE) & \
	superset_pid=$$!; \
	kubectl port-forward svc/openmetadata 8585:8585 -n $(NAMESPACE) & \
	om_pid=$$!; \
	trap 'kill $$seaweedfs_pid $$polaris_pid $$trino_pid $$dagster_pid $$superset_pid $$om_pid 2>/dev/null || true' INT TERM EXIT; \
	wait
