from dagster import Definitions, MetadataValue, job, op


@op
def iteration2_smoke_op(context) -> str:
    context.log.info("OpenLakeForge Iteration 2 Dagster smoke run executed.")
    context.add_output_metadata(
        {
            "iteration": MetadataValue.int(2),
            "domain": MetadataValue.text("sales"),
            "purpose": MetadataValue.text("project-code Kubernetes run launcher smoke test"),
        }
    )
    return "ok"


@job
def iteration2_smoke_job() -> None:
    iteration2_smoke_op()


defs = Definitions(jobs=[iteration2_smoke_job])
