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
