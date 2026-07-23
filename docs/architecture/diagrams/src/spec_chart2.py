#!/usr/bin/env python3
"""Chart 2 — Namespace Runtime Topology. What talks to what, with the real
service DNS names from the provider contracts on the wires."""
from pathlib import Path
from k8ssvg import Chart, C

c = Chart(1180, 990, "Namespace Runtime Topology",
          "namespace: lakehouse · service DNS from the provider contracts · dashed purple = per-run")


def svc_tag(x, y, w, dns):
    """svc icon + DNS string in a card's top-right corner."""
    c.icon(x + w - 30, y + 10, "svc", "", size=30)
    c.label(x + w - 50, y + 30, dns, size=10.5, mono=True, anchor="end")


# ---------- Row A ----------
c.box(52, 150, 400, 240, "Dagster", color="platform", fill="#FFFFFF")
svc_tag(52, 150, 400, "dagster-dagster-webserver:80")
c.icon(130, 210, "deploy", "webserver", size=46)
c.icon(240, 210, "deploy", "daemon", size=46, label2="K8sRunLauncher")
c.icon(130, 300, "deploy", "code server", size=46, label2="sales")
c.icon(240, 300, "deploy", "code server", size=46, label2="supply-chain")

c.box(472, 150, 280, 240, "Per run", color="ephemeral", fill="#FAF6FC", dashed=True)
c.icon(545, 225, "job", "run pod", size=50, variant="ephemeral", label2="dlt + dbt-trino")
c.icon(685, 225, "job", "floe runner", size=50, variant="ephemeral", label2="floe:0.6.8")
c.edge([(572, 250), (658, 250)], color="ephemeral", dashed=True, label="creates")

c.box(772, 150, 370, 240, "OpenMetadata", color="platform", fill="#FFFFFF")
svc_tag(772, 150, 370, "openmetadata:8585")
c.icon(880, 230, "deploy", "server", size=46)
c.icon(1010, 230, "sts", "opensearch", size=46, label2="single node")

# ---------- Row B ----------
c.box(52, 470, 250, 170, "Superset", color="platform", fill="#FFFFFF")
svc_tag(52, 470, 250, "superset:8088")
c.icon(105, 530, "deploy", "node", size=42)
c.icon(177, 530, "deploy", "worker", size=42)
c.icon(249, 530, "sts", "redis", size=42)

c.box(322, 470, 250, 170, "Trino", color="platform", fill="#FFFFFF")
svc_tag(322, 470, 250, "trino:8080")
c.icon(447, 525, "deploy", "coordinator", size=48, label2="workers: 0")

c.box(592, 470, 250, 170, "Polaris", color="platform", fill="#FFFFFF")
svc_tag(592, 470, 250, "polaris:8181")
c.icon(670, 525, "deploy", "catalog", size=48, label2="/api/catalog")
c.icon(780, 525, "secret", "creds ×5", size=48, label2="per-engine oauth")

c.box(862, 470, 280, 170, "PostgreSQL", color="platform", fill="#FFFFFF")
svc_tag(862, 470, 280, "postgresql:5432")
c.icon(1000, 525, "sts", "shared metadata db", size=48,
       label2="dagster · om · superset")

# ---------- Row C ----------
c.box(52, 710, 700, 230, "SeaweedFS — object storage", color="platform", fill="#FFFFFF")
svc_tag(52, 710, 700, "seaweedfs-s3:8333")
c.icon(150, 790, "sts", "master", size=46, label2=":9333")
c.icon(310, 790, "sts", "volume", size=46, label2="32 slots")
c.icon(470, 790, "sts", "filer", size=46, label2=":8888")
c.icon(630, 790, "deploy", "s3 gateway", size=46, label2="S3 API")

c.box(772, 710, 370, 230, "Buckets", color="storage", fill="#FFFFFF")
c.cylinder(875, 812, 150, 74, "lakehouse-bronze", color="bronze")
c.cylinder(1052, 812, 150, 74, "lakehouse-silver", color="silver")
c.cylinder(875, 896, 150, 74, "lakehouse-gold", color="gold")
c.cylinder(1052, 896, 150, 74, "openlakeforge-ops", color="storage")

# ---------- Wires ----------
c.edge([(302, 542), (322, 542)], color="control")
c.label(312, 528, "sql", size=10.5, mono=True, anchor="middle", color=C["control"])
c.edge([(572, 542), (592, 542)], color="control")
c.label(582, 528, "iceberg rest", size=10.5, mono=True, anchor="middle", color=C["control"])

c.edge([(447, 640), (447, 710)], color="control", label="s3a")
c.edge([(692, 640), (692, 710)], color="control", label="table io")

c.edge([(252, 390), (252, 444), (950, 444), (950, 470)], color="dim")
c.edge([(957, 390), (957, 470)], color="dim")
c.edge([(130, 470), (130, 452), (990, 452), (990, 470)], color="dim")

c.edge([(452, 230), (472, 230)], color="ephemeral", dashed=True)
c.label(462, 216, "launch", size=10.5, mono=True, anchor="middle", color=C["ephemeral"])
c.edge([(612, 390), (612, 470)], color="ephemeral", dashed=True)
c.label(622, 414, "floe: commit silver", size=10.5, mono=True, anchor="start", color=C["ephemeral"])

c.edge([(510, 390), (510, 470)], color="ephemeral", dashed=True)
c.label(500, 414, "dbt-trino: build gold", size=10.5, mono=True, anchor="end", color=C["ephemeral"])
c.edge([(582, 390), (582, 710)], color="ephemeral", dashed=True)
c.label(582, 690, "dlt: raw → bronze", size=10.5, mono=True, anchor="middle", color=C["ephemeral"])
c.label(582, 704, "floe: bronze → silver", size=10.5, mono=True, anchor="middle", color=C["ephemeral"])

c.edge([(800, 390), (800, 470)], color="dim", label="crawls")

c.edge([(752, 825), (772, 825)], color="storage")

n = c.write(str(Path(__file__).resolve().parent.parent / "chart2-namespace-topology.svg"))
print("chart2 svg:", n, "bytes")
