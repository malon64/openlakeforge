from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import boto3
import dlt
from botocore.client import Config

SALES_POC_ENTITIES = ("sales", "customers", "products")

_RAW_DIR = Path(__file__).resolve().parents[2] / "examples" / "raw"
_BRONZE_PREFIX = "bronze/sales"


@dataclass(frozen=True)
class BronzeLoadResult:
    entity: str
    rows: int
    uri: str
    source_file: str


def load_all_entities_to_bronze(raw_dir: Path | None = None) -> dict[str, BronzeLoadResult]:
    return {
        entity: load_entity_to_bronze(entity, raw_dir=raw_dir)
        for entity in SALES_POC_ENTITIES
    }


def load_entity_to_bronze(entity: str, raw_dir: Path | None = None) -> BronzeLoadResult:
    if entity not in SALES_POC_ENTITIES:
        raise ValueError(f"unsupported Sales POC entity: {entity}")

    source_file = (raw_dir or _RAW_DIR) / f"{entity}.csv"
    rows = list(_dlt_csv_resource(entity, source_file))
    if not rows:
        raise ValueError(f"{source_file} did not produce any rows")

    bucket = _required_env("OPENLAKEFORGE_S3_BUCKET", default="iceberg-data")
    key = f"{_BRONZE_PREFIX}/{entity}/{entity}.csv"
    _put_csv(bucket=bucket, key=key, rows=rows)

    return BronzeLoadResult(
        entity=entity,
        rows=len(rows),
        uri=f"s3://{bucket}/{key}",
        source_file=str(source_file),
    )


def _dlt_csv_resource(entity: str, source_file: Path) -> Iterable[dict[str, str]]:
    @dlt.resource(name=entity, write_disposition="replace")
    def _rows():
        with source_file.open(newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)

    yield from _rows()


def _put_csv(*, bucket: str, key: str, rows: list[dict[str, str]]) -> None:
    fieldnames = list(rows[0].keys())
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

    _s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=buffer.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )


def _s3_client():
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL_S3")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )


def _required_env(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value
