# Shared Libraries

`libs/` is reserved for reusable OpenLakeForge platform glue. It must not contain domain business logic.

Appropriate examples include:

- config loading
- storage path conventions
- Dagster resource helpers
- OpenLineage naming conventions
- logging and observability helpers

Runtime library APIs will be introduced when Iteration 2 starts the project-code image and Dagster integration.
