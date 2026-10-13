"use client";

import { FormEvent, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { API_BASE, apiPost } from "@/lib/api";

export default function CapturePage() {
  const [customerId, setCustomerId] = useState("");
  const [orderId, setOrderId] = useState("");
  const [orderItemId, setOrderItemId] = useState("item-1");
  const [lineId, setLineId] = useState("line-1");
  const [pin, setPin] = useState("560103");
  const [index, setIndex] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [brandHint, setBrandHint] = useState("Peter England");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      const consent = await apiPost<{ consent_id: string }>("/v1/garments/consent", {
        source_customer_id: customerId,
        purpose: "aggregated_b2b_analytics",
      });
      if (consent.errors?.length) {
        setStatus(consent.errors[0].message);
        setBusy(false);
        return;
      }

      const init = await apiPost<{
        garment_instance: { garment_instance_id: string };
        image: { image_id: string; gcs_object_key: string };
        upload: { mode: string; upload_url: string; gcs_object_key: string };
        extraction_job: { job_id: string };
      }>("/v1/garments/images:initiate", {
        source_customer_id: customerId,
        source_order_id: orderId,
        source_order_item_id: orderItemId,
        source_order_commodity_line_id: lineId,
        physical_item_index: index,
        consent_id: consent.data.consent_id,
        order_pin_code: pin,
      });
      if (init.errors?.length) {
        setStatus(init.errors[0].message);
        setBusy(false);
        return;
      }

      const token = localStorage.getItem("suffai_token");
      if (file && init.data.upload.mode === "local") {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("gcs_object_key", init.data.upload.gcs_object_key);
        fd.append("image_id", init.data.image.image_id);
        await fetch(`${API_BASE}/v1/garments/images/upload-local`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: fd,
        });
      }

      const ext = await apiPost<{ job: { job_id: string; status: string } }>("/v1/garments/extractions", {
        image_id: init.data.image.image_id,
        garment_instance_id: init.data.garment_instance.garment_instance_id,
        gcs_object_key: init.data.image.gcs_object_key,
      });

      // Seed attributes for review demo (ops can edit on Review page)
      await fetch(`${API_BASE}/v1/garments/${init.data.garment_instance.garment_instance_id}/attributes`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          attributes: {
            brand_name_raw: brandHint || "unknown",
            commodity_type: "shirt",
            material_type: "cotton",
            primary_color: "navy",
            size_normalized: "M",
          },
          review_status: "needs_review",
        }),
      });

      setStatus(
        `Captured garment ${init.data.garment_instance.garment_instance_id.slice(0, 8)}… · ` +
        `job ${ext.data?.job?.status || "queued"}. Open Review to mark completed.`,
      );
    } catch {
      setStatus("Capture failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Capture garment</h1>
          <p className="sub">
            IMG-01/02: consent → register physical item → upload photo → extraction job → review.
            Quantity lines alone never invent attributes.
          </p>
        </div>
      </div>
      <form className="panel" onSubmit={onSubmit}>
        <div className="split-2">
          <div className="field">
            <label>Customer id (customers._id)</label>
            <input value={customerId} onChange={(e) => setCustomerId(e.target.value)} required />
          </div>
          <div className="field">
            <label>Order id</label>
            <input value={orderId} onChange={(e) => setOrderId(e.target.value)} required />
          </div>
          <div className="field">
            <label>Order item id</label>
            <input value={orderItemId} onChange={(e) => setOrderItemId(e.target.value)} required />
          </div>
          <div className="field">
            <label>Commodity line id</label>
            <input value={lineId} onChange={(e) => setLineId(e.target.value)} required />
          </div>
          <div className="field">
            <label>Order PIN</label>
            <input value={pin} onChange={(e) => setPin(e.target.value)} />
          </div>
          <div className="field">
            <label>Physical item index</label>
            <input type="number" min={1} value={index} onChange={(e) => setIndex(Number(e.target.value))} />
          </div>
          <div className="field">
            <label>Brand hint (for review)</label>
            <input value={brandHint} onChange={(e) => setBrandHint(e.target.value)} />
          </div>
          <div className="field">
            <label>Photo</label>
            <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </div>
        </div>
        <button className="btn" type="submit" disabled={busy}>{busy ? "Saving…" : "Capture & queue extraction"}</button>
        {status && <div className="alert info">{status}</div>}
      </form>
    </RequireAuth>
  );
}
