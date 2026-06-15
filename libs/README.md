# Shared Libraries

`libs/` is reserved for reusable OpenLakeForge platform glue. It must not contain domain business logic.

Appropriate examples include:

- config loading
- shared dbt packages and macros
- shared Floe runtime profiles
- storage path conventions
- Dagster resource helpers
- OpenLineage naming conventions
- logging and observability helpers

Domain business logic stays under `domains/`; `libs/` contains reusable platform
glue shared by those domains.
