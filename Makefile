# Deprecated checkout compatibility targets.  The supported interface is olf.
.PHONY: help tree check-structure check-components check-contracts check-infra check-project-code check-dbt check-lockfiles release-check release-bundle floe-manifest floe-manifest-upload dbt-parse project-code-image project-code-load superset-image superset-load superset-reports-deploy superset-reports-export openmetadata-metadata-deploy local-foundation-up local-foundation-down local-platform-up local-platform-down local-artifacts-deploy local-up local-down local-status local-forward local-prefetch local-e2e local-slim-platform-up local-slim-artifacts-deploy local-slim-up local-slim-e2e local-slim-smoke local-slim-down azure-foundation-up azure-platform-up azure-platform-down azure-artifacts-deploy azure-up azure-status azure-forward azure-e2e azure-down azure-foundation-down aws-foundation-up aws-platform-up aws-platform-down aws-artifacts-deploy aws-up aws-status aws-forward aws-e2e aws-down aws-foundation-down

OLF_BIN ?= uv run --project tools/olf --locked olf
NAMESPACE ?= lakehouse
CLUSTER_NAME ?= openlakeforge-local
KUBE_CONTEXT ?= kind-$(CLUSTER_NAME)
LOCAL_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/local.yaml
LOCAL_PROFILE ?= full
LOCAL_TFVARS_FILE ?=
E2E_SUITE ?= full
SMOKE_TIMEOUT_SECONDS ?= 2700
AZURE_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/azure.yaml
AWS_KUBECONFIG_PATH ?= $(CURDIR)/.tmp/kubeconfigs/aws.yaml

help:
	@$(OLF_BIN) --help
tree:
	@$(OLF_BIN) diagnostics tree
check-structure:
	@$(OLF_BIN) check structure
check-components:
	@$(OLF_BIN) check components
check-contracts:
	@$(OLF_BIN) check contracts
check-infra:
	@$(OLF_BIN) check infra
check-project-code:
	@$(OLF_BIN) check project-code
check-dbt:
	@$(OLF_BIN) check dbt
check-lockfiles:
	@$(OLF_BIN) check lockfiles
release-check:
	@$(OLF_BIN) check all
release-bundle:
	@$(OLF_BIN) release build-bundle
floe-manifest:
	@$(OLF_BIN) floe generate-manifests --provider local
floe-manifest-upload:
	@KUBECONFIG=$(LOCAL_KUBECONFIG_PATH) KUBE_CONTEXT=$(KUBE_CONTEXT) $(OLF_BIN) artifacts upload-manifests --provider local --via port-forward
dbt-parse:
	@$(OLF_BIN) dbt parse
project-code-image:
	@$(OLF_BIN) images build project-code --cluster-name $(CLUSTER_NAME)
project-code-load:
	@$(OLF_BIN) images load project-code --cluster-name $(CLUSTER_NAME)
superset-image:
	@$(OLF_BIN) images build superset --cluster-name $(CLUSTER_NAME)
superset-load:
	@$(OLF_BIN) images load superset --cluster-name $(CLUSTER_NAME)
superset-reports-deploy:
	@KUBECONFIG=$(LOCAL_KUBECONFIG_PATH) KUBE_CONTEXT=$(KUBE_CONTEXT) $(OLF_BIN) superset deploy-reports --provider local
superset-reports-export:
	@KUBECONFIG=$(LOCAL_KUBECONFIG_PATH) KUBE_CONTEXT=$(KUBE_CONTEXT) $(OLF_BIN) superset export-reports --provider local
openmetadata-metadata-deploy:
	@KUBECONFIG=$(LOCAL_KUBECONFIG_PATH) KUBE_CONTEXT=$(KUBE_CONTEXT) $(OLF_BIN) openmetadata deploy-metadata --provider local

local-foundation-up:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase foundation
local-foundation-down:
	@$(OLF_BIN) destroy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase foundation
local-platform-up:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase platform
local-artifacts-deploy:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase artifacts
local-up:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile full
local-slim-platform-up:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile slim --phase platform
local-slim-artifacts-deploy:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile slim --phase artifacts
local-slim-up:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile slim
local-slim-e2e:
	@$(OLF_BIN) e2e run --env local --suite smoke
local-slim-smoke:
	@$(OLF_BIN) smoke run --timeout-seconds $(SMOKE_TIMEOUT_SECONDS)
local-slim-down:
	@$(OLF_BIN) destroy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile slim
local-down:
	@$(OLF_BIN) destroy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE)
local-platform-down:
	@$(OLF_BIN) destroy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase platform
local-status:
	@$(OLF_BIN) status --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH)
local-prefetch:
	@$(OLF_BIN) deploy --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE) --phase prefetch
local-forward:
	@$(OLF_BIN) forward --provider local --namespace $(NAMESPACE) --cluster-name $(CLUSTER_NAME) --kubeconfig-path $(LOCAL_KUBECONFIG_PATH) --profile $(LOCAL_PROFILE)
local-e2e:
	@$(OLF_BIN) e2e run --env local --suite $(E2E_SUITE)

azure-foundation-up:
	@$(OLF_BIN) deploy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH) --phase foundation
azure-platform-up:
	@$(OLF_BIN) deploy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH) --phase platform
azure-artifacts-deploy:
	@$(OLF_BIN) deploy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH) --phase artifacts
azure-up:
	@$(OLF_BIN) deploy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH)
azure-status:
	@$(OLF_BIN) status --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH)
azure-forward:
	@$(OLF_BIN) forward --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH)
azure-e2e:
	@$(OLF_BIN) e2e run --env azure
azure-down:
	@$(OLF_BIN) destroy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH)
azure-platform-down:
	@$(OLF_BIN) destroy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH) --phase platform
azure-foundation-down:
	@$(OLF_BIN) destroy --provider azure --namespace $(NAMESPACE) --kubeconfig-path $(AZURE_KUBECONFIG_PATH) --phase foundation

aws-foundation-up:
	@$(OLF_BIN) deploy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH) --phase foundation
aws-platform-up:
	@$(OLF_BIN) deploy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH) --phase platform
aws-artifacts-deploy:
	@$(OLF_BIN) deploy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH) --phase artifacts
aws-up:
	@$(OLF_BIN) deploy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH)
aws-status:
	@$(OLF_BIN) status --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH)
aws-forward:
	@$(OLF_BIN) forward --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH)
aws-e2e:
	@$(OLF_BIN) e2e run --env aws
aws-down:
	@$(OLF_BIN) destroy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH)
aws-platform-down:
	@$(OLF_BIN) destroy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH) --phase platform
aws-foundation-down:
	@$(OLF_BIN) destroy --provider aws --namespace $(NAMESPACE) --kubeconfig-path $(AWS_KUBECONFIG_PATH) --phase foundation
