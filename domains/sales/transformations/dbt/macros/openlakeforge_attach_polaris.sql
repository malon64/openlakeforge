{% macro openlakeforge_attach_polaris() %}
  {% if execute and env_var('OPENLAKEFORGE_DBT_ATTACH_POLARIS', 'false') == 'true' %}
    {% set s3_endpoint = env_var('AWS_ENDPOINT_URL_S3', 'http://seaweedfs-s3:8333') %}
    {% set s3_endpoint_host = s3_endpoint | replace('http://', '') | replace('https://', '') %}

    {% do run_query("INSTALL httpfs") %}
    {% do run_query("INSTALL iceberg") %}
    {% do run_query("LOAD httpfs") %}
    {% do run_query("LOAD iceberg") %}

    {% do run_query(
      "CREATE OR REPLACE SECRET openlakeforge_s3_secret ("
      ~ "TYPE s3, "
      ~ "KEY_ID '" ~ env_var('AWS_ACCESS_KEY_ID', 'openlakeforge') ~ "', "
      ~ "SECRET '" ~ env_var('AWS_SECRET_ACCESS_KEY', 'openlakeforge') ~ "', "
      ~ "REGION '" ~ env_var('AWS_REGION', 'us-east-1') ~ "', "
      ~ "ENDPOINT '" ~ s3_endpoint_host ~ "', "
      ~ "URL_STYLE 'path', "
      ~ "USE_SSL false"
      ~ ")"
    ) %}

    {% do run_query(
      "CREATE OR REPLACE SECRET openlakeforge_polaris_secret ("
      ~ "TYPE iceberg, "
      ~ "CLIENT_ID '" ~ env_var('POLARIS_DBT_CLIENT_ID', 'openlakeforge-dbt') ~ "', "
      ~ "CLIENT_SECRET '" ~ env_var('POLARIS_DBT_CLIENT_SECRET', 'openlakeforge-dbt') ~ "', "
      ~ "OAUTH2_SERVER_URI '" ~ env_var('POLARIS_TOKEN_URI', 'http://polaris:8181/api/catalog/v1/oauth/tokens') ~ "', "
      ~ "OAUTH2_SCOPE '" ~ env_var('POLARIS_OAUTH_SCOPE', 'PRINCIPAL_ROLE:ALL') ~ "'"
      ~ ")"
    ) %}

    {% do run_query(
      "ATTACH IF NOT EXISTS '" ~ env_var('POLARIS_WAREHOUSE', 'lakehouse') ~ "' AS polaris ("
      ~ "TYPE iceberg, "
      ~ "SECRET openlakeforge_polaris_secret, "
      ~ "ENDPOINT '" ~ env_var('POLARIS_REST_URI', 'http://polaris:8181/api/catalog') ~ "', "
      ~ "ACCESS_DELEGATION_MODE 'none'"
      ~ ")"
    ) %}
  {% endif %}
{% endmacro %}
