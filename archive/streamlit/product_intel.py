"""Product intelligence: vision attributes, OCR helpers, order hierarchy."""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

from config import settings

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

COLOR_RGB = {
    "Crisp White": (245, 245, 245),
    "Deep Navy": (25, 45, 100),
    "Jet Black": (20, 20, 25),
    "Charcoal Grey": (70, 80, 90),
    "Olive Green": (80, 100, 50),
    "Sky Blue": (80, 180, 230),
    "Beige": (215, 185, 150),
    "Maroon": (180, 25, 40),
}


def order_bill(doc: dict | pd.Series) -> str:
    val = doc.get("bill_number") or doc.get("bill_no") or ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    return str(val).strip()


def order_status(doc: dict | pd.Series) -> str:
    val = doc.get("order_status") or doc.get("status") or ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    return str(val).strip()


def extract_order_hierarchy(order_doc: dict) -> dict[str, dict[str, int]]:
    hierarchy: dict[str, dict[str, int]] = {}
    items = order_doc.get("orderItems") or []
    if not isinstance(items, list):
        return hierarchy

    for itm in items:
        if not isinstance(itm, dict):
            continue
        service = str(
            itm.get("name") or itm.get("menu") or itm.get("service") or "Wash & Iron"
        ).strip().title()
        hierarchy.setdefault(service, {})
        accs = itm.get("orderedAccommodities") or itm.get("accommodities") or []
        if isinstance(accs, list) and accs:
            for acc in accs:
                if not isinstance(acc, dict):
                    continue
                c_name = str(
                    acc.get("commodityName") or acc.get("name") or "Top Wear"
                ).strip().title()
                qty = int(acc.get("quantity") or acc.get("qty") or 1)
                hierarchy[service][c_name] = hierarchy[service].get(c_name, 0) + qty
        else:
            c_name = str(itm.get("commodityName") or itm.get("type") or "Top Wear").strip().title()
            qty = int(itm.get("quantity") or itm.get("total_service_quantity") or 1)
            hierarchy[service][c_name] = hierarchy[service].get(c_name, 0) + qty
    return hierarchy


def find_order(orders_df: pd.DataFrame, query: str) -> tuple[dict | None, str]:
    if orders_df.empty or not query:
        return None, "Enter a bill number or order id."

    q = query.strip().lower().lstrip("#")

    def exact(row):
        bill = order_bill(row).lower().lstrip("#")
        oid = str(row.get("_id", "")).lower()
        return bill == q or oid == q

    def fuzzy(row):
        if len(q) < 3:
            return False
        bill = order_bill(row).lower().lstrip("#")
        oid = str(row.get("_id", "")).lower()
        return q in bill or q in oid

    hits = orders_df[orders_df.apply(exact, axis=1)]
    if hits.empty:
        hits = orders_df[orders_df.apply(fuzzy, axis=1)]
    if hits.empty:
        return None, f"No orders matching `{query}`."

    docs = []
    for _, row in hits.iterrows():
        doc = row.to_dict()
        status = order_status(doc).lower()
        if "cancel" in status:
            continue
        raw = doc.get("order_date") or doc.get("created_at")
        doc["_parsed"] = pd.to_datetime(raw, errors="coerce", utc=True)
        doc["_active"] = "delivered_to_customer" not in status
        docs.append(doc)

    if not docs:
        return None, "Matched orders are cancelled."

    active = [d for d in docs if d["_active"]] or docs
    active.sort(key=lambda d: d["_parsed"] if pd.notna(d["_parsed"]) else pd.Timestamp.min.tz_localize("UTC"), reverse=True)
    selected = active[0]
    msg = (
        f"Bill `{order_bill(selected) or query}` · "
        f"Status `{order_status(selected) or 'n/a'}` · "
        f"Items ready for capture"
    )
    return selected, msg


def list_order_suggestions(orders_df: pd.DataFrame, limit: int = 20) -> list[dict]:
    if orders_df.empty:
        return []
    work = orders_df.copy()
    work["_bill"] = work.apply(order_bill, axis=1)
    work["_status"] = work.apply(order_status, axis=1)
    work = work[~work["_status"].str.lower().str.contains("cancel", na=False)]
    work = work[work["_bill"].ne("")]
    work["_prio"] = work["_status"].str.lower().str.contains("delivered_to_customer").astype(int)
    work = work.sort_values(["_prio", "order_date"], ascending=[True, False], na_position="last")
    work = work.drop_duplicates("_bill").head(limit)
    out = []
    for _, r in work.iterrows():
        out.append({
            "bill": r["_bill"],
            "status": r["_status"],
            "date": r.get("order_date"),
            "label": f"{r['_bill']} · {r['_status']}",
        })
    return out


def extract_dominant_color(pil_img: Image.Image) -> str:
    w, h = pil_img.size
    crop = pil_img.crop((int(w * 0.3), int(h * 0.3), int(w * 0.7), int(h * 0.7))).resize((100, 100))
    rgb = np.array(crop.convert("RGB"))

    if cv2 is not None:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        pixels = rgb.reshape(-1, 3)
        hsv_p = hsv.reshape(-1, 3)
        mask = (hsv_p[:, 2] > 30) & (hsv_p[:, 2] < 245)
        if mask.sum() > 100:
            pixels = pixels[mask]
        pixels_f = np.float32(pixels)
        _, labels, centers = cv2.kmeans(
            pixels_f, 3, None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
            10, cv2.KMEANS_RANDOM_CENTERS,
        )
        dominant = centers[np.bincount(labels.flatten()).argmax()].astype(np.uint8)
        lab = cv2.cvtColor(np.array([[dominant]], dtype=np.uint8), cv2.COLOR_RGB2LAB)[0][0]
    else:
        dominant = rgb.mean(axis=(0, 1)).astype(np.uint8)
        lab = dominant

    best, best_d = "Crisp White", float("inf")
    for name, ref in COLOR_RGB.items():
        if cv2 is not None:
            ref_lab = cv2.cvtColor(np.array([[ref]], dtype=np.uint8), cv2.COLOR_RGB2LAB)[0][0]
        else:
            ref_lab = ref
        dist = math.sqrt(sum((int(a) - int(b)) ** 2 for a, b in zip(lab, ref_lab)))
        if dist < best_d:
            best, best_d = name, dist
    return best


def extract_tag_ocr(pil_img: Image.Image) -> tuple[str | None, str | None]:
    brand = size = None
    if pytesseract is None:
        return brand, size
    try:
        w, h = pil_img.size
        crop = pil_img.crop((int(w * 0.3), 0, int(w * 0.7), int(h * 0.35)))
        text = pytesseract.image_to_string(crop).upper()
        for b in settings.BRANDS:
            if b.upper() in text:
                brand = b
                break
        for s in settings.SIZES:
            if f" {s.upper()} " in f" {text} ":
                size = s
                break
    except Exception:
        pass
    return brand, size


def lifecycle_score(pil_img: Image.Image) -> int:
    if cv2 is None:
        return 85
    gray = np.array(pil_img.convert("L"))
    var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return int(np.clip((var / 500.0) * 100, 45, 98))


def save_commodity_records(records: list[dict], images: list[tuple[Image.Image, str]]) -> int:
    settings.IMAGE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    for img, filename in images:
        img.save(settings.IMAGE_STORE_DIR / filename)

    df_new = pd.DataFrame(records)
    path = settings.COMMODITY_CSV
    if path.exists():
        df_new.to_csv(path, mode="a", header=False, index=False)
    else:
        df_new.to_csv(path, index=False)
    return len(records)


def load_captured_catalog() -> pd.DataFrame:
    path = settings.COMMODITY_CSV
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


def build_wardrobe_taste(summary_df: pd.DataFrame) -> pd.DataFrame:
    """
    Verified garment attributes only.

    PRD: do not fabricate brand/color/size/material from order quantity.
    Returns captured catalog rows when present; otherwise empty (UI shows empty state).
    """
    captured = load_captured_catalog()
    if captured.empty:
        return pd.DataFrame(
            columns=[
                "customer_id", "user_name", "pincode", "area", "persona",
                "spend_share", "brand", "color", "size", "commodity", "care", "gender",
            ]
        )

    # Map captured CSV into taste shape when columns exist
    cols = {c.lower(): c for c in captured.columns}
    records = []
    for _, row in captured.iterrows():
        records.append({
            "customer_id": str(row.get(cols.get("customer_id", "customer_id"), "")),
            "user_name": row.get(cols.get("user_name", "user_name"), ""),
            "pincode": row.get(cols.get("pincode", "pincode")),
            "area": row.get(cols.get("area", "area"), "Unknown"),
            "persona": row.get(cols.get("persona", "persona"), "Unknown"),
            "spend_share": float(row.get(cols.get("spend_share", "spend_share"), 0) or 0),
            "brand": row.get(cols.get("brand", "brand"), "unknown"),
            "color": row.get(cols.get("color", "color"), "unknown"),
            "size": row.get(cols.get("size", "size"), "unknown"),
            "commodity": row.get(cols.get("commodity", "commodity"), "unknown"),
            "care": row.get(cols.get("care", "care"), "unknown"),
            "gender": row.get(cols.get("gender", "gender"), "Unknown"),
        })
    return pd.DataFrame(records)
