#!/usr/bin/env python3
"""Chart 5 — Provider Contracts. The modularity chart: engines on the left
consume Terraform-owned contract interfaces; adapters on the right implement
them. Swapping the platform = choosing the Terraform root. Engines never change."""
from pathlib import Path
from k8ssvg import Chart, C

c = Chart(1180, 840, "Provider Contracts",
          "engines consume interfaces, never implementations · swap the adapters, keep the engines")

# ---------- left: engines ----------
c.box(52, 130, 260, 560, "Engines — identical everywhere", color="platform",
      fill="#FFFFFF", title_size=13)
c.icon(130, 200, "deploy", "Dagster", size=46)
c.icon(244, 200, "deploy", "Trino", size=46)
c.icon(130, 330, "job", "Floe", size=46, variant="ephemeral")
c.icon(244, 330, "deploy", "Superset", size=46)
c.icon(130, 460, "deploy", "dbt", size=46, label2="runs via Trino")
c.icon(244, 460, "deploy", "OpenMetadata", size=46)
c.label(182, 610, "same images · same Helm charts", size=11, anchor="middle")
c.label(182, 626, "same SQL · same manifests", size=11, anchor="middle")

# ---------- center: the contract spine ----------
c.box(370, 130, 270, 560, "Provider contracts", color="control", fill="#FFFFFF",
      title_size=13)
ROWS = [
    ("storage", "endpoint · buckets · region"),
    ("catalog", "catalog_type · uri · warehouse"),
    ("metadata database", "host · db names · secret refs"),
    ("artifacts + images", "registry · manifest base URI"),
    ("identity + secrets", "principals · secret names"),
]
for i, (name, fields) in enumerate(ROWS):
    y = 190 + i * 100
    c.badge(390, y, 230, 56, [name, fields], color="control", size=12)
    c.edge([(620, y + 28), (660, y + 28)], color="control")

c.edge([(312, 410), (370, 410)], color="platform", width=3)
c.label(341, 396, "fields only", size=10.5, mono=True, anchor="middle", color=C["platform"])

# ---------- right: two adapter columns ----------
c.box(660, 130, 240, 560, "Local — kind", color="storage", fill="#FFFFFF", title_size=13)
c.box(920, 130, 230, 560, "AWS POC — EKS", color="managed", fill="#FFFFFF", title_size=13)
LOCAL = ["SeaweedFS S3 :8333", "Polaris REST 1.4.1", "PostgreSQL in-cluster",
         "kind image load · ops bucket", "K8s Secrets (dev-only)"]
AWS = ["Amazon S3", "AWS Glue", "RDS PostgreSQL", "ECR · S3", "EKS Pod Identity"]
for i, (l, a) in enumerate(zip(LOCAL, AWS)):
    y = 198 + i * 100
    c.chip(676, y, 208, 40, l, color="storage")
    c.chip(936, y, 198, 40, a, color="managed")

c.label(905, 712, "⇄  swapping columns = choosing the Terraform root", size=11.5,
        mono=True, anchor="middle", color=C["managed"])

# ---------- bottom band: the mechanism ----------
c.doc(112, 740, 230, 56, "environments/local", sub="contracts.tf → local adapters")
c.doc(392, 740, 230, 56, "environments/aws-poc", sub="contracts.tf → AWS adapters")
c.doc(672, 740, 230, 56, "environments/azure-poc", sub="AKS parity with local")
c.label(1030, 772, "same modules,", size=11.5, anchor="middle")
c.label(1030, 788, "different contracts.tf", size=11.5, anchor="middle", mono=True)

n = c.write(str(Path(__file__).resolve().parent.parent / "chart5-provider-contracts.svg"))
print("chart5 svg:", n, "bytes")
