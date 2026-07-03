{% materialization iceberg_table, adapter="duckdb", supported_languages=["sql"] %}

  {%- set target_relation = this.incorporate(type="table") -%}
  {%- set existing_relation = load_cached_relation(target_relation) -%}
  {%- set grant_config = config.get("grants") -%}
  {%- set is_glue = (env_var("OPENLAKEFORGE_CATALOG_TYPE", "") == "glue") -%}

  {{ run_hooks(pre_hooks, inside_transaction=False) }}
  {{ run_hooks(pre_hooks, inside_transaction=True) }}

  {% if is_glue %}
    {#-- DuckDB's iceberg extension omits `location` on createTable against the AWS Glue
         REST catalog, which Glue rejects ("Location information cannot be null"). Work
         around it: stage the model result in a local DuckDB table, (re)create the Glue
         table WITH a location via the olf_glue_ensure_iceberg_table UDF (registered by
         the libs.dbt.duckdb_plugins.glue_iceberg plugin), then load it with INSERT,
         which DuckDB performs correctly. See docs/technical-debt.md. --#}
    {%- set stage_name = target_relation.identifier ~ "__olf_stage" -%}

    {% call statement("stage_model", language="sql") -%}
      create or replace temporary table {{ stage_name }} as (
        {{ compiled_code }}
      )
    {%- endcall %}

    {% call statement("ensure_glue_table", language="sql") -%}
      select olf_glue_ensure_iceberg_table(
        '{{ target_relation.schema }}',
        '{{ target_relation.identifier }}',
        (
          select to_json(list({'name': column_name, 'type': column_type}))
          from (describe {{ stage_name }})
        )
      )
    {%- endcall %}

    {{ adapter.commit() }}

    {% call statement("main", language="sql") -%}
      insert into {{ target_relation }} by name
      select * from {{ stage_name }}
    {%- endcall %}

    {% call statement("drop_stage", language="sql") -%}
      drop table if exists {{ stage_name }}
    {%- endcall %}

  {% else %}
    {% call statement("drop_existing_relation", language="sql") -%}
      drop table if exists {{ target_relation }}
    {%- endcall %}

    {{ adapter.commit() }}

    {% call statement("main", language="sql") -%}
      {{ create_table_as(False, target_relation, compiled_code, "sql") }}
    {%- endcall %}
  {% endif %}

  {{ run_hooks(post_hooks, inside_transaction=True) }}

  {% set should_revoke = should_revoke(existing_relation, full_refresh_mode=True) %}
  {% do apply_grants(target_relation, grant_config, should_revoke=should_revoke) %}
  {% do persist_docs(target_relation, model) %}

  {{ adapter.commit() }}
  {{ run_hooks(post_hooks, inside_transaction=False) }}

  {{ return({"relations": [target_relation]}) }}

{% endmaterialization %}
