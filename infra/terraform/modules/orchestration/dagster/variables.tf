variable "namespace" {
  description = "Kubernetes namespace where Dagster is deployed."
  type        = string
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "dagster"
}

variable "chart_repository" {
  description = "Dagster Helm chart repository."
  type        = string
  default     = "https://dagster-io.github.io/helm"
}

variable "chart_version" {
  description = "Dagster Helm chart version. Keep this aligned with the project-code Dagster Python package."
  type        = string
  default     = "1.13.6"
}

variable "base_values_file" {
  description = "Path to the non-secret base Helm values file."
  type        = string
}

variable "project_code_image_repository" {
  description = "Project-code image repository used by the Dagster code server and run pods."
  type        = string
  default     = "ghcr.io/openlakeforge/project-code"
}

variable "project_code_image_tag" {
  description = "Project-code image tag used by the Dagster code server and run pods."
  type        = string
  default     = "local"
}

variable "project_code_image_pull_policy" {
  description = "Project-code image pull policy used by the Dagster code server and run pods."
  type        = string
  default     = "IfNotPresent"
}

variable "code_location_name" {
  description = "Dagster user-code deployment and code location name."
  type        = string
  default     = "sales-dagster"
}

variable "definitions_module" {
  description = "Python module exposing Dagster Definitions."
  type        = string
  default     = "domains.sales.orchestration.dagster.definitions"
}
