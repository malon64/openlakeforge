"""Provider-neutral catalog namespace reconciliation for Phase 2.

Namespace lifecycle used to live in Phase 1 for both Polaris and AWS Glue: the
Polaris module templated a `create_namespace` shell loop into its bootstrap
Kubernetes Job, and the Glue module managed one `aws_glue_catalog_database` per
namespace as a Terraform resource. Both made `platform-up` depend on
domain-code content, which ADR 0008's two-phase boundary forbids, and meant a
product added to a descriptor was not queryable until somebody re-applied the
platform.

This module holds the reconciliation logic shared by every backend (see
`olf.polaris` and `olf.glue`): what the descriptors say the catalog should
look like, and how that compares to what a backend reports it currently holds.
A backend only has to implement `create_namespace`, `update_namespace_location`,
and `drop_namespace` against a `CatalogNamespace` -- `apply_namespace_sync`
calls those three methods and does not otherwise care which catalog it is
talking to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from olf.inventory import CatalogNamespace, inventory_for


class NamespaceClient(Protocol):
    """The reconciliation surface every catalog backend implements."""

    def create_namespace(self, namespace: CatalogNamespace) -> None: ...

    def update_namespace_location(self, namespace: CatalogNamespace) -> None: ...

    def drop_namespace(self, name: str) -> None: ...


@dataclass(frozen=True)
class NamespaceSyncPlan:
    """The reconciliation the catalog needs to match the descriptors.

    `unchanged` is carried rather than discarded so `--dry-run` output can show
    the full desired state, not only the delta.
    """

    create: tuple[CatalogNamespace, ...] = ()
    update: tuple[CatalogNamespace, ...] = ()
    delete: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    #: Namespaces the catalog holds that no descriptor declares. Always
    #: populated so a run without --prune can still report what it is leaving
    #: behind; only mirrored into `delete` when pruning was asked for.
    orphans: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.create or self.update or self.delete)


def plan_namespace_sync(
    existing: Mapping[str, str],
    desired: Sequence[CatalogNamespace],
    *,
    prune: bool = False,
) -> NamespaceSyncPlan:
    """Diff the live catalog against the descriptors.

    `existing` maps a namespace name to its current storage location ("" when
    the catalog holds no location for it). Namespaces the catalog knows about
    but no descriptor declares are only scheduled for deletion when `prune` is
    set -- dropping a namespace can destroy the Iceberg tables inside it, so it
    stays an explicit opt-in rather than a side effect of removing a product.
    """
    create: list[CatalogNamespace] = []
    update: list[CatalogNamespace] = []
    unchanged: list[str] = []
    for namespace in desired:
        if namespace.name not in existing:
            create.append(namespace)
        elif existing[namespace.name] != namespace.location:
            update.append(namespace)
        else:
            unchanged.append(namespace.name)

    declared = {namespace.name for namespace in desired}
    orphans = tuple(sorted(name for name in existing if name not in declared))

    return NamespaceSyncPlan(
        create=tuple(create),
        update=tuple(update),
        delete=orphans if prune else (),
        unchanged=tuple(unchanged),
        orphans=orphans,
    )


def desired_namespaces(repo_root: Path, *, silver_bucket: str, gold_bucket: str) -> tuple[CatalogNamespace, ...]:
    """Resolve the namespaces the descriptors ask for, with their locations."""
    physical = inventory_for(repo_root).resolve_physical_names(
        catalog_database_fqn="",
        silver_bucket=silver_bucket,
        gold_bucket=gold_bucket,
        manifest_base_uri="",
    )
    return physical.catalog_namespaces


def apply_namespace_sync(client: NamespaceClient, plan: NamespaceSyncPlan) -> None:
    """Execute a plan against a backend client: creates, then relocations, then deletes."""
    for namespace in plan.create:
        client.create_namespace(namespace)
    for namespace in plan.update:
        client.update_namespace_location(namespace)
    for name in plan.delete:
        client.drop_namespace(name)


def render_plan(plan: NamespaceSyncPlan, *, prune: bool) -> str:
    """Render a plan for humans, in the order apply_namespace_sync runs it."""
    lines: list[str] = []
    for namespace in plan.create:
        lines.append(f"  + create {namespace.name} -> {namespace.location}")
    for namespace in plan.update:
        lines.append(f"  ~ relocate {namespace.name} -> {namespace.location}")
    for name in plan.delete:
        lines.append(f"  - drop {name}")
    for name in plan.unchanged:
        lines.append(f"    unchanged {name}")
    if not prune:
        for name in plan.orphans:
            lines.append(f"    undeclared {name} (kept; rerun with --prune to drop)")
    return "\n".join(lines)
