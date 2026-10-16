#!/usr/bin/env python3
"""Build dashboard/metrics.json from local CSVs (default) or BigQuery.

Usage (local sample):
  python3 aggregate_metrics.py

Usage (your CSVs):
  python3 aggregate_metrics.py --customers path.csv --orders path.csv --commodities path.csv

Usage (BigQuery after load_to_bq.sh):
  python3 aggregate_metrics.py --bq --project YOUR_PROJECT
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # AI_CUP_updated
DEFAULT_SAMPLE = REPO / "command_center_data" / "sample"
PIN_POP_PATH = REPO / "command_center_data" / "pin_population.json"
OUT_PATH = REPO / "web" / "metrics.json"


def age_group(age) -> str:
    try:
        a = int(float(age))
    except (TypeError, ValueError):
        return "unknown"
    if a < 18:
        return "<18"
    if a <= 24:
        return "18-24"
    if a <= 34:
        return "25-34"
    if a <= 44:
        return "35-44"
    if a <= 54:
        return "45-54"
    return "55+"


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_from_bq(project: str, dataset: str = "suffai_ops"):
    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client(project=project)

    def q(table: str) -> list[dict]:
        rows = client.query(f"SELECT * FROM `{project}.{dataset}.{table}`").result()
        return [dict(r) for r in rows]

    return q("customers"), q("orders"), q("commodities")


def cell_key(pin, age_g, gender, rfm) -> tuple:
    return (str(pin or ""), str(age_g or "unknown"), str(gender or "unknown"), str(rfm or "unknown"))


def bump_count(store: dict, key: str, n: int = 1):
    store[key] = store.get(key, 0) + n


def build(customers, orders, commodities, pin_pop: dict) -> dict:
    cust_by_id = {str(c["_id"]): c for c in customers}
    orders_by_cust: dict[str, list] = defaultdict(list)
    for o in orders:
        orders_by_cust[str(o["customer_id"])].append(o)

    # customer_cells: pin × age × gender × rfm
    customer_cells: dict[tuple, dict] = {}
    for c in customers:
        cid = str(c["_id"])
        key = cell_key(c.get("pin_code"), age_group(c.get("age")), c.get("gender"), c.get("rfm_segment"))
        cell = customer_cells.setdefault(
            key,
            {
                "pin_code": key[0],
                "age_group": key[1],
                "gender": key[2],
                "rfm_segment": key[3],
                "customers": 0,
                "orders": 0,
                "revenue": 0.0,
                "clothes": 0,
                "lat_sum": 0.0,
                "lon_sum": 0.0,
                "coord_n": 0,
                "services": {},
                "orders_by_month": {},
            },
        )
        cell["customers"] += 1
        try:
            lat, lon = float(c["lat"]), float(c["lon"])
            cell["lat_sum"] += lat
            cell["lon_sum"] += lon
            cell["coord_n"] += 1
        except (TypeError, ValueError, KeyError):
            pass

        for o in orders_by_cust.get(cid, []):
            cell["orders"] += 1
            try:
                cell["revenue"] += float(o.get("amount") or 0)
            except (TypeError, ValueError):
                pass
            try:
                cell["clothes"] += int(float(o.get("total_number_of_clothes") or 0))
            except (TypeError, ValueError):
                pass
            svc = str(o.get("service") or "unknown")
            bump_count(cell["services"], svc)
            month = str(o.get("scheduled_date") or "")[:7] or "unknown"
            bump_count(cell["orders_by_month"], month)

    # garment_cells: same dims + brand
    garment_cells: dict[tuple, dict] = {}
    for g in commodities:
        c = cust_by_id.get(str(g.get("customer_id")))
        if not c:
            continue
        brand = str(g.get("brand") or "unknown")
        key = cell_key(c.get("pin_code"), age_group(c.get("age")), c.get("gender"), c.get("rfm_segment")) + (brand,)
        cell = garment_cells.setdefault(
            key,
            {
                "pin_code": key[0],
                "age_group": key[1],
                "gender": key[2],
                "rfm_segment": key[3],
                "brand": brand,
                "garments": 0,
                "sizes": {},
                "colors": {},
                "patterns": {},
                "materials": {},
                "sleeve_types": {},
                "collar_types": {},
                "names": {},
                "confidence_sum": 0.0,
                "confidence_n": 0,
                "low_confidence_n": 0,
            },
        )
        cell["garments"] += 1
        bump_count(cell["sizes"], str(g.get("size") or "unknown"))
        bump_count(cell["colors"], str(g.get("color") or "unknown"))
        bump_count(cell["patterns"], str(g.get("pattern") or "unknown"))
        bump_count(cell["materials"], str(g.get("material") or "unknown"))
        bump_count(cell["sleeve_types"], str(g.get("sleeve_type") or "unknown"))
        bump_count(cell["collar_types"], str(g.get("collar_type") or "unknown"))
        bump_count(cell["names"], str(g.get("name") or "unknown"))
        try:
            score = float(g.get("confidence_score") or 0)
            cell["confidence_sum"] += score
            cell["confidence_n"] += 1
            if score < 0.8:
                cell["low_confidence_n"] += 1
        except (TypeError, ValueError):
            pass

    def finalize_customer(cell: dict) -> dict:
        n = cell.pop("coord_n")
        lat_sum = cell.pop("lat_sum")
        lon_sum = cell.pop("lon_sum")
        return {
            **{k: cell[k] for k in ("pin_code", "age_group", "gender", "rfm_segment", "customers", "orders", "revenue", "clothes")},
            "centroid_lat": round(lat_sum / n, 5) if n else None,
            "centroid_lon": round(lon_sum / n, 5) if n else None,
            "services": [{"value": k, "count": v} for k, v in sorted(cell["services"].items(), key=lambda x: -x[1])],
            "orders_by_month": [{"month": k, "orders": v} for k, v in sorted(cell["orders_by_month"].items())],
        }

    def finalize_garment(cell: dict) -> dict:
        return {
            **{k: cell[k] for k in ("pin_code", "age_group", "gender", "rfm_segment", "brand", "garments")},
            "sizes": [{"value": k, "count": v} for k, v in sorted(cell["sizes"].items(), key=lambda x: -x[1])],
            "colors": [{"value": k, "count": v} for k, v in sorted(cell["colors"].items(), key=lambda x: -x[1])],
            "patterns": [{"value": k, "count": v} for k, v in sorted(cell["patterns"].items(), key=lambda x: -x[1])],
            "materials": [{"value": k, "count": v} for k, v in sorted(cell["materials"].items(), key=lambda x: -x[1])],
            "sleeve_types": [{"value": k, "count": v} for k, v in sorted(cell["sleeve_types"].items(), key=lambda x: -x[1])],
            "collar_types": [{"value": k, "count": v} for k, v in sorted(cell["collar_types"].items(), key=lambda x: -x[1])],
            "names": [{"value": k, "count": v} for k, v in sorted(cell["names"].items(), key=lambda x: -x[1])[:10]],
            "confidence_sum": round(cell["confidence_sum"], 4),
            "confidence_n": cell["confidence_n"],
            "low_confidence_n": cell["low_confidence_n"],
        }

    cust_list = [finalize_customer(c) for c in customer_cells.values()]
    garm_list = [finalize_garment(c) for c in garment_cells.values()]

    # PIN hubs for map (centroid + population from JSON)
    pin_agg: dict[str, dict] = {}
    for c in cust_list:
        pin = c["pin_code"]
        hub = pin_agg.setdefault(pin, {"lat_sum": 0.0, "lon_sum": 0.0, "n": 0, "customers": 0, "orders": 0, "revenue": 0.0})
        if c["centroid_lat"] is not None:
            hub["lat_sum"] += c["centroid_lat"] * c["customers"]
            hub["lon_sum"] += c["centroid_lon"] * c["customers"]
            hub["n"] += c["customers"]
        hub["customers"] += c["customers"]
        hub["orders"] += c["orders"]
        hub["revenue"] += c["revenue"]

    pin_geo = {}
    for pin, hub in pin_agg.items():
        pop = pin_pop.get(pin) or {}
        if pin.startswith("_"):
            continue
        pin_geo[pin] = {
            "area_label": pop.get("area_label", pin),
            "centroid_lat": round(hub["lat_sum"] / hub["n"], 5) if hub["n"] else None,
            "centroid_lon": round(hub["lon_sum"] / hub["n"], 5) if hub["n"] else None,
            "population_pin": pop.get("population_pin"),
            "pop_buffer_1km": pop.get("pop_buffer_1km"),
            "pop_buffer_2km": pop.get("pop_buffer_2km"),
            "pop_buffer_4km": pop.get("pop_buffer_4km"),
            "customers": hub["customers"],
            "orders": hub["orders"],
            "revenue": round(hub["revenue"], 2),
        }

    # Also attach any pins that exist only in population file
    for pin, pop in pin_pop.items():
        if pin.startswith("_") or not isinstance(pop, dict):
            continue
        if pin not in pin_geo:
            pin_geo[pin] = {
                "area_label": pop.get("area_label", pin),
                "centroid_lat": None,
                "centroid_lon": None,
                "population_pin": pop.get("population_pin"),
                "pop_buffer_1km": pop.get("pop_buffer_1km"),
                "pop_buffer_2km": pop.get("pop_buffer_2km"),
                "pop_buffer_4km": pop.get("pop_buffer_4km"),
                "customers": 0,
                "orders": 0,
                "revenue": 0,
            }

    brands = sorted({g["brand"] for g in garm_list})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "pin_codes": sorted(pin_geo.keys()),
            "age_groups": sorted({c["age_group"] for c in cust_list}),
            "genders": sorted({c["gender"] for c in cust_list}),
            "rfm_segments": sorted({c["rfm_segment"] for c in cust_list}),
            "brands": brands,
        },
        "pin_geo": pin_geo,
        "customer_cells": cust_list,
        "garment_cells": garm_list,
    }


def main():
    p = argparse.ArgumentParser(description="Aggregate metrics for the command-center dashboard")
    p.add_argument("--customers", type=Path, default=DEFAULT_SAMPLE / "customers.csv")
    p.add_argument("--orders", type=Path, default=DEFAULT_SAMPLE / "orders.csv")
    p.add_argument("--commodities", type=Path, default=DEFAULT_SAMPLE / "commodities.csv")
    p.add_argument("--pin-population", type=Path, default=PIN_POP_PATH)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    p.add_argument("--bq", action="store_true", help="Read tables from BigQuery instead of CSV")
    p.add_argument("--project", default="", help="GCP project id (required with --bq)")
    p.add_argument("--dataset", default="suffai_ops")
    args = p.parse_args()

    pin_pop = json.loads(args.pin_population.read_text(encoding="utf-8"))

    if args.bq:
        if not args.project:
            raise SystemExit("--project is required with --bq")
        customers, orders, commodities = load_from_bq(args.project, args.dataset)
    else:
        customers = read_csv(args.customers)
        orders = read_csv(args.orders)
        commodities = read_csv(args.commodities)

    payload = build(customers, orders, commodities, pin_pop)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} ({len(payload['customer_cells'])} customer cells, {len(payload['garment_cells'])} garment cells)")


if __name__ == "__main__":
    main()
