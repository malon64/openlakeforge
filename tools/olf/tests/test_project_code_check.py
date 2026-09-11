from __future__ import annotations

from types import SimpleNamespace

import pytest

from olf import project_code_check


def _source_asset(*, can_subset: bool, output_required: bool) -> SimpleNamespace:
    return SimpleNamespace(
        keys=(SimpleNamespace(path=("crm", "orders")),),
        can_subset=can_subset,
        op=SimpleNamespace(output_defs=(SimpleNamespace(name="orders", is_required=output_required),)),
    )


def test_bronze_subsetability_requires_one_optional_subsettable_multi_asset_per_source() -> None:
    inventory = SimpleNamespace(sources=(SimpleNamespace(name="crm"),))
    definitions = SimpleNamespace(assets=(_source_asset(can_subset=True, output_required=False),))

    project_code_check._verify_bronze_subsetability(inventory, definitions)


@pytest.mark.parametrize(
    ("can_subset", "output_required", "message"),
    [
        (False, False, "must support product-specific subsets"),
        (True, True, "must have optional outputs"),
    ],
)
def test_bronze_subsetability_rejects_non_subsettable_or_required_outputs(
    can_subset: bool, output_required: bool, message: str
) -> None:
    inventory = SimpleNamespace(sources=(SimpleNamespace(name="crm"),))
    definitions = SimpleNamespace(assets=(_source_asset(can_subset=can_subset, output_required=output_required),))

    with pytest.raises(RuntimeError, match=message):
        project_code_check._verify_bronze_subsetability(inventory, definitions)


def test_sequential_floe_orchestration_requires_serial_manifest_and_job_config() -> None:
    product = SimpleNamespace(id="order_revenue", job_name="order_revenue_job")
    manifest = SimpleNamespace(execution=SimpleNamespace(orchestration=SimpleNamespace(strategy="sequential")))
    job = SimpleNamespace(run_config={"execution": {"config": {"multiprocess": {"max_concurrent": 1}}}})

    project_code_check._verify_sequential_floe_orchestration(product, manifest, job)

    job.run_config["execution"]["config"]["multiprocess"]["max_concurrent"] = 2
    with pytest.raises(RuntimeError, match="did not inherit Floe orchestration concurrency"):
        project_code_check._verify_sequential_floe_orchestration(product, manifest, job)


def _schedule_library(
    *,
    stages: tuple[str, ...] = ("prod",),
    job_name: str = "order_revenue_pipeline",
    cron: str = "0 3 * * *",
    status: str = "STOPPED",
    count: int = 1,
) -> SimpleNamespace:
    schedule = SimpleNamespace(
        name="order_revenue_daily",
        job_name=job_name,
        cron_schedule=cron,
        default_status=SimpleNamespace(value=status),
    )
    return SimpleNamespace(
        ProductDefinitionSpec=lambda **fields: SimpleNamespace(**fields),
        build_stage_schedules=lambda spec, *, stage: [schedule] * count if stage in stages else [],
    )


def _inventory() -> SimpleNamespace:
    product = SimpleNamespace(id="order_revenue", domain_name="sales", job_name="order_revenue_pipeline")
    return SimpleNamespace(products=(product,))


def test_stage_schedules_accept_a_stopped_daily_scaffold_in_prod_only() -> None:
    project_code_check._verify_stage_schedules(_inventory(), _schedule_library())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"stages": ("dev", "prod")}, "must define no schedule"),
        ({"stages": ("", "prod")}, "must define no schedule"),
        ({"count": 2}, "exactly one daily schedule"),
        ({"job_name": "hand_written_job"}, "not the descriptor job"),
        ({"status": "RUNNING"}, "must not start a schedule"),
        ({"cron": "*/5 * * * *"}, "must run once a day"),
    ],
)
def test_stage_schedules_reject_anything_a_promotion_could_start(overrides: dict, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        project_code_check._verify_stage_schedules(_inventory(), _schedule_library(**overrides))
