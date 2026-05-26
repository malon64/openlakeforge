from domains.sales.orchestration.dagster.definitions import iteration2_smoke_job


def test_iteration2_smoke_job_executes_in_process() -> None:
    result = iteration2_smoke_job.execute_in_process()

    assert result.success
