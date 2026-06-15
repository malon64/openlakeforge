{% materialization iceberg_table, adapter="duckdb", supported_languages=["sql"] %}

  {%- set target_relation = this.incorporate(type="table") -%}
  {%- set existing_relation = load_cached_relation(target_relation) -%}
  {%- set grant_config = config.get("grants") -%}

  {{ run_hooks(pre_hooks, inside_transaction=False) }}
  {{ run_hooks(pre_hooks, inside_transaction=True) }}

  {% call statement("drop_existing_relation", language="sql") -%}
    drop table if exists {{ target_relation }}
  {%- endcall %}

  {{ adapter.commit() }}

  {% call statement("main", language="sql") -%}
    {{ create_table_as(False, target_relation, compiled_code, "sql") }}
  {%- endcall %}

  {{ run_hooks(post_hooks, inside_transaction=True) }}

  {% set should_revoke = should_revoke(existing_relation, full_refresh_mode=True) %}
  {% do apply_grants(target_relation, grant_config, should_revoke=should_revoke) %}
  {% do persist_docs(target_relation, model) %}

  {{ adapter.commit() }}
  {{ run_hooks(post_hooks, inside_transaction=False) }}

  {{ return({"relations": [target_relation]}) }}

{% endmaterialization %}
