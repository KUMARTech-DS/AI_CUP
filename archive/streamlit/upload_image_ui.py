import streamlit as st
import pandas as pd
import numpy as np
from PIL import Image
from datetime import datetime
import os
import json
import math

# Graceful fallback imports for local computer vision libraries
try:
    import cv2
except ImportError:
    cv2 = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

# ==========================================
# 1. PAGE CONFIG & ENTERPRISE STYLING
# ==========================================
st.set_page_config(
    page_title="SUFFAI Enterprise Edge Capture Station",
    page_icon="👔",
    layout="wide"
)

st.markdown("""
    <style>
    .main-header { font-size: 1.6rem; font-weight: 700; color: #1E3A8A; margin-bottom: 0.1rem; }
    .sub-text { color: #64748B; font-size: 0.9rem; margin-bottom: 1.2rem; }
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

# New CSV Schema File Path
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

# ==========================================
# 2. LOCAL DATA LOADING & ORDER HIERARCHY
# ==========================================
def extract_id(val):
    if isinstance(val, dict) and "$oid" in val:
        return str(val["$oid"])
    if pd.notna(val):
        return str(val).strip()
    return ""

@st.cache_data(ttl=300)
def load_offline_datasets():
    orders_path = os.path.join("data_offline", "orders.json")
    customers_path = os.path.join("data_offline", "customers.json")
    
    orders_df = pd.DataFrame()
    customers_df = pd.DataFrame()
    
    if os.path.exists(orders_path):
        try:
            with open(orders_path, "r", encoding="utf-8") as f:
                orders_data = json.load(f)
            orders_df = pd.DataFrame(orders_data)
            if "_id" in orders_df.columns:
                orders_df["_id"] = orders_df["_id"].apply(extract_id)
            if "customer_id" in orders_df.columns:
                orders_df["customer_id"] = orders_df["customer_id"].apply(extract_id)
        except Exception as e:
            st.error(f"Error loading offline orders dataset: {e}")

    if os.path.exists(customers_path):
        try:
            with open(customers_path, "r", encoding="utf-8") as f:
                cust_data = json.load(f)
            customers_df = pd.DataFrame(cust_data)
            if "_id" in customers_df.columns:
                customers_df["customer_id"] = customers_df["_id"].apply(extract_id)
        except Exception as e:
            st.error(f"Error loading offline customers dataset: {e}")

    return orders_df, customers_df

orders_df, customers_df = load_offline_datasets()

def fetch_latest_active_order_offline(orders_df, query_bill_no):
    if orders_df.empty or not query_bill_no:
        return None, "Offline dataset uninitialized or empty."

    q_str = str(query_bill_no).strip().lower()
    
    def matches_query(row):
        b_no = str(row.get("bill_no", "")).strip().lower()
        o_id = str(row.get("_id", "")).strip().lower()
        return q_str in b_no or q_str in o_id

    matched_mask = orders_df.apply(matches_query, axis=1)
    candidates_df = orders_df[matched_mask].copy()

    if candidates_df.empty:
        return None, f"No orders matching `{query_bill_no}` in offline database."

    active_candidates = []
    EXCLUDED_KEYWORDS = ["cancel", "delivered", "delivered_to_customer", "delivered to customer"]

    for idx, row in candidates_df.iterrows():
        doc = row.to_dict()
        status_str = str(doc.get("status", "")).strip().lower()
        if any(kw in status_str for kw in EXCLUDED_KEYWORDS):
            continue

        raw_date = doc.get("scheduled_date") or doc.get("created_at")
        if isinstance(raw_date, dict):
            if "$date" in raw_date:
                d_val = raw_date["$date"]
                if isinstance(d_val, dict) and "$numberLong" in d_val:
                    raw_date = int(d_val["$numberLong"])
                else:
                    raw_date = d_val

        parsed_dt = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed_dt):
            parsed_dt = pd.Timestamp.min

        doc["_parsed_scheduled_date"] = parsed_dt
        active_candidates.append(doc)

    if not active_candidates:
        return None, f"Found {len(candidates_df)} entry(ies) for `{query_bill_no}`, but all are Cancelled or Delivered."

    active_candidates.sort(key=lambda x: x["_parsed_scheduled_date"], reverse=True)
    selected_order = active_candidates[0]
    audit_msg = f"Order found ({len(candidates_df)} history records). Scheduled: {selected_order['_parsed_scheduled_date'].strftime('%d %b %Y')} | Status: `{selected_order.get('status', 'Active')}`"
    
    return selected_order, audit_msg

def extract_order_hierarchy(order_doc):
    """
    Extracts hierarchical structure: Service/Menu -> Commodity Name -> Required Quantity
    Handles orders with up to 100+ items gracefully.
    """
    hierarchy = {}
    items = order_doc.get("orderItems", [])
    
    if isinstance(items, list):
        for itm in items:
            if not isinstance(itm, dict):
                continue
            
            menu_name = itm.get("menu") or itm.get("service") or itm.get("category") or "Wash & Iron"
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
                qty = int(itm.get("quantity") or itm.get("qty") or 1)
                hierarchy[menu_clean][c_clean] = hierarchy[menu_clean].get(c_clean, 0) + qty

    return hierarchy

# ==========================================
# 3. PERCEPTUAL CIELAB VISION HELPERS
# ==========================================
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
# 4. STREAMLIT ENTERPRISE DASHBOARD
# ==========================================
st.markdown('<p class="main-header">👔 SUFFAI Edge Vision Capture Station (Offline Mode)</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-text">Hierarchical Menu & Service Categorization for High-Volume Orders</p>', unsafe_allow_html=True)

# 1. Order Lookup Bar
with st.container():
    col_search, col_info = st.columns([1, 2])
    with col_search:
        bill_no = st.text_input("🔍 Bill No / Order ID:", placeholder="Enter Bill No (e.g. 49045)...", key="bill_input").strip()

order_info = None
hierarchy_data = {}

if bill_no:
    selected_order_doc, audit_msg = fetch_latest_active_order_offline(orders_df, bill_no)
    if selected_order_doc:
        cust_id = str(selected_order_doc.get("customer_id", ""))
        cust_name = "Unknown Customer"
        cust_phone = "N/A"
        
        if not customers_df.empty and cust_id:
            cust_match = customers_df[customers_df["customer_id"] == cust_id]
            if not cust_match.empty:
                cust_name = cust_match.iloc[0].get("customer_name", "Unknown Customer")
                cust_phone = cust_match.iloc[0].get("phone_no", "N/A")
        
        order_info = {
            "order_id": str(selected_order_doc.get("_id", "")),
            "customer_id": cust_id,
            "customer_name": cust_name,
            "phone": cust_phone,
            "status": selected_order_doc.get("status", "Active"),
            "scheduled_date": selected_order_doc.get("_parsed_scheduled_date")
        }
        hierarchy_data = extract_order_hierarchy(selected_order_doc)
    else:
        st.warning(f"⚠️ {audit_msg}")
        # Simulation Fallback
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
    
    # 2. Executive Order Metadata Banner
    st.markdown("---")
    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
    kpi_c1.metric("Customer Name", order_info["customer_name"])
    kpi_c2.metric("Phone / Account", order_info["phone"])
    kpi_c3.metric("Service Menus", f"{total_menus} Categories")
    kpi_c4.metric("Total Garments Required", f"{total_order_items} Pieces")

    st.markdown("---")

    # 3. Hierarchical Service / Menu Tabs
    service_names = list(hierarchy_data.keys())
    tab_list = st.tabs([f"🧺 {srv} ({sum(hierarchy_data[srv].values())} pcs)" for srv in service_names])
    
    global_uploads_catalog = {}
    global_validation_passed = True

    for srv_idx, srv_name in enumerate(service_names):
        with tab_list[srv_idx]:
            commodities_map = hierarchy_data[srv_name]
            st.markdown(f"#### Service Category: **{srv_name}**")
            
            for comm_name, req_qty in commodities_map.items():
                with st.expander(f"👕 **{comm_name}** — Required: **{req_qty} pcs**", expanded=True):
                    
                    uploaded_files = st.file_uploader(
                        f"Upload {req_qty} image(s) for {comm_name}:",
                        type=["jpg", "png", "jpeg"],
                        accept_multiple_files=True,
                        key=f"uploader_{srv_name}_{comm_name}"
                    )
                    
                    up_count = len(uploaded_files) if uploaded_files else 0
                    global_uploads_catalog[(srv_name, comm_name)] = {
                        "files": uploaded_files or [],
                        "required": req_qty,
                        "count": up_count
                    }
                    
                    if up_count == req_qty:
                        st.markdown(f'<span class="metric-badge badge-success">✅ Uploaded {up_count} / {req_qty} pcs</span>', unsafe_allow_html=True)
                    elif up_count < req_qty:
                        st.markdown(f'<span class="metric-badge badge-warning">⚠️ Incomplete: {up_count} / {req_qty} pcs (Add {req_qty - up_count} more)</span>', unsafe_allow_html=True)
                        global_validation_passed = False
                    else:
                        st.markdown(f'<span class="metric-badge badge-error">❌ Exceeded: {up_count} / {req_qty} pcs (Remove {up_count - req_qty})</span>', unsafe_allow_html=True)
                        global_validation_passed = False

    # 4. Feature Review & Batch Verification Section
    total_uploaded_global = sum(data["count"] for data in global_uploads_catalog.values())
    
    st.markdown("---")
    st.subheader("4. Feature Review & Batch Sync")

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
                                            key=f"b_{srv_name}_{comm_name}_{item_idx}"
                                        )
                                        val_color = st.selectbox(
                                            "Color:", list(COLOR_PALETTE.keys()),
                                            index=list(COLOR_PALETTE.keys()).index(detected_color),
                                            key=f"c_{srv_name}_{comm_name}_{item_idx}"
                                        )
                                        val_size = st.selectbox(
                                            "Size:", KNOWN_SIZES,
                                            index=KNOWN_SIZES.index(detected_size) if detected_size and detected_size in KNOWN_SIZES else 0,
                                            key=f"s_{srv_name}_{comm_name}_{item_idx}"
                                        )
                                        val_care = st.selectbox(
                                            "Care:", CARE_LABELS, index=0,
                                            key=f"cr_{srv_name}_{comm_name}_{item_idx}"
                                        )
                                        
                                    img_filename = f"{order_info['customer_id']}_{comm_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{item_idx}.jpg"
                                    
                                    # Updated Schema: Commodity Details Output Format
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

        # Global Sync Action
        if st.button("💾 Sync Batch Order to Commodity Details Catalog", disabled=not global_validation_passed, type="primary", use_container_width=True):
            saved_rows = []
            for item in records_to_save:
                item["pil_image"].save(os.path.join(IMAGE_STORE_DIR, item["filename"]))
                saved_rows.append(item["record_data"])
            
            df_new = pd.DataFrame(saved_rows)
            if os.path.exists(COMMODITY_DETAILS_CSV):
                df_new.to_csv(COMMODITY_DETAILS_CSV, mode='a', header=False, index=False)
            else:
                df_new.to_csv(COMMODITY_DETAILS_CSV, index=False)
                
            st.success(f"Successfully synchronized {len(saved_rows)} garment commodity prediction records for Order `{bill_no}` to `{COMMODITY_DETAILS_CSV}`!")

else:
    st.info("👈 Enter a valid Bill No / Order ID above to load order requirements.")

# ==========================================
# 5. LIVE DATASET WAREHOUSE
# ==========================================
st.markdown("---")
st.subheader(f"📋 Captured Commodity Details Warehouse (`{COMMODITY_DETAILS_CSV}`)")

if os.path.exists(COMMODITY_DETAILS_CSV):
    captured_df = pd.read_csv(COMMODITY_DETAILS_CSV)
    st.dataframe(captured_df, use_container_width=True)
else:
    st.info("No commodity prediction records stored in catalog yet.")