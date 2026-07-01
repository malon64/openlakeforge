# Shared Libraries

`libs/` is reserved for reusable OpenLakeForge platform glue. It must not contain domain business logic.

Appropriate examples include:

- config loading
- shared dbt packages, macros, and environment profile templates
- shared Floe runtime profiles
- storage path conventions
- Dagster resource helpers
- OpenLineage naming conventions
- logging and observability helpers

Domain business logic stays under `domains/`; `libs/` contains reusable platform
glue shared by those domains.

dbt environment profiles live under `libs/dbt/profiles/` and are rendered into
product projects by `libs.dbt.render_profiles`. Product dbt projects keep
product-local model code and schema defaults; provider-specific attach settings
belong in the shared templates.
