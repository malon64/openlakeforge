FROM apachesuperset.docker.scarf.sh/apache/superset:5.0.0

USER root

RUN pip install --no-cache-dir "trino>=0.336,<1"

USER superset
