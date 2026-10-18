from pathlib import Path

import pytest

import aggregate_metrics as agg
import load_csv_bq

SAMPLE = Path(__file__).resolve().parents[2] / "command_center_data" / "sample"
DDL = load_csv_bq.DDL_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("table", ["customers", "orders", "commodities"])
def test_sample_csvs_only_use_declared_columns(table):
    header = load_csv_bq.csv_header(SAMPLE / f"{table}.csv")
    schema, _missing = load_csv_bq.plan_load(table, header, DDL)
    assert [c[0] for c in schema] == header


def test_commodities_schema_follows_header_and_adds_missing_confidence():
    header = load_csv_bq.csv_header(SAMPLE / "commodities.csv")
    schema, missing = load_csv_bq.plan_load("commodities", header, DDL)
    assert "confidence_score" not in header
    assert ("confidence_score", "FLOAT64", False) in missing
    assert dict((n, t) for n, t, _ in schema)["reason"] == "STRING"


def test_customers_phone_number_stays_a_string():
    schema, _ = load_csv_bq.plan_load("customers", load_csv_bq.csv_header(SAMPLE / "customers.csv"), DDL)
    assert dict((n, t) for n, t, _ in schema)["number"] == "STRING"


def test_undeclared_csv_column_fails():
    with pytest.raises(SystemExit):
        load_csv_bq.plan_load("orders", ["_id", "customer_id", "surprise"], DDL)


def test_unscored_rows_are_not_counted_as_low_confidence():
    assert agg.confidence_score({}) is None
    assert agg.confidence_score({"confidence_score": ""}) is None
    assert agg.confidence_score({"confidence_score": "0.65"}) == 0.65
    customers = [{"_id": "c1", "pin_code": "560001", "age": 30, "gender": "F", "rfm_segment": "Loyal"}]
    commodities = [
        {"customer_id": "c1", "brand": "Zara", "name": "Shirt"},
        {"customer_id": "c1", "brand": "Zara", "name": "Shirt", "confidence_score": 0.5},
    ]
    payload = agg.build(customers, [], commodities, {})
    cells = payload["garment_cells"]
    assert sum(c["confidence_n"] for c in cells) == 1
    assert sum(c["low_confidence_n"] for c in cells) == 1
