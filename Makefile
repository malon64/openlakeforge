.PHONY: help tree check-structure check-infra local-cluster local-destroy-cluster local-up local-down local-status local-forward

NAMESPACE ?= lakehouse

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' '  make check-infra      Validate Terraform and render Helm values'
	@printf '%s\n' ''
	@printf '%s\n' 'Local stack:'
	@printf '%s\n' '  make local-cluster    Create the kind cluster (WSL + kind required)'
	@printf '%s\n' '  make local-destroy-cluster  Delete the local kind cluster'
	@printf '%s\n' '  make local-up         Terraform-apply SeaweedFS + Polaris + Trino'
	@printf '%s\n' '  make local-down       Terraform-destroy the local stack'
	@printf '%s\n' '  make local-status     Show pod and service status in the lakehouse namespace'
	@printf '%s\n' '  make local-forward    Port-forward all services to localhost'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/check-structure.sh

check-infra:
	@bash scripts/check-infra.sh

local-cluster:
	@bash scripts/local/create-cluster.sh

local-destroy-cluster:
	@bash scripts/local/destroy-cluster.sh

local-up:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/setup.sh

local-down:
	@NAMESPACE=$(NAMESPACE) bash scripts/local/teardown.sh

local-status:
	@echo "=== Pods ===" && kubectl get pods -n $(NAMESPACE)
	@echo "" && echo "=== Services ===" && kubectl get svc -n $(NAMESPACE)
	@echo "" && echo "=== PVCs ===" && kubectl get pvc -n $(NAMESPACE)

local-forward:
	@echo "Starting port-forwards (Ctrl-C to stop all)..."
	@set -e; \
	kubectl port-forward svc/seaweedfs-s3 9000:8333 -n $(NAMESPACE) & \
	seaweedfs_pid=$$!; \
	kubectl port-forward svc/polaris 8181:8181 -n $(NAMESPACE) & \
	polaris_pid=$$!; \
	kubectl port-forward svc/trino 8080:8080 -n $(NAMESPACE) & \
	trino_pid=$$!; \
	trap 'kill $$seaweedfs_pid $$polaris_pid $$trino_pid 2>/dev/null || true' INT TERM EXIT; \
	wait
