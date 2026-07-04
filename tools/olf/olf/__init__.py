"""OpenLakeForge deployment tooling.

Cross-environment logic shared by the local, Azure, and AWS deploy scripts:
provider-contract parsing, service REST APIs (OpenMetadata, Superset, Polaris),
object-storage uploads, and Kubernetes image bookkeeping. Shell scripts remain
the orchestrators for terraform/kubectl/helm/docker CLI invocations and call
into this package through the `olf` CLI.
"""

__version__ = "0.1.0"
