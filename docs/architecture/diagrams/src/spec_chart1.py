#!/usr/bin/env python3
"""Chart 1 — Cluster Pod Census. Verified via `helm template` with the
project's own values files: 16 pods at rest (10 Deployments, 6 StatefulSets)."""
from pathlib import Path
from k8ssvg import Chart

c = Chart(1180, 892, "Cluster Pod Census",
          "namespace: lakehouse · kind cluster openlakeforge-local · long-lived services + on-demand Jobs")

# namespace boundary
c.box(28, 92, 1124, 768, "namespace: lakehouse", color="control", title_size=14)

ROW1, ROW2, ROW3 = 150, 436, 660
IY1, IY2 = 46, 152  # icon y-offsets inside a row-1 card

# --- Row 1 ---
c.box(52, ROW1, 350, 258, "Dagster — 4 pods", color="platform", fill="#FFFFFF")
c.icon(140, ROW1 + IY1, "deploy", "webserver", label2="dagster chart 1.13.6")
c.icon(316, ROW1 + IY1, "deploy", "daemon", label2="K8sRunLauncher")
c.icon(140, ROW1 + IY2, "deploy", "code server", label2="sales-dagster")
c.icon(316, ROW1 + IY2, "deploy", "code server", label2="supply-chain-dagster")

c.box(422, ROW1, 350, 258, "SeaweedFS — 4 pods", color="platform", fill="#FFFFFF")
c.icon(510, ROW1 + IY1, "sts", "master", label2=":9333")
c.icon(686, ROW1 + IY1, "sts", "volume", label2="maxVolumes 32")
c.icon(510, ROW1 + IY2, "sts", "filer", label2=":8888")
c.icon(686, ROW1 + IY2, "deploy", "s3 gateway", label2=":8333")

c.box(792, ROW1, 350, 258, "Superset — 3 pods", color="platform", fill="#FFFFFF")
c.icon(880, ROW1 + IY1, "deploy", "node", label2=":8088")
c.icon(1056, ROW1 + IY1, "deploy", "worker", label2="celery, solo pool")
c.icon(880, ROW1 + IY2, "sts", "redis", label2="cache + queue")

# --- Row 2 ---
c.box(52, ROW2, 350, 180, "OpenMetadata — 2 pods", color="platform", fill="#FFFFFF")
c.icon(140, ROW2 + IY1, "deploy", "server", label2=":8585")
c.icon(316, ROW2 + IY1, "sts", "opensearch", label2="single node")

c.box(422, ROW2, 168, 180, "PostgreSQL — 1", color="platform", fill="#FFFFFF")
c.icon(506, ROW2 + IY1, "sts", "shared db", label2="dagster · om · superset")

c.box(606, ROW2, 168, 180, "Polaris — 1", color="platform", fill="#FFFFFF")
c.icon(690, ROW2 + IY1, "deploy", "catalog", label2=":8181 · in-memory")

c.box(790, ROW2, 168, 180, "Trino — 1", color="platform", fill="#FFFFFF")
c.icon(874, ROW2 + IY1, "deploy", "coordinator", label2="workers: 0 · 2G heap")

c.badge(978, ROW2, 164, 180,
        ["16 pods", "at steady state", "", "10 Deployments", "6 StatefulSets"],
        color="control")

# --- Row 3: ephemeral ---
c.box(52, ROW3, 1090, 160,
      "Ephemeral & bootstrap Jobs — run/ingestion Jobs are TTL-collected; bootstrap Jobs + the log-archive CronJob leave completed pods until re-apply",
      color="ephemeral", fill="#FAF6FC", dashed=True)
EY = ROW3 + 44
c.icon(150, EY, "job", "dagster run pod", variant="ephemeral", label2="TTL 1h")
c.icon(340, EY, "job", "floe runner", variant="ephemeral", label2="malon64/floe:0.6.11 · TTL 1h")
c.icon(530, EY, "job", "polaris-bootstrap", variant="ephemeral", label2="per apply · no TTL")
c.icon(720, EY, "job", "superset init", variant="ephemeral", label2="chart hook · no TTL")
c.icon(910, EY, "job", "OM ingestion", variant="ephemeral", label2="ttl 3600s")
c.icon(1080, EY, "cronjob", "k8s-log-archive", variant="ephemeral", label2="hourly")

n = c.write(str(Path(__file__).resolve().parent.parent / "chart1-cluster-pod-census.svg"))
print("chart1 svg:", n, "bytes")
