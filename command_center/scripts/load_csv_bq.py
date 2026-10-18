#!/usr/bin/env python3
"""Load a CSV into a suffai_ops table using the column types declared in create_bq_tables.sql.

BigQuery matches CSV columns by position, so the load schema is built in the CSV's header order.
A CSV column the DDL doesn't declare fails the load; DDL columns the CSV lacks are added afterwards
as NULL (e.g. commodities.confidence_score) instead of being silently absent.

  python load_csv_bq.py --project P --dataset suffai_ops --table commodities \
      --header path/to/commodities.csv --uri gs://bucket/tables/commodities.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

DDL_PATH = Path(__file__).resolve().parent / "create_bq_tables.sql"


def ddl_columns(table: str, ddl_text: str) -> list[tuple[str, str, bool]]:
    """(name, type, required) for `table`, in DDL order."""
    m = re.search(rf"CREATE TABLE IF NOT EXISTS\s+\w+\.{re.escape(table)}\s*\((.*?)\)\s*;", ddl_text, re.S)
    if not m:
        raise SystemExit(f"{table}: no CREATE TABLE in {DDL_PATH.name}")
    cols = []
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        name, typ = line.split()[:2]
        cols.append((name, typ.upper(), "NOT NULL" in line.upper()))
    return cols


def csv_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [h.strip() for h in next(csv.reader(fh))]


def plan_load(table: str, header: list[str], ddl_text: str) -> tuple[list[tuple[str, str, bool]], list[tuple[str, str, bool]]]:
    """Return (load schema in header order, DDL columns missing from the CSV)."""
    cols = ddl_columns(table, ddl_text)
    by_name = {c[0]: c for c in cols}
    unknown = [h for h in header if h not in by_name]
    if unknown:
        raise SystemExit(f"{table}: CSV columns not declared in {DDL_PATH.name}: {', '.join(unknown)}")
    return [by_name[h] for h in header], [c for c in cols if c[0] not in header]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", default="suffai_ops")
    ap.add_argument("--table", required=True)
    ap.add_argument("--header", required=True, type=Path, help="Local copy of the CSV (header row is read)")
    ap.add_argument("--uri", help="gs:// URI to load from (defaults to loading the local file)")
    ap.add_argument("--location", default=None)
    args = ap.parse_args(argv)

    from google.cloud import bigquery  # type: ignore

    schema, missing = plan_load(args.table, csv_header(args.header), DDL_PATH.read_text(encoding="utf-8"))
    client = bigquery.Client(project=args.project, location=args.location)
    table_id = f"{args.project}.{args.dataset}.{args.table}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=False,
        allow_quoted_newlines=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[bigquery.SchemaField(n, t, mode="REQUIRED" if req else "NULLABLE") for n, t, req in schema],
    )
    if args.uri:
        job = client.load_table_from_uri(args.uri, table_id, job_config=job_config)
    else:
        with args.header.open("rb") as fh:
            job = client.load_table_from_file(fh, table_id, job_config=job_config)
    job.result()
    for name, typ, _req in missing:
        client.query(f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS `{name}` {typ}").result()
    rows = client.get_table(table_id).num_rows
    added = f"; added NULL column(s): {', '.join(c[0] for c in missing)}" if missing else ""
    print(f"Loaded {table_id} rows={rows}{added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
