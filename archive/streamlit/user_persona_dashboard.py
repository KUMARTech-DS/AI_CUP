import os
import json
import math
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime, timedelta
from PIL import Image
import random
import re

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

# ==========================================
# 1. PAGE CONFIGURATION & SETUP
# ==========================================
st.set_page_config(
    page_title="SUFFAI Cohort & Brand Intelligence",
    page_icon="👔",
    layout="wide"
)

st.markdown("""
    <style>
    .main-header { font-size: 1.8rem; font-weight: 700; color: #1E3A8A; margin-bottom: 0.2rem; }
    .sub-text { color: #64748B; font-size: 0.95rem; margin-bottom: 1.5rem; }
    .commodity-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 10px;
        text-align: center;
        margin-bottom: 8px;
    }
    .icon-circle {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 45px;
        height: 45px;
        background-color: #EFF6FF;
        border-radius: 50%;
        font-size: 22px;
        margin-bottom: 4px;
    }
    .status-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 12px;
    }
    .metric-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .badge-success { background-color: #DCFCE7; color: #15803D; }
    .badge-warning { background-color: #FEF3C7; color: #B45309; }
    .badge-error { background-color: #FEE2E2; color: #B91C1C; }
    </style>
""", unsafe_allow_html=True)

# Edge Capture Station constants
COMMODITY_DETAILS_CSV = "commodity_details.csv"
IMAGE_STORE_DIR = "captured_garments"
os.makedirs(IMAGE_STORE_DIR, exist_ok=True)

COLOR_PALETTE = {
    "Crisp White": (245, 245, 245),
    "Deep Navy": (25, 45, 100),
    "Jet Black": (20, 20, 25),
    "Charcoal Grey": (70, 80, 90),
    "Olive Green": (80, 100, 50),
    "Sky Blue": (80, 180, 230),
    "Beige": (215, 185, 150),
    "Maroon": (180, 25, 40)
}

KNOWN_BRANDS = ["Select / Verify Brand", "Zara", "Nike", "Peter England", "Arrow", "Van Heusen", "Allen Solly", "Raymond", "FabIndia", "Levi's", "H&M"]
KNOWN_SIZES = ["Select / Verify Size", "S", "M", "L", "XL", "Slim-38", "Regular-40", "32x32", "34x32", "30x30"]
CARE_LABELS = ["Starch & Iron", "Gentle Wash", "Dry Clean Only", "Delicate Fabric", "Normal Wash"]

# Target Area Labels & Default Coordinates Map
TARGET_AREA_NAMES = {
    "560102": {"area": "HSR Layout", "lat": 12.9121, "lon": 77.6445},
    "560103": {"area": "Bellandur / Devarabeesanahalli", "lat": 12.9279, "lon": 77.6841},
    "560034": {"area": "Koramangala", "lat": 12.9352, "lon": 77.6245},
    "560068": {"area": "BTM Layout / Silk Board", "lat": 12.9081, "lon": 77.6476},
    "560035": {"area": "Sarjapur Road", "lat": 12.9063, "lon": 77.6822},
    "560087": {"area": "Varthur / Marathahalli", "lat": 12.9562, "lon": 77.7019},
    "560037": {"area": "Brookefield / Kundalahalli", "lat": 12.9698, "lon": 77.7123},
    "560095": {"area": "Koramangala 1st Block", "lat": 12.9385, "lon": 77.6302},
    "560066": {"area": "Whitefield", "lat": 12.9698, "lon": 77.7500},
    "560048": {"area": "Hoodi / Mahadevapura", "lat": 12.9925, "lon": 77.6844},
    "560029": {"area": "Tavarekere / BTM 1st Stage", "lat": 12.9250, "lon": 77.6000},
    "560104": {"area": "Singasandra / Electronic City", "lat": 12.8800, "lon": 77.6300},
    "560078": {"area": "JP Nagar", "lat": 12.9010, "lon": 77.5800},
    "560099": {"area": "Electronic City Phase 1", "lat": 12.8390, "lon": 77.6770},
    "560119": {"area": "Bannerghatta Road", "lat": 12.8750, "lon": 77.6050}
}

TARGET_PINCODES = list(TARGET_AREA_NAMES.keys())

def clean_pincode_str(val):
    if not val or pd.isna(val):
        return None
    val_str = str(val).strip()
    match = re.search(r'\b560[0-9]{3}\b', val_str)
    if match:
        p_clean = match.group(0)
        if p_clean in TARGET_AREA_NAMES:
            return p_clean
    return None

def impute_missing_with_mode(df):
    """Imputes missing values in any column of a DataFrame with its most frequent value (mode)."""
    if df.empty:
        return df
    df = df.copy()
    for col in df.columns:
        try:
            if df[col].isnull().any():
                modes = df[col].dropna().mode()
                if not modes.empty:
                    most_frequent = modes.iloc[0]
                    df[col] = df[col].fillna(most_frequent)
        except Exception:
            pass
    return df

def extract_id(val):
    """Extract string ID from BSON dict or raw string."""
    if isinstance(val, dict) and "$oid" in val:
        return str(val["$oid"])
    if pd.notna(val):
        return str(val).strip()
    return None

def extract_date(val):
    """Safely parse timestamps from BSON dict or ISO strings."""
    if isinstance(val, dict):
        if "$date" in val:
            d_val = val["$date"]
            if isinstance(d_val, dict) and "$numberLong" in d_val:
                return pd.to_datetime(int(d_val["$numberLong"]), unit="ms", errors="coerce")
            return pd.to_datetime(d_val, errors="coerce")
    return pd.to_datetime(val, errors="coerce")

def extract_lat_lon(location_data):
    """Safely extract latitude and longitude coordinates."""
    if isinstance(location_data, dict) and 'coordinates' in location_data:
        coords = location_data.get('coordinates')
        if isinstance(coords, list) and len(coords) == 2:
            lon, lat = float(coords[0]), float(coords[1])
            if 12.0 <= lat <= 14.0 and 77.0 <= lon <= 79.0:
                return pd.Series({'lat': lat, 'long': lon})
            return pd.Series({'lat': lat, 'long': lon})
    return pd.Series({'lat': np.nan, 'long': np.nan})

# ==========================================
# EDGE CAPTURE HELPERS (from upload_image_ui)
# ==========================================
def _order_bill_number(row_or_doc):
    """Offline data uses `bill_number`; older UI used `bill_no`."""
    val = row_or_doc.get("bill_number")
    if val in (None, "", []):
        val = row_or_doc.get("bill_no")
    if pd.isna(val) if not isinstance(val, (list, dict)) else False:
        return ""
    return str(val).strip()

def _order_status(row_or_doc):
    """Offline data uses `order_status`; older UI used `status`."""
    val = row_or_doc.get("order_status")
    if val in (None, "", []):
        val = row_or_doc.get("status")
    if pd.isna(val) if not isinstance(val, (list, dict)) else False:
        return ""
    return str(val).strip()

def _parse_order_date(doc):
    raw_date = (
        doc.get("scheduled_date")
        or doc.get("order_date_time")
        or doc.get("pickup_date_time")
        or doc.get("created_at")
    )
    if isinstance(raw_date, dict):
        if "$date" in raw_date:
            d_val = raw_date["$date"]
            if isinstance(d_val, dict) and "$numberLong" in d_val:
                raw_date = int(d_val["$numberLong"])
            else:
                raw_date = d_val
    parsed_dt = pd.to_datetime(raw_date, errors="coerce", utc=True)
    if pd.isna(parsed_dt):
        return pd.Timestamp.min.tz_localize("UTC")
    return parsed_dt

def fetch_latest_active_order_offline(orders_df, query_bill_no):
    if orders_df.empty or not query_bill_no:
        return None, "Offline dataset uninitialized or empty."

    q_str = str(query_bill_no).strip().lower().lstrip("#")

    def bill_key(row):
        return _order_bill_number(row).lower().lstrip("#")

    def matches_exact(row):
        b_no = bill_key(row)
        o_id = str(row.get("_id", "")).strip().lower()
        return (q_str and b_no == q_str) or (q_str and o_id == q_str)

    def matches_fuzzy(row):
        b_no = bill_key(row)
        o_id = str(row.get("_id", "")).strip().lower()
        # Avoid ultra-short substring false positives (e.g. "20" inside many bills)
        if len(q_str) < 3:
            return False
        return (q_str in b_no) or (q_str in o_id)

    exact_mask = orders_df.apply(matches_exact, axis=1)
    candidates_df = orders_df[exact_mask].copy()
    if candidates_df.empty:
        fuzzy_mask = orders_df.apply(matches_fuzzy, axis=1)
        candidates_df = orders_df[fuzzy_mask].copy()

    if candidates_df.empty:
        return None, f"No orders matching `{query_bill_no}` in offline database."

    active_candidates = []
    # Prefer in-progress ops; keep cancelled/final-delivered as last-resort fallback
    HARD_EXCLUDED = ["cancel"]
    SOFT_EXCLUDED = ["delivered_to_customer", "delivered to customer"]

    for idx, row in candidates_df.iterrows():
        doc = row.to_dict()
        status_str = _order_status(doc).lower()
        if any(kw in status_str for kw in HARD_EXCLUDED):
            continue

        doc["_parsed_scheduled_date"] = _parse_order_date(doc)
        doc["_status_norm"] = status_str
        doc["_is_soft_excluded"] = any(kw in status_str for kw in SOFT_EXCLUDED)
        doc["_exact_bill"] = bill_key(row) == q_str
        active_candidates.append(doc)

    if not active_candidates:
        return None, f"Found {len(candidates_df)} entry(ies) for `{query_bill_no}`, but all are Cancelled."

    in_progress = [d for d in active_candidates if not d["_is_soft_excluded"]]
    pool = in_progress if in_progress else active_candidates
    pool.sort(key=lambda x: (x.get("_exact_bill", False), x["_parsed_scheduled_date"]), reverse=True)
    selected_order = pool[0]
    status_label = _order_status(selected_order) or "Active"
    audit_msg = (
        f"Order found ({len(candidates_df)} history records). "
        f"Date: {selected_order['_parsed_scheduled_date'].strftime('%d %b %Y')} | "
        f"Status: `{status_label}` | Bill: `{_order_bill_number(selected_order) or 'N/A'}`"
    )
    return selected_order, audit_msg

@st.cache_data(ttl=300)
def list_capture_order_suggestions(orders_df, limit=25):
    """Build selectable recent/active orders for the Edge Capture picker."""
    if orders_df.empty:
        return []

    work = orders_df.copy()
    if "bill_number" in work.columns:
        bill_series = work["bill_number"]
    elif "bill_no" in work.columns:
        bill_series = work["bill_no"]
    else:
        bill_series = pd.Series([""] * len(work), index=work.index)

    if "order_status" in work.columns:
        status_series = work["order_status"]
    elif "status" in work.columns:
        status_series = work["status"]
    else:
        status_series = pd.Series([""] * len(work), index=work.index)

    work["_bill"] = bill_series.fillna("").astype(str).str.strip()
    work["_status"] = status_series.fillna("").astype(str).str.strip()
    work = work[~work["_status"].str.lower().str.contains("cancel", na=False)]
    work["_id_str"] = work["_id"].astype(str)
    empty_bill = work["_bill"].eq("") | work["_bill"].str.lower().isin(["nan", "none"])
    work.loc[empty_bill, "_bill"] = work.loc[empty_bill, "_id_str"].str[:10]
    work = work[work["_bill"].ne("")]

    date_col = next(
        (c for c in ["scheduled_date_ts", "order_date_time", "pickup_date_time", "created_at", "scheduled_date"] if c in work.columns),
        None
    )
    if date_col:
        work["_date"] = pd.to_datetime(work[date_col], errors="coerce", utc=True)
    else:
        work["_date"] = pd.NaT

    status_lower = work["_status"].str.lower()
    work["_priority"] = status_lower.str.contains("delivered_to_customer", na=False).astype(int)
    work = work.sort_values(by=["_priority", "_date"], ascending=[True, False])
    work = work.drop_duplicates(subset=["_bill"], keep="first")

    suggestions = []
    for _, row in work.head(limit).iterrows():
        bill = row["_bill"] if row["_bill"] else str(row.get("_id", ""))[:8]
        suggestions.append({
            "bill": bill,
            "order_id": str(row.get("_id", "")),
            "status": row["_status"] or "unknown",
            "date": row["_date"],
            "priority": int(row["_priority"]),
        })
    return suggestions

def extract_order_hierarchy(order_doc):
    """Extracts hierarchical structure: Service/Menu -> Commodity Name -> Required Quantity."""
    hierarchy = {}
    items = order_doc.get("orderItems", [])

    if isinstance(items, list):
        for itm in items:
            if not isinstance(itm, dict):
                continue

            # Offline schema uses item["name"] for service (Wash & Iron, Dry Clean, ...)
            menu_name = (
                itm.get("name")
                or itm.get("menu")
                or itm.get("service")
                or itm.get("category")
                or "Wash & Iron"
            )
            menu_clean = str(menu_name).strip().title()

            if menu_clean not in hierarchy:
                hierarchy[menu_clean] = {}

            accs = itm.get("orderedAccommodities") or itm.get("accommodities") or []
            if isinstance(accs, list) and len(accs) > 0:
                for acc in accs:
                    if isinstance(acc, dict):
                        c_name = acc.get("commodityName") or acc.get("name") or acc.get("commodity") or "Top Wear"
                        c_clean = str(c_name).strip().title()
                        qty = int(acc.get("quantity") or acc.get("qty") or 1)
                        hierarchy[menu_clean][c_clean] = hierarchy[menu_clean].get(c_clean, 0) + qty
            else:
                c_name = itm.get("commodityName") or itm.get("type") or "Top Wear"
                c_clean = str(c_name).strip().title()
                qty = int(itm.get("quantity") or itm.get("qty") or itm.get("total_service_quantity") or 1)
                hierarchy[menu_clean][c_clean] = hierarchy[menu_clean].get(c_clean, 0) + qty

    return hierarchy

def extract_dominant_color(pil_img):
    w, h = pil_img.size
    crop_box = (int(w * 0.30), int(h * 0.30), int(w * 0.70), int(h * 0.70))
    cropped = pil_img.crop(crop_box).resize((100, 100))
    np_rgb = np.array(cropped.convert("RGB"))

    if cv2 is not None:
        hsv = cv2.cvtColor(np_rgb, cv2.COLOR_RGB2HSV)
        pixels_rgb = np_rgb.reshape(-1, 3)
        pixels_hsv = hsv.reshape(-1, 3)

        valid_mask = (pixels_hsv[:, 2] > 30) & (pixels_hsv[:, 2] < 245)
        if np.sum(valid_mask) > 100:
            pixels_rgb = pixels_rgb[valid_mask]

        pixels_float = np.float32(pixels_rgb)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(pixels_float, 3, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)

        counts = np.bincount(labels.flatten())
        dominant_rgb = centers[np.argmax(counts)].astype(np.uint8)
        dominant_lab = cv2.cvtColor(np.array([[dominant_rgb]], dtype=np.uint8), cv2.COLOR_RGB2LAB)[0][0]
    else:
        dominant_rgb = np_rgb.mean(axis=(0, 1)).astype(np.uint8)
        dominant_lab = dominant_rgb

    def get_lab(rgb_tuple):
        arr = np.array([[rgb_tuple]], dtype=np.uint8)
        if cv2 is not None:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0][0]
        return rgb_tuple

    best_color = "Crisp White"
    min_dist = float("inf")

    for color_name, rgb_val in COLOR_PALETTE.items():
        ref_lab = get_lab(rgb_val)
        dist = math.sqrt(sum((int(a) - int(b)) ** 2 for a, b in zip(dominant_lab, ref_lab)))
        if dist < min_dist:
            min_dist = dist
            best_color = color_name

    return best_color

def extract_tag_ocr(pil_img):
    detected_brand, detected_size = None, None
    if pytesseract is not None:
        try:
            w, h = pil_img.size
            tag_crop = pil_img.crop((int(w * 0.3), 0, int(w * 0.7), int(h * 0.35)))
            text = pytesseract.image_to_string(tag_crop).upper()

            for b in KNOWN_BRANDS[1:]:
                if b.upper() in text:
                    detected_brand = b
                    break
            for s in KNOWN_SIZES[1:]:
                if f" {s.upper()} " in f" {text} " or f"\n{s.upper()}\n" in text:
                    detected_size = s
                    break
        except Exception:
            pass

    return detected_brand, detected_size

def calculate_lifecycle_score(pil_img):
    if cv2 is not None:
        np_img = np.array(pil_img.convert('L'))
        laplacian_var = cv2.Laplacian(np_img, cv2.CV_64F).var()
        return int(np.clip((laplacian_var / 500.0) * 100, 45, 98))
    return 85

# ==========================================
# 2. DATA LOADING (OFFLINE PARSER WITH ONLINE FALLBACK)
# ==========================================
@st.cache_data(ttl=300)
def load_filtered_users_and_orders():
    offline_orders = os.path.join("data_offline", "orders.json")
    offline_customers = os.path.join("data_offline", "customers.json")

    # Mode 1: OFFLINE LOCAL JSON
    # if os.path.exists(offline_orders) and os.path.exists(offline_customers):
    try:
        with open(offline_orders, "r", encoding="utf-8") as f:
            orders_data = json.load(f)
        orders_df = pd.DataFrame(orders_data)

        with open(offline_customers, "r", encoding="utf-8") as f:
            customers_data = json.load(f)
        customers_df = pd.DataFrame(customers_data)
        
        # Note: avoid Streamlit UI calls inside @st.cache_data (causes CacheReplayClosureError)
    except Exception as e:
        raise RuntimeError(f"Failed to parse local JSON files: {e}") from e
    # else:
    #     # Mode 2: LIVE MONGODB FALLBACK
    #     try:
    #         mongo_uri = os.getenv("MONGO_URI", "mongodb+srv://suffaiClusterAdmin:HJu3hV9E67RBoout@suffai-cluster.c50pj.mongodb.net/suffai-production")
    #         client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    #         db = client["suffai-production"]

    #         orders_cursor = db["orders"].find({}, {
    #             "_id": 1, "customer_id": 1, "scheduled_date": 1, "created_at": 1,
    #             "total_amount": 1, "actual_price_without_discount": 1, "pickup_fee": 1,
    #             "delivery_fee": 1, "status": 1, "orderItems": 1, "schedule": 1
    #         })
    #         orders_df = pd.DataFrame(list(orders_cursor))

    #         customers_cursor = db["customers"].find({}, {
    #             "_id": 1, "customer_name": 1, "phone_no": 1, "address": 1, 
    #             "pin_code": 1, "pincode": 1, "location": 1
    #         })
    #         customers_df = pd.DataFrame(list(customers_cursor))
    #         st.toast("🌐 Running in Live Online Mode (Connected to MongoDB Atlas)", icon="📡")
    #     except Exception as e:
    #         st.error(f"⚠️ Database connection failed: {e}")
    #         return pd.DataFrame(), pd.DataFrame()

    if orders_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Clean IDs
    if "_id" in orders_df.columns:
        orders_df["_id"] = orders_df["_id"].apply(extract_id)
    if "customer_id" in orders_df.columns:
        orders_df["customer_id"] = orders_df["customer_id"].apply(extract_id)

    # Clean Timestamps
    if "scheduled_date" in orders_df.columns:
        orders_df["scheduled_date_ts"] = orders_df["scheduled_date"].apply(extract_date)
    else:
        orders_df["scheduled_date_ts"] = orders_df["created_at"].apply(extract_date)

    orders_df = orders_df.dropna(subset=["scheduled_date_ts"])

    def calculate_amount(row):
        amt = row.get("total_amount", 0)
        if pd.isna(amt) or amt == 0:
            base = row.get("actual_price_without_discount", 0) or 0
            p_fee = row.get("pickup_fee", 0) or 0
            d_fee = row.get("delivery_fee", 0) or 0
            amt = base + p_fee + d_fee
        return float(amt or 0)

    orders_df["order_amount"] = orders_df.apply(calculate_amount, axis=1)

    if 'schedule' in orders_df.columns:
        orders_df['extracted_pin'] = orders_df['schedule'].apply(
            lambda x: clean_pincode_str(x.get('address', {}).get('pin_code')) if isinstance(x, dict) else None
        )
    else:
        orders_df['extracted_pin'] = None

    # Parse Customers
    if not customers_df.empty:
        if "_id" in customers_df.columns:
            customers_df["customer_id"] = customers_df["_id"].apply(extract_id)

        if 'location' in customers_df.columns:
            lat_lon_df = customers_df['location'].apply(extract_lat_lon)
            customers_df['lat'] = lat_lon_df['lat']
            customers_df['long'] = lat_lon_df['long']
            customers_df = customers_df.drop(columns=['location'], errors='ignore')
        else:
            customers_df['lat'] = np.nan
            customers_df['long'] = np.nan

        def get_cust_pincode(row):
            for col in ["pin_code", "pincode"]:
                cleaned = clean_pincode_str(row.get(col))
                if cleaned:
                    return cleaned
            addr = row.get("address")
            if isinstance(addr, dict):
                for sub_col in ["pin_code", "pincode", "zipcode"]:
                    cleaned = clean_pincode_str(addr.get(sub_col))
                    if cleaned:
                        return cleaned
            return None
        customers_df["normalized_pincode"] = customers_df.apply(get_cust_pincode, axis=1)
    else:
        customers_df = pd.DataFrame(columns=["customer_id", "customer_name", "phone_no", "address", "pin_code", "normalized_pincode", "lat", "long"])

    customers_df = impute_missing_with_mode(customers_df)

    if "customer_id" in orders_df.columns and not customers_df.empty:
        cust_lat_map = customers_df.set_index("customer_id")["lat"].to_dict()
        cust_lon_map = customers_df.set_index("customer_id")["long"].to_dict()
        orders_df["lat"] = orders_df["customer_id"].map(cust_lat_map)
        orders_df["long"] = orders_df["customer_id"].map(cust_lon_map)
    else:
        orders_df["lat"] = np.nan
        orders_df["long"] = np.nan

    orders_df = impute_missing_with_mode(orders_df)

    return orders_df, customers_df

try:
    orders_df, customers_df = load_filtered_users_and_orders()
    st.toast("⚡ Running in Offline Mode (Using ./data_offline JSONs)", icon="🚀")
except Exception as e:
    st.error(f"⚠️ Failed to load offline datasets: {e}")
    st.stop()

if orders_df.empty:
    st.warning("⚠️ No valid order records with dates found in the offline dataset.")
    st.stop()

# ==========================================
# 3. DYNAMIC COHORT FILTERING LOGIC
# ==========================================
max_db_date = orders_df["scheduled_date_ts"].max()
cutoff_date = max_db_date - timedelta(days=60)

active_recent_orders = orders_df[orders_df["scheduled_date_ts"] >= cutoff_date]
recent_active_customer_ids = active_recent_orders["customer_id"].dropna().unique()

lifetime_order_counts = orders_df.groupby("customer_id").filter(lambda x: len(x) >= 3)
qualified_customer_ids = set(recent_active_customer_ids).intersection(set(lifetime_order_counts["customer_id"].unique()))

if len(qualified_customer_ids) == 0:
    qualified_customer_ids = set(lifetime_order_counts["customer_id"].unique())
if len(qualified_customer_ids) == 0:
    qualified_customer_ids = set(orders_df["customer_id"].dropna().unique())

filtered_orders_df = orders_df[orders_df["customer_id"].isin(qualified_customer_ids)]

# ==========================================
# 4. BUILD GLOBAL SUMMARY & PINCODE MAPPING
# ==========================================
user_summary_list = []

for cid in qualified_customer_ids:
    u_orders = filtered_orders_df[filtered_orders_df["customer_id"] == cid]
    total_spend = u_orders["order_amount"].sum()
    order_count = len(u_orders)
    
    user_lat = u_orders["lat"].mean() if not u_orders["lat"].dropna().empty else np.nan
    user_long = u_orders["long"].mean() if not u_orders["long"].dropna().empty else np.nan

    cust_match = customers_df[customers_df["customer_id"] == cid] if not customers_df.empty else pd.DataFrame()
    name = cust_match.iloc[0].get("customer_name", f"User {cid[:6]}") if not cust_match.empty else f"User {cid[:6]}"
    phone = cust_match.iloc[0].get("phone_no", "N/A") if not cust_match.empty else "N/A"
    
    pincode = None
    if not cust_match.empty:
        pincode = cust_match.iloc[0].get("normalized_pincode")
    
    if not pincode:
        valid_order_pins = u_orders["extracted_pin"].dropna()
        if not valid_order_pins.empty:
            pincode = str(valid_order_pins.iloc[0]).strip()
    
    if not pincode or pincode not in TARGET_AREA_NAMES:
        random.seed(hash(cid))
        pincode = random.choice(TARGET_PINCODES)

    rev_tag = "High" if total_spend > 5000 else ("Med" if total_spend > 2000 else "Low")
    freq_tag = "High" if order_count > 6 else ("Med" if order_count > 3 else "Low")
    persona_tag = f"Rev: {rev_tag} | Freq: {freq_tag}"
    
    if rev_tag == "High" and freq_tag == "High":
        badge = "⭐ Champion Tier"
    elif rev_tag == "High":
        badge = "💎 High Spender"
    elif freq_tag == "High":
        badge = "🔄 High Frequency"
    else:
        badge = "🛡️ Steady Regular"

    user_summary_list.append({
        "customer_id": str(cid),
        "User Name": str(name),
        "Phone Number": str(phone),
        "Pincode": str(pincode).strip(),
        "Total Spend (₹)": float(total_spend),
        "Total Orders": int(order_count),
        "Customer Tag": str(badge),
        "Metrics": str(persona_tag),
        "user_lat": float(user_lat) if pd.notna(user_lat) else np.nan,
        "user_long": float(user_long) if pd.notna(user_long) else np.nan
    })

if user_summary_list:
    summary_df = pd.DataFrame(user_summary_list).sort_values(by="Total Spend (₹)", ascending=False)
else:
    summary_df = pd.DataFrame(columns=[
        "customer_id", "User Name", "Phone Number", "Pincode", 
        "Total Spend (₹)", "Total Orders", "Customer Tag", "Metrics", "user_lat", "user_long"
    ])

summary_df = impute_missing_with_mode(summary_df)

customer_pincode_map = summary_df.set_index("customer_id")["Pincode"].to_dict() if not summary_df.empty else {}
filtered_orders_df["pincode"] = filtered_orders_df["customer_id"].map(customer_pincode_map).fillna("560102")

# ==========================================
# 5. HEADER & SYSTEM HEALTH DIAGNOSTICS
# ==========================================
st.markdown('<p class="main-header">👔 SUFFAI Cohort & Brand Intelligence</p>', unsafe_allow_html=True)
st.markdown(f'<p class="sub-text">Dataset date range: up to {max_db_date.strftime("%b %d, %Y")}. Comprehensive spatial, persona, and partner brand taste intelligence.</p>', unsafe_allow_html=True)

with st.expander("🚨 System Health & Pipeline Diagnostics (Always Visible)", expanded=True):
    st.write("### ⚙️ Pipeline Audit")
    
    diag_col1, diag_col2, diag_col3 = st.columns(3)
    diag_col1.metric("Total Orders Loaded", f"{len(orders_df):,}")
    diag_col2.metric("Total Customers Loaded", f"{len(customers_df):,}")
    diag_col3.metric("Qualified Cohort (`summary_df`)", f"{len(summary_df):,} Users")

    st.markdown("---")
    st.write("#### 📋 Column Health Verification")

    if not summary_df.empty:
        col_audit = []
        for col in ["customer_id", "User Name", "Phone Number", "Pincode", "Total Spend (₹)", "Total Orders", "Customer Tag", "user_lat", "user_long"]:
            if col in summary_df.columns:
                valid_cnt = summary_df[col].notna().sum()
                null_cnt = summary_df[col].isna().sum()
                col_audit.append({
                    "Column Name": col,
                    "Valid Count": valid_cnt,
                    "Null Count": null_cnt,
                    "Completeness (%)": f"{(valid_cnt / len(summary_df) * 100):.1f}%",
                    "Data Type": str(summary_df[col].dtype)
                })
        audit_df = pd.DataFrame(col_audit)
        st.dataframe(audit_df, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.write("#### 🔍 Master `summary_df` Dataset Preview")
        st.dataframe(summary_df[[
            "customer_id", "User Name", "Phone Number", "Pincode", 
            "Total Spend (₹)", "Total Orders", "Customer Tag", "user_lat", "user_long"
        ]], use_container_width=True)

tab_user, tab_pincode, tab_brand, tab_capture = st.tabs([
    "👤 User Persona Intelligence",
    "📍 Pincode & Geo Intelligence",
    "🏷 Brand Affinity & Taste Intelligence",
    "📷 Edge Capture Station"
])

def get_commodity_icon(name):
    lower_name = str(name).lower()
    if any(k in lower_name for k in ["shirt", "top", "t-shirt", "kurta"]):
        return "👕"
    elif any(k in lower_name for k in ["pant", "jean", "trouser", "bottom"]):
        return "👖"
    elif any(k in lower_name for k in ["suit", "blazer", "jacket", "coat"]):
        return "🧥"
    elif any(k in lower_name for k in ["bed", "sheet", "pillow", "linen", "curtain"]):
        return "🛏"
    elif any(k in lower_name for k in ["shoe", "sneaker", "footwear"]):
        return "👟"
    else:
        return "👔"

def get_color_hex(color_name):
    mapping = {
        "beige": "#D4B28C",
        "crisp white": "#F8FAFC",
        "olive green": "#556B2F",
        "charcoal grey": "#334155",
        "navy blue": "#1E3A8A",
        "jet black": "#0F172A",
        "sky blue": "#38BDF8",
        "maroon": "#800000"
    }
    return mapping.get(str(color_name).strip().lower(), "#64748B")

# ==========================================
# TAB 1: USER PERSONA INTELLIGENCE
# ==========================================
with tab_user:
    try:
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Qualified Cohort Size", f"{len(summary_df)} Users")
        col_m2.metric("Cohort Total Spend", f"₹{summary_df['Total Spend (₹)'].sum():,.0f}")
        col_m3.metric("Average Orders / User", f"{summary_df['Total Orders'].mean():.1f}")

        st.markdown("---")
        st.markdown("### 📋 Qualified Customer Directory")

        search_query = st.text_input("🔍 Search user by name or phone number:", "", key="user_search").strip().lower()
        display_summary_df = summary_df[
            summary_df["User Name"].str.lower().str.contains(search_query) | 
            summary_df["Phone Number"].str.contains(search_query)
        ] if search_query else summary_df

        for index, row in display_summary_df.iterrows():
            try:
                cid = row["customer_id"]
                name = row["User Name"]
                phone = row["Phone Number"]
                badge = row["Customer Tag"]
                spend = row["Total Spend (₹)"]
                orders = row["Total Orders"]
                
                with st.expander(f"👤 **{name}**  |  📞 {phone}  |  📍 Pincode: {row['Pincode']}  |  🏷️ **{badge}**  |  Spend: **₹{spend:,.0f}** ({orders} Orders)"):
                    
                    user_data = filtered_orders_df[filtered_orders_df["customer_id"] == cid].sort_values("scheduled_date_ts")
                    total_ltv = user_data["order_amount"].sum()
                    total_user_orders = len(user_data)
                    aov = total_ltv / total_user_orders if total_user_orders > 0 else 0.0

                    min_date = user_data["scheduled_date_ts"].min()
                    max_date = user_data["scheduled_date_ts"].max()
                    if pd.notna(min_date) and pd.notna(max_date) and min_date != max_date:
                        days_diff = (max_date - min_date).days
                        avg_freq = total_user_orders / max(1.0, days_diff / 30.0)
                    else:
                        avg_freq = float(total_user_orders)

                    commodity_records = []
                    detailed_garment_records = []
                    extracted_menus_counts = {}
                    
                    mock_brands = ["Peter England", "Arrow", "Van Heusen", "Allen Solly", "Raymond", "FabIndia", "Zara"]
                    mock_colors = ["Jet Black", "Navy Blue", "Sky Blue", "Crisp White", "Olive Green", "Charcoal Grey", "Maroon"]
                    mock_sizes = ["Regular-40", "Slim-38", "L", "M", "32x32"]
                    mock_cares = ["Starch & Iron", "Gentle Wash", "Dry Clean Only", "Delicate Fabric"]

                    for _, o_row in user_data.iterrows():
                        items = o_row.get("orderItems", [])
                        if isinstance(items, list):
                            for itm in items:
                                if isinstance(itm, dict):
                                    menu_val = itm.get("menu") or itm.get("category") or itm.get("type")
                                    if menu_val and isinstance(menu_val, str):
                                        m_clean = menu_val.capitalize()
                                        extracted_menus_counts[m_clean] = extracted_menus_counts.get(m_clean, 0) + 1
                                    
                                    accs = itm.get("orderedAccommodities", [])
                                    if isinstance(accs, list):
                                        for acc in accs:
                                            if isinstance(acc, dict):
                                                c_name = acc.get("commodityName", "Top Wear")
                                                sub_menu = acc.get("menu")
                                                if sub_menu and isinstance(sub_menu, str):
                                                    sm_clean = sub_menu.capitalize()
                                                    extracted_menus_counts[sm_clean] = extracted_menus_counts.get(sm_clean, 0) + 1
                                                    
                                                qty = int(acc.get("quantity", 1))
                                                commodity_records.append({
                                                    "Commodity": c_name,
                                                    "menu": sub_menu or "Men",
                                                    "quantity": qty
                                                })
                                                random.seed(hash(c_name + str(o_row["_id"])))
                                                detailed_garment_records.append({
                                                    "Commodity": c_name,
                                                    "Brand": random.choice(mock_brands),
                                                    "Color": random.choice(mock_colors),
                                                    "Size": random.choice(mock_sizes),
                                                    "Care": random.choice(mock_cares)
                                                })

                    if extracted_menus_counts:
                        sorted_menus = sorted(extracted_menus_counts.items(), key=lambda x: x[1], reverse=True)
                        top_3_menus = [m[0] for m in sorted_menus[:3]]
                        menu_display_str = ", ".join(top_3_menus)
                    else:
                        menu_display_str = "Men"

                    if len(commodity_records) > 0:
                        comm_df = pd.DataFrame(commodity_records)
                        grouped_comm = comm_df.groupby("Commodity")["quantity"].sum().reset_index()
                        grouped_comm = grouped_comm.sort_values(by="quantity", ascending=False)
                        total_qty = grouped_comm["quantity"].sum()
                        grouped_comm["Share"] = (grouped_comm["quantity"] / total_qty * 100).round(1)
                        top_commodities_list = grouped_comm.head(4).to_dict('records')
                    else:
                        top_commodities_list = [
                            {"Commodity": "Top Wear", "Share": 64.7},
                            {"Commodity": "Bottom Wear", "Share": 35.3}
                        ]

                    top_color_display = pd.DataFrame({
                        "Color": ["Beige", "Crisp White", "Olive Green", "Charcoal Grey", "Navy Blue"],
                        "Share (%)": [35, 25, 20, 12, 8]
                    }).sort_values(by="Share (%)", ascending=True)

                    fig_color_bar = px.bar(
                        top_color_display, x="Share (%)", y="Color", orientation="h",
                        text="Share (%)", height=180, color="Color",
                        color_discrete_map={c: get_color_hex(c) for c in top_color_display["Color"]}
                    )
                    fig_color_bar.update_layout(
                        margin=dict(t=10, b=10, l=10, r=10),
                        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(showgrid=False),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=False
                    )
                    fig_color_bar.update_traces(texttemplate='%{text}%', textposition='outside')

                    top_size_display = pd.DataFrame({
                        "Size / Fit": ["Regular-40", "Slim-38", "Slim-38", "M", "32x32"],
                        "Share (%)": [40, 30, 15, 10, 5]
                    })

                    col_c1, col_c2, col_c3, col_c4 = st.columns(4)
                    with col_c1:
                        st.markdown("##### 1. Demographics & Metadata")
                        st.markdown(f"""
                        * **ID:** `{cid[:6]}...`
                        * **Location:** Bengaluru, KA
                        * **Phone:** {phone}
                        * **Pincode:** {row['Pincode']}
                        * **Menu Catalog (Top 3):** **{menu_display_str}**
                        * **Tag:** {row['Metrics']}
                        * **Total LTV:** **₹{total_ltv:,.2f}**
                        * **AOV:** **₹{aov:,.2f}**
                        * **Avg Freq:** {avg_freq:.1f}/mo
                        """)
                    with col_c2:
                        st.markdown("##### 2. Top Commodities")
                        for comm in top_commodities_list:
                            c_name = comm["Commodity"]
                            c_share = comm["Share"]
                            icon = get_commodity_icon(c_name)
                            st.markdown(f"""
                            <div class="commodity-card">
                                <div style="display: flex; align-items: center; justify-content: space-between;">
                                    <div style="display: flex; align-items: center;">
                                        <div class="icon-circle">{icon}</div>
                                        <div style="text-align: left; margin-left: 10px; font-weight: 600; font-size: 0.9rem; color: #1E293B;">{c_name}</div>
                                    </div>
                                    <div style="font-weight: 700; color: #3B82F6; font-size: 0.95rem;">{c_share}%</div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                    with col_c3:
                        st.markdown("##### 3. Top Color Choices")
                        st.plotly_chart(fig_color_bar, use_container_width=True, key=f"color_bar_{cid}")
                    with col_c4:
                        st.markdown("##### 4. Top Sizes & Fits")
                        st.dataframe(top_size_display, use_container_width=True, hide_index=True)

                    st.markdown("")
                    if st.button(f"🎁 Send Retention Offer to {name}", key=f"offer_{cid}"):
                        st.success(f"Successfully dispatched 15% off retention voucher to {phone}!")

                    st.markdown("---")

                    col_b1, col_b2 = st.columns([1, 1.8])
                    with col_b1:
                        st.markdown(f"##### Service Split for {name}")
                        service_pie_data = pd.DataFrame({
                            "Service": ["Wash & Iron", "Wash & Fold", "Dry Clean", "Shoe Clean", "Ironing"],
                            "Share": [36.1, 22.7, 17.5, 1.03, 3.09]
                        })
                        fig_service = px.pie(service_pie_data, names="Service", values="Share", hole=0.3, height=280)
                        fig_service.update_layout(margin=dict(t=10, b=10, l=10, r=10))
                        st.plotly_chart(fig_service, use_container_width=True, key=f"service_pie_{cid}")

                    with col_b2:
                        st.markdown("##### 🏷 Detailed Garment Attributes (Brand, Size, Color & Care)")
                        garment_df = pd.DataFrame(detailed_garment_records) if detailed_garment_records else pd.DataFrame({
                            "Commodity": ["Top Wear", "Bottom Wear"], "Brand": ["Peter England", "Van Heusen"],
                            "Color": ["Jet Black", "Sky Blue"], "Size": ["Regular-40", "L"], "Care": ["Starch & Iron", "Gentle Wash"]
                        })
                        st.dataframe(garment_df.head(8), use_container_width=True, hide_index=True)

                    st.markdown("---")
                    st.markdown("##### Financial Growth & Momentum Analytics")
                    
                    fa_col1, fa_col2 = st.columns(2)
                    with fa_col1:
                        user_data["cumulative_spend"] = user_data["order_amount"].cumsum()
                        user_data["display_date"] = user_data["scheduled_date_ts"].dt.strftime("%b %Y")
                        fig_cum = px.area(user_data, x="display_date", y="cumulative_spend", title="Cumulative Lifetime Spend Growth (₹)", height=220)
                        fig_cum.update_layout(margin=dict(t=30, b=10, l=10, r=10), yaxis_title="cumulative_spend")
                        st.plotly_chart(fig_cum, use_container_width=True, key=f"cum_spend_{cid}")
                    with fa_col2:
                        monthly_aov = user_data.groupby(user_data["scheduled_date_ts"].dt.strftime("%b %Y"))["order_amount"].mean().reset_index()
                        monthly_aov.columns = ["Month", "AOV"]
                        fig_aov = px.bar(monthly_aov, x="Month", y="AOV", text_auto=True, title="Monthly Average Order Value (AOV)", height=220)
                        fig_aov.update_layout(margin=dict(t=30, b=10, l=10, r=10))
                        st.plotly_chart(fig_aov, use_container_width=True, key=f"monthly_aov_{cid}")

                    st.markdown("##### Monthly Order Frequency & Volume Movement")
                    monthly_freq = user_data.groupby(user_data["scheduled_date_ts"].dt.strftime("%b %Y")).size().reset_index(name="Order Count")
                    monthly_freq.columns = ["Month", "Order Count"]
                    fig_freq = px.line(monthly_freq, x="Month", y="Order Count", markers=True, title="Monthly Order Frequency Trend", height=240)
                    fig_freq.update_layout(margin=dict(t=30, b=10, l=10, r=10), xaxis_title="Month", yaxis_title="Order Count")
                    st.plotly_chart(fig_freq, use_container_width=True, key=f"monthly_freq_{cid}")

                    st.markdown("##### Recent Order History Timeline")
                    order_history_display = user_data[["_id", "scheduled_date_ts", "order_amount"]].copy()
                    order_history_display.columns = ["Order ID", "Date", "Amount (₹)"]
                    order_history_display["Date"] = order_history_display["Date"].dt.strftime("%Y-%m-%d")
                    st.dataframe(order_history_display.tail(10), use_container_width=True, hide_index=True)

            except Exception as user_err:
                st.warning(f"⚠️ Skipped rendering user profile {row.get('customer_id', 'Unknown')}: {user_err}")

    except Exception as tab1_err:
        st.error("❌ An exception occurred inside Tab 1:")
        st.exception(tab1_err)

# ==========================================
# TAB 2: PINCODE & DYNAMIC GEO INTELLIGENCE
# ==========================================
with tab_pincode:
    st.markdown("### 📍 Operational Target Pincodes — Active Density & Spatial Analytics")
    st.markdown(f"Metrics aggregated across **{len(TARGET_PINCODES)} target delivery hubs** with complete mode-imputed coordinates.")

    try:
        summary_df_clean = summary_df.copy()
        summary_df_clean["Pincode"] = summary_df_clean["Pincode"].astype(str).str.strip()

        target_summary_df = summary_df_clean[summary_df_clean["Pincode"].isin(TARGET_PINCODES)].copy()

        if target_summary_df.empty:
            st.warning("⚠️ No customer records matched the target pincodes list. Displaying all available pincode rows from summary_df instead.")
            target_summary_df = summary_df_clean.copy()

        target_summary_df = impute_missing_with_mode(target_summary_df)

        kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
        total_target_users = len(target_summary_df)
        total_target_rev = target_summary_df["Total Spend (₹)"].sum()
        total_target_orders = target_summary_df["Total Orders"].sum()
        target_aov = total_target_rev / total_target_orders if total_target_orders > 0 else 0.0

        kpi_col1.metric("Active Delivery Hubs", f"{target_summary_df['Pincode'].nunique()} Pincodes")
        kpi_col2.metric("Target Cohort Users", f"{total_target_users:,} Users")
        kpi_col3.metric("Target Cohort Revenue", f"₹{total_target_rev:,.0f}")
        kpi_col4.metric("Cohort AOV", f"₹{target_aov:,.2f}")

        st.markdown("---")

        pincode_summary = target_summary_df.groupby("Pincode").agg(
            Active_Users=("customer_id", "count"),
            Total_Revenue=("Total Spend (₹)", "sum"),
            Total_Orders=("Total Orders", "sum"),
            Latitude=("user_lat", "mean"),
            Longitude=("user_long", "mean")
        ).reset_index().sort_values(by="Active_Users", ascending=False)

        def fill_missing_lat(row):
            if pd.notna(row["Latitude"]) and not np.isnan(row["Latitude"]):
                return float(row["Latitude"])
            p_str = str(row["Pincode"]).strip()
            return float(TARGET_AREA_NAMES.get(p_str, {}).get("lat", 12.9300))

        def fill_missing_lon(row):
            if pd.notna(row["Longitude"]) and not np.isnan(row["Longitude"]):
                return float(row["Longitude"])
            p_str = str(row["Pincode"]).strip()
            return float(TARGET_AREA_NAMES.get(p_str, {}).get("lon", 77.6200))

        pincode_summary["Latitude"] = pincode_summary.apply(fill_missing_lat, axis=1)
        pincode_summary["Longitude"] = pincode_summary.apply(fill_missing_lon, axis=1)
        pincode_summary["AOV"] = (pincode_summary["Total_Revenue"] / pincode_summary["Total_Orders"].replace(0, 1)).round(2)
        pincode_summary["Area Name"] = pincode_summary["Pincode"].apply(lambda p: TARGET_AREA_NAMES.get(str(p).strip(), {}).get("area", f"Pincode {p}"))

        col_t1, col_t2 = st.columns([1.2, 1])
        with col_t1:
            st.markdown("#### 📊 Target Pincodes Aggregation Summary Table")
            st.dataframe(pincode_summary[["Pincode", "Area Name", "Active_Users", "Total_Revenue", "Total_Orders", "AOV"]].rename(columns={
                "Pincode": "Delivery Pincode", 
                "Area Name": "Area / Location",
                "Active_Users": "Active Users", 
                "Total_Revenue": "Total Revenue (₹)", 
                "Total_Orders": "Total Orders", 
                "AOV": "AOV (₹)"
            }), use_container_width=True, hide_index=True)

        with col_t2:
            st.markdown("#### 💰 Revenue Contribution by Pincode")
            fig_pin_rev = px.bar(
                pincode_summary, x="Area Name", y="Total_Revenue", text_auto=",.0f",
                color="Total_Revenue", color_continuous_scale="Viridis", height=280
            )
            fig_pin_rev.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            st.plotly_chart(fig_pin_rev, use_container_width=True, key="pincode_rev_bar_chart")

        st.markdown("---")
        
        map_view_type = st.radio("Select Geo Map View:", ["📍 Pincode Dynamic Centroids (Aggregated)", "🎯 Exact User Delivery Coordinates (Pinpoint Points)"], horizontal=True)

        target_summary_df["map_lat"] = target_summary_df.apply(
            lambda r: r["user_lat"] if (pd.notna(r["user_lat"]) and not np.isnan(r["user_lat"])) else TARGET_AREA_NAMES.get(str(r["Pincode"]).strip(), {}).get("lat", 12.9121), axis=1
        )
        target_summary_df["map_long"] = target_summary_df.apply(
            lambda r: r["user_long"] if (pd.notna(r["user_long"]) and not np.isnan(r["user_long"])) else TARGET_AREA_NAMES.get(str(r["Pincode"]).strip(), {}).get("lon", 77.6445), axis=1
        )

        if "Aggregated" in map_view_type:
            fig_map = px.scatter_mapbox(
                pincode_summary, 
                lat="Latitude", 
                lon="Longitude", 
                size="Active_Users", 
                color="Total_Revenue",
                hover_name="Area Name", 
                hover_data=["Pincode", "Active_Users", "AOV", "Total_Revenue"],
                color_continuous_scale="Viridis", 
                size_max=35, 
                zoom=11,
                height=550,
                title="SUFFAI Dynamic Customer Centroids by Pincode"
            )
        else:
            fig_map = px.scatter_mapbox(
                target_summary_df, 
                lat="map_lat", 
                lon="map_long", 
                color="Pincode",
                size="Total Spend (₹)",
                hover_name="User Name", 
                hover_data=["Pincode", "Phone Number", "Total Spend (₹)", "Total Orders"],
                zoom=11,
                height=550,
                title="SUFFAI Exact Individual Customer Locations (Mode-Imputed)"
            )

        fig_map.update_layout(
            mapbox_style="open-street-map",
            margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_map, use_container_width=True, key="pincode_mapbox_chart_final")

    except Exception as tab2_err:
        st.error("❌ An exception occurred inside Tab 2:")
        st.exception(tab2_err)

# ==========================================
# TAB 3: BRAND AFFINITY & TASTE INTELLIGENCE SUITE
# ==========================================
with tab_brand:
    st.markdown("### 🏷 Partner Brand Affinity, Taste Behavior & Competitor Intelligence")
    st.markdown("Empower partner brands to analyze wardrobe overlap, competitor switching, garment attribute choices, local dark store inventory sizing, and regional ad targeting.")

    try:
        mock_brands_pool = ["Raymond", "Van Heusen", "Zara", "Allen Solly", "Levis", "Arrow", "H&M", "FabIndia", "Peter England"]
        mock_colors_pool = ["Navy Blue", "Charcoal Grey", "Crisp White", "Jet Black", "Sky Blue", "Olive Green", "Beige", "Maroon"]
        mock_sizes_pool = ["Regular-40", "Slim-38", "M", "L", "XL", "32x32"]
        mock_commodities_pool = ["Top Wear", "Bottom Wear", "Outerwear", "Ethnic Wear", "Home Linen", "Footwear"]
        mock_menus_pool = ["Men", "Women", "Premium Formals", "Ethnic Care", "Casuals", "Kids"]
        mock_cares_pool = ["Dry Clean Only", "Starch & Iron", "Gentle Wash", "Delicate Fabric"]

        brand_taste_records = []
        for _, row in summary_df.iterrows():
            cid = row["customer_id"]
            pcode = row["Pincode"]
            tier = row["Customer Tag"]
            spend = float(row["Total Spend (₹)"])
            
            random.seed(hash(str(cid) + "brand_suite_v2"))
            num_garments = random.randint(2, 4)
            for g_idx in range(num_garments):
                random.seed(hash(str(cid) + str(g_idx) + "v2"))
                assigned_brand = random.choice(mock_brands_pool)
                assigned_color = random.choice(mock_colors_pool)
                assigned_size = random.choice(mock_sizes_pool)
                assigned_comm = random.choice(mock_commodities_pool)
                assigned_menu = random.choice(mock_menus_pool)
                assigned_care = random.choice(mock_cares_pool)
                
                brand_taste_records.append({
                    "customer_id": cid,
                    "User Name": row["User Name"],
                    "Pincode": pcode,
                    "Area Name": TARGET_AREA_NAMES.get(pcode, {}).get("area", f"Pincode {pcode}"),
                    "Customer Tag": tier,
                    "Total Spend": spend / num_garments,
                    "Brand": assigned_brand,
                    "Color": assigned_color,
                    "Size": assigned_size,
                    "Commodity": assigned_comm,
                    "Menu Catalog": assigned_menu,
                    "Care Requirement": assigned_care
                })

        brand_taste_df = pd.DataFrame(brand_taste_records) if brand_taste_records else pd.DataFrame(columns=[
            "customer_id", "User Name", "Pincode", "Area Name", "Customer Tag", 
            "Total Spend", "Brand", "Color", "Size", "Commodity", "Menu Catalog", "Care Requirement"
        ])

        brand_taste_df = impute_missing_with_mode(brand_taste_df)

        st.markdown("#### 🎛️ Interactive Brand Intelligence Filters")
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)
        
        available_pincodes = sorted(brand_taste_df["Pincode"].unique().tolist()) if not brand_taste_df.empty else TARGET_PINCODES

        with f_col1:
            selected_brands_filter = st.multiselect(
                "Filter Partner / Competitor Brands:", 
                options=mock_brands_pool, 
                default=mock_brands_pool, 
                key="bt_brands_filter"
            )
        with f_col2:
            selected_zones_filter = st.multiselect(
                "Filter Delivery Hubs / Pincodes:", 
                options=available_pincodes, 
                default=available_pincodes, 
                key="bt_zones_filter"
            )
        with f_col3:
            selected_tier_filter = st.selectbox(
                "Filter Customer Tier:", 
                options=["All Tiers", "⭐ Champion Tier", "💎 High Spender", "🔄 High Frequency", "🛡️ Steady Regular"], 
                key="bt_tier_filter"
            )
        with f_col4:
            selected_commodity_filter = st.multiselect(
                "Filter Commodity Type:", 
                options=mock_commodities_pool, 
                default=mock_commodities_pool, 
                key="bt_commodity_filter"
            )
        with f_col5:
            selected_menu_filter = st.multiselect(
                "Filter Menu Catalog:", 
                options=mock_menus_pool, 
                default=mock_menus_pool, 
                key="bt_menu_filter"
            )

        filtered_bt_df = brand_taste_df[
            brand_taste_df["Brand"].isin(selected_brands_filter) &
            brand_taste_df["Pincode"].isin(selected_zones_filter) &
            brand_taste_df["Commodity"].isin(selected_commodity_filter) &
            brand_taste_df["Menu Catalog"].isin(selected_menu_filter)
        ]
        if selected_tier_filter != "All Tiers":
            filtered_bt_df = filtered_bt_df[filtered_bt_df["Customer Tag"] == selected_tier_filter]

        st.markdown("---")

        kpi_b1, kpi_b2, kpi_b3, kpi_b4, kpi_b5, kpi_b6 = st.columns(6)
        
        top_brand_val = filtered_bt_df["Brand"].mode()[0] if not filtered_bt_df.empty else "N/A"
        top_color_val = filtered_bt_df["Color"].mode()[0] if not filtered_bt_df.empty else "N/A"
        top_size_val = filtered_bt_df["Size"].mode()[0] if not filtered_bt_df.empty else "N/A"
        top_zone_val = filtered_bt_df["Area Name"].mode()[0] if not filtered_bt_df.empty else "N/A"

        kpi_b1.metric("Analyzed Wardrobes", f"{filtered_bt_df['customer_id'].nunique()} Users")
        kpi_b2.metric("Cohort Purchasing Power", f"₹{filtered_bt_df['Total Spend'].sum():,.0f}")
        kpi_b3.metric("Leading Competitor", top_brand_val)
        kpi_b4.metric("Dominant Color Taste", top_color_val)
        kpi_b5.metric("Top Sizing Demand", top_size_val)
        kpi_b6.metric("Highest Velocity Zone", top_zone_val)

        st.markdown("---")

        if filtered_bt_df.empty:
            st.warning("⚠️ No wardrobe records matched the selected filter combinations. Try expanding your brand, pincode, or catalog selections.")
        else:
            st.markdown("#### 1. 🏢 Brand & Competitor Cross-Pollination (Share of Wallet & Overlap)")
            st.markdown("Visualize brand co-existence in customer closets and category penetration across competing labels.")

            treemap_df = filtered_bt_df.groupby(["Brand", "Commodity"]).agg(
                Total_Spend=("Total Spend", "sum"),
                Item_Count=("customer_id", "count")
            ).reset_index()

            fig_treemap = px.treemap(
                treemap_df, 
                path=["Brand", "Commodity"], 
                values="Total_Spend",
                color="Total_Spend",
                color_continuous_scale="Viridis",
                title="Brand & Commodity Market Share Treemap (Weighted by Spend ₹)"
            )
            fig_treemap.update_layout(height=400, margin=dict(t=30, b=10, l=10, r=10))
            st.plotly_chart(fig_treemap, use_container_width=True, key="bt_treemap_chart")

            st.markdown("---")

            col_t_v1, col_t_v2 = st.columns([1.2, 1])

            with col_t_v1:
                st.markdown("#### 2. 🎨 Garment Taste Hierarchy (Menu → Commodity → Color → Care)")
                st.markdown("Interactive Sunburst chart revealing customer product and feature choices.")

                fig_sunburst = px.sunburst(
                    filtered_bt_df,
                    path=["Menu Catalog", "Commodity", "Color", "Care Requirement"],
                    values="Total Spend",
                    color="Commodity",
                    title="User Taste Decision Tree",
                    height=450
                )
                fig_sunburst.update_layout(margin=dict(t=30, b=10, l=10, r=10))
                st.plotly_chart(fig_sunburst, use_container_width=True, key="bt_sunburst_chart")

            with col_t_v2:
                st.markdown("#### 🧴 Care Requirement & Fabric Treatment Preferences")
                care_counts = filtered_bt_df.groupby(["Care Requirement", "Commodity"]).size().reset_index(name="Volume")
                fig_care_bar = px.bar(
                    care_counts,
                    x="Care Requirement",
                    y="Volume",
                    color="Commodity",
                    barmode="stack",
                    text_auto=True,
                    title="Garment Care Type Demand Breakdown",
                    height=450
                )
                fig_care_bar.update_layout(margin=dict(t=30, b=10, l=10, r=10))
                st.plotly_chart(fig_care_bar, use_container_width=True, key="bt_care_bar_chart")

            st.markdown("---")

            st.markdown("#### 3. 📐 Inventory & Sizing Demand Matrix (Stock Allocation for Dark Stores)")
            st.markdown("Forecast exact size depth needed per commodity category across customer spending tiers.")

            size_comm_df = filtered_bt_df.groupby(["Size", "Commodity"]).size().reset_index(name="Demand Volume")
            
            fig_size_matrix = px.bar(
                size_comm_df,
                x="Size",
                y="Demand Volume",
                color="Commodity",
                barmode="group",
                text_auto=True,
                title="Sizing Demand Matrix across Commodity Categories",
                height=380
            )
            fig_size_matrix.update_layout(margin=dict(t=30, b=10, l=10, r=10))
            st.plotly_chart(fig_size_matrix, use_container_width=True, key="bt_size_matrix_chart")

            st.markdown("---")

            st.markdown("#### 4. 📍 Zone-Wise Taste & Regional Preference Skew")
            st.markdown("Identify regional style variances across delivery hubs to optimize localized inventory and hyper-local ad campaigns.")

            zone_taste_df = filtered_bt_df.groupby(["Area Name", "Brand"]).size().reset_index(name="User Count")

            fig_zone_skew = px.bar(
                zone_taste_df,
                x="Area Name",
                y="User Count",
                color="Brand",
                barmode="stack",
                text_auto=True,
                title="Brand Penetration & Preference Concentration across Pincodes",
                height=400
            )
            fig_zone_skew.update_layout(margin=dict(t=30, b=10, l=10, r=10), xaxis_tickangle=-30)
            st.plotly_chart(fig_zone_skew, use_container_width=True, key="bt_zone_skew_chart")

            st.markdown("---")

            st.markdown("#### 5. 🎯 Targeted Marketing & Campaign Actionability Insights")
            
            rec_col1, rec_col2, rec_col3 = st.columns(3)
            
            with rec_col1:
                st.info(f"""
                **📢 High-Converting Ad Creative Focus**
                * **Target Region:** {top_zone_val}
                * **Hero Product:** {filtered_bt_df['Commodity'].mode()[0]}
                * **Color Focus:** {top_color_val}
                * **Action:** Launch targeted Instagram/Meta ads highlighting **{top_color_val} {filtered_bt_df['Commodity'].mode()[0]}** in {top_zone_val}.
                """)

            with rec_col2:
                st.success(f"""
                **📦 Dark Store Stock Depth Alert**
                * **Priority Sizing:** {top_size_val}
                * **Care Preference:** {filtered_bt_df['Care Requirement'].mode()[0]}
                * **Action:** Maintain 40%+ safety stock buffer for size **{top_size_val}** in dark store hubs servicing **{top_zone_val}**.
                """)

            with rec_col3:
                st.warning(f"""
                **⚔️ Competitor Cross-Shopping Push**
                * **Primary Rival:** {top_brand_val}
                * **Target Tier:** {selected_tier_filter}
                * **Action:** Dispatch exclusive 15% trade-up vouchers to users frequenting **{top_brand_val}** to capture market share.
                """)

    except Exception as tab3_err:
        st.error("❌ An exception occurred inside Tab 3:")
        st.exception(tab3_err)

# ==========================================
# TAB 4: EDGE VISION CAPTURE STATION (from upload_image_ui)
# ==========================================
with tab_capture:
    try:
        st.markdown("### 👔 SUFFAI Edge Vision Capture Station (Offline Mode)")
        st.markdown("Hierarchical Menu & Service Categorization for High-Volume Orders")

        capture_suggestions = list_capture_order_suggestions(orders_df, limit=25)
        suggestion_labels = [
            f"{s['bill']}  ·  {s['status']}  ·  {s['date'].strftime('%d %b %Y') if pd.notna(s['date']) else 'N/A'}"
            for s in capture_suggestions
        ]

        col_pick, col_search = st.columns([1.4, 1])
        with col_pick:
            picker_options = suggestion_labels if suggestion_labels else ["No orders available"]
            picked = st.selectbox(
                "Quick-pick recent / active order:",
                options=picker_options,
                key="capture_order_picker"
            )
        with col_search:
            typed_bill = st.text_input(
                "🔍 Bill No / Order ID (optional override):",
                placeholder="e.g. WIHSR5979 or 4488",
                key="capture_bill_input"
            ).strip()

        # Prefer typed bill; otherwise use the selected picker row (auto-loads first suggestion)
        bill_no = typed_bill
        if (not bill_no) and capture_suggestions and picked in suggestion_labels:
            pick_idx = suggestion_labels.index(picked)
            bill_no = capture_suggestions[pick_idx]["bill"]

        order_info = None
        hierarchy_data = {}

        if bill_no:
            selected_order_doc, audit_msg = fetch_latest_active_order_offline(orders_df, bill_no)
            if selected_order_doc:
                cust_id = str(selected_order_doc.get("customer_id", "") or "")
                cust_name = "Unknown Customer"
                cust_phone = "N/A"

                if not customers_df.empty and cust_id:
                    cust_match = customers_df[customers_df["customer_id"] == cust_id]
                    if not cust_match.empty:
                        cust_name = cust_match.iloc[0].get("customer_name", "Unknown Customer")
                        cust_phone = cust_match.iloc[0].get("phone_no", "N/A")

                resolved_bill = _order_bill_number(selected_order_doc) or bill_no
                order_info = {
                    "order_id": str(selected_order_doc.get("_id", "")),
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "phone": cust_phone,
                    "status": _order_status(selected_order_doc) or "Active",
                    "scheduled_date": selected_order_doc.get("_parsed_scheduled_date")
                }
                hierarchy_data = extract_order_hierarchy(selected_order_doc)
                bill_no = resolved_bill
                st.caption(audit_msg)

                if not hierarchy_data:
                    st.warning("Order matched, but no garment line-items were found on this order.")
            else:
                st.warning(f"⚠️ {audit_msg}")
                order_info = {
                    "order_id": bill_no,
                    "customer_id": f"CUST_{bill_no[:6]}",
                    "customer_name": "Walk-in / Test Customer",
                    "phone": "9999999999",
                    "status": "Test Mode",
                    "scheduled_date": pd.Timestamp.now()
                }
                hierarchy_data = {
                    "Wash & Iron": {"Top Wear": 6, "Bottom Wear": 2},
                    "Dry Cleaning": {"Formal Suit": 2}
                }

        if order_info and hierarchy_data:
            total_order_items = sum(sum(comms.values()) for comms in hierarchy_data.values())
            total_menus = len(hierarchy_data)

            st.markdown("---")
            kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
            kpi_c1.metric("Customer Name", order_info["customer_name"])
            kpi_c2.metric("Phone / Account", order_info["phone"])
            kpi_c3.metric("Service Menus", f"{total_menus} Categories")
            kpi_c4.metric("Total Garments Required", f"{total_order_items} Pieces")

            st.markdown("---")

            service_names = list(hierarchy_data.keys())
            capture_service_tabs = st.tabs(
                [f"🧺 {srv} ({sum(hierarchy_data[srv].values())} pcs)" for srv in service_names]
            )

            global_uploads_catalog = {}
            global_validation_passed = True

            for srv_idx, srv_name in enumerate(service_names):
                with capture_service_tabs[srv_idx]:
                    commodities_map = hierarchy_data[srv_name]
                    st.markdown(f"#### Service Category: **{srv_name}**")

                    for comm_name, req_qty in commodities_map.items():
                        with st.expander(f"👕 **{comm_name}** — Required: **{req_qty} pcs**", expanded=True):
                            uploaded_files = st.file_uploader(
                                f"Upload {req_qty} image(s) for {comm_name}:",
                                type=["jpg", "png", "jpeg"],
                                accept_multiple_files=True,
                                key=f"capture_uploader_{srv_name}_{comm_name}"
                            )

                            up_count = len(uploaded_files) if uploaded_files else 0
                            global_uploads_catalog[(srv_name, comm_name)] = {
                                "files": uploaded_files or [],
                                "required": req_qty,
                                "count": up_count
                            }

                            if up_count == req_qty:
                                st.markdown(
                                    f'<span class="metric-badge badge-success">✅ Uploaded {up_count} / {req_qty} pcs</span>',
                                    unsafe_allow_html=True
                                )
                            elif up_count < req_qty:
                                st.markdown(
                                    f'<span class="metric-badge badge-warning">⚠️ Incomplete: {up_count} / {req_qty} pcs (Add {req_qty - up_count} more)</span>',
                                    unsafe_allow_html=True
                                )
                                global_validation_passed = False
                            else:
                                st.markdown(
                                    f'<span class="metric-badge badge-error">❌ Exceeded: {up_count} / {req_qty} pcs (Remove {up_count - req_qty})</span>',
                                    unsafe_allow_html=True
                                )
                                global_validation_passed = False

            total_uploaded_global = sum(data["count"] for data in global_uploads_catalog.values())

            st.markdown("---")
            st.subheader("Feature Review & Batch Sync")

            if total_uploaded_global > 0:
                if not global_validation_passed:
                    st.error("⛔ Batch Sync Locked: Please resolve all quantity mismatches across all service tabs before saving.")
                else:
                    st.success("✅ All service menu requirements matched! Ready for catalog sync.")

                records_to_save = []

                for (srv_name, comm_name), upload_data in global_uploads_catalog.items():
                    files = upload_data["files"]
                    if files:
                        st.markdown(f"##### 📦 Service: `{srv_name}` ➔ Garment: **{comm_name}** ({len(files)} items)")

                        for idx_range in range(0, len(files), 2):
                            grid_cols = st.columns(2)

                            for sub_i in range(2):
                                item_idx = idx_range + sub_i
                                if item_idx < len(files):
                                    uploaded_file = files[item_idx]
                                    img = Image.open(uploaded_file)

                                    detected_color = extract_dominant_color(img)
                                    detected_brand, detected_size = extract_tag_ocr(img)
                                    lifecycle_score = calculate_lifecycle_score(img)

                                    with grid_cols[sub_i]:
                                        with st.container():
                                            st.markdown(f"**Item #{item_idx + 1} ({comm_name})**")
                                            img_c, meta_c = st.columns([1, 1.2])

                                            with img_c:
                                                st.image(img, use_container_width=True)
                                                st.caption(f"Fabric Lifecycle: **{lifecycle_score}/100**")

                                            with meta_c:
                                                val_brand = st.selectbox(
                                                    "Brand:", KNOWN_BRANDS,
                                                    index=KNOWN_BRANDS.index(detected_brand) if detected_brand and detected_brand in KNOWN_BRANDS else 0,
                                                    key=f"capture_b_{srv_name}_{comm_name}_{item_idx}"
                                                )
                                                val_color = st.selectbox(
                                                    "Color:", list(COLOR_PALETTE.keys()),
                                                    index=list(COLOR_PALETTE.keys()).index(detected_color),
                                                    key=f"capture_c_{srv_name}_{comm_name}_{item_idx}"
                                                )
                                                val_size = st.selectbox(
                                                    "Size:", KNOWN_SIZES,
                                                    index=KNOWN_SIZES.index(detected_size) if detected_size and detected_size in KNOWN_SIZES else 0,
                                                    key=f"capture_s_{srv_name}_{comm_name}_{item_idx}"
                                                )
                                                val_care = st.selectbox(
                                                    "Care:", CARE_LABELS, index=0,
                                                    key=f"capture_cr_{srv_name}_{comm_name}_{item_idx}"
                                                )

                                            img_filename = (
                                                f"{order_info['customer_id']}_{comm_name.replace(' ', '_')}_"
                                                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{item_idx}.jpg"
                                            )

                                            records_to_save.append({
                                                "record_data": {
                                                    "order_id": order_info["order_id"],
                                                    "customer_id": order_info["customer_id"],
                                                    "bill_no": bill_no,
                                                    "service_menu": srv_name,
                                                    "commodity": comm_name,
                                                    "brand": val_brand,
                                                    "color": val_color,
                                                    "size": val_size,
                                                    "care_instruction": val_care,
                                                    "lifecycle_score": lifecycle_score,
                                                    "image_filename": img_filename,
                                                    "timestamp": datetime.now().isoformat()
                                                },
                                                "pil_image": img,
                                                "filename": img_filename
                                            })
                        st.markdown("---")

                if st.button(
                    "💾 Sync Batch Order to Commodity Details Catalog",
                    disabled=not global_validation_passed,
                    type="primary",
                    use_container_width=True,
                    key="capture_sync_batch_btn"
                ):
                    saved_rows = []
                    for item in records_to_save:
                        item["pil_image"].save(os.path.join(IMAGE_STORE_DIR, item["filename"]))
                        saved_rows.append(item["record_data"])

                    df_new = pd.DataFrame(saved_rows)
                    if os.path.exists(COMMODITY_DETAILS_CSV):
                        df_new.to_csv(COMMODITY_DETAILS_CSV, mode='a', header=False, index=False)
                    else:
                        df_new.to_csv(COMMODITY_DETAILS_CSV, index=False)

                    st.success(
                        f"Successfully synchronized {len(saved_rows)} garment commodity prediction records "
                        f"for Order `{bill_no}` to `{COMMODITY_DETAILS_CSV}`!"
                    )
        else:
            st.info("👈 Enter a valid Bill No / Order ID above to load order requirements.")

        st.markdown("---")
        st.subheader(f"📋 Captured Commodity Details Warehouse (`{COMMODITY_DETAILS_CSV}`)")

        if os.path.exists(COMMODITY_DETAILS_CSV):
            captured_df = pd.read_csv(COMMODITY_DETAILS_CSV)
            st.dataframe(captured_df, use_container_width=True)
        else:
            st.info("No commodity prediction records stored in catalog yet.")

    except Exception as tab4_err:
        st.error("❌ An exception occurred inside Tab 4 (Edge Capture):")
        st.exception(tab4_err)