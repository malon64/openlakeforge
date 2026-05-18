.PHONY: help tree check-structure local-cluster local-up local-down local-status local-forward

NAMESPACE ?= lakehouse

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'
	@printf '%s\n' ''
	@printf '%s\n' 'Local stack:'
	@printf '%s\n' '  make local-cluster    Create the kind cluster (WSL + kind required)'
	@printf '%s\n' '  make local-up         Deploy Garage + Polaris + Trino to the current cluster'
	@printf '%s\n' '  make local-down       Uninstall all releases and delete the namespace'
	@printf '%s\n' '  make local-status     Show pod and service status in the lakehouse namespace'
	@printf '%s\n' '  make local-forward    Port-forward all services to localhost'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/check-structure.sh

local-cluster:
	@bash scripts/local/create-cluster.sh

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
	@kubectl port-forward svc/garage  9000:3900 -n $(NAMESPACE) &
	@kubectl port-forward svc/polaris 8181:8181 -n $(NAMESPACE) &
	@kubectl port-forward svc/trino   8080:8080 -n $(NAMESPACE) &
	@wait
