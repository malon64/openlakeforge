# Fastest local evaluation path: retain the full ingestion-to-Gold data path
# while omitting optional governance and dashboard layers.
enable_governance = false
enable_analytics  = false
# Product pipelines are launched directly by e2e; this stack has no schedules
# or sensors that require the background Dagster scheduler/run-queue daemon.
enable_dagster_daemon = false
