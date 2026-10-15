import { FormEvent, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { API_BASE, apiPost } from "@/lib/api";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];

type ApiError = { code: string; message: string };

function firstError(errors?: ApiError[]) {
  return errors?.[0]?.message;
}

async function responseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return (
      body?.errors?.[0]?.message ||
      body?.detail ||
      `Request failed (${res.status})`
    );
  } catch {
    return `Request failed (${res.status})`;
  }
}

export default function CapturePage() {
  const [customerId, setCustomerId] = useState("");
  const [orderId, setOrderId] = useState("");
  const [orderItemId, setOrderItemId] = useState("item-1");
  const [lineId, setLineId] = useState("line-1");
  const [pin, setPin] = useState("560103");
  const [index, setIndex] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [brandHint, setBrandHint] = useState("Peter England");
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<"success" | "error">("success");
  const [busy, setBusy] = useState(false);

  function showError(message: string) {
    setStatusType("error");
    setStatus(message);
  }

  function selectFile(selected: File | null) {
    setFile(selected);
    setStatus(null);

    if (!selected) return;

    if (!ALLOWED_TYPES.includes(selected.type)) {
      setFile(null);
      showError("Choose a JPEG, PNG, or WebP image.");
      return;
    }

    if (selected.size <= 0 || selected.size > MAX_IMAGE_BYTES) {
      setFile(null);
      showError("The image must be larger than 0 bytes and no larger than 10 MB.");
    }
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setStatus(null);
    setBusy(true);

    try {
      if (!file) {
        throw new Error("Please select a garment photo before continuing.");
      }

      if (!consentConfirmed) {
        throw new Error("Please confirm the consent checkbox before continuing.");
      }

      if (!ALLOWED_TYPES.includes(file.type) || file.size > MAX_IMAGE_BYTES) {
        throw new Error("Choose a JPEG, PNG, or WebP image no larger than 10 MB.");
      }

      const consent = await apiPost<{ consent_id: string }>(
        "/v1/garments/consent",
        {
          source_customer_id: customerId.trim(),
          purpose: "aggregated_b2b_analytics",
        },
      );

      const consentError = firstError(consent.errors);
      if (consentError) throw new Error(consentError);

      if (!consent.data?.consent_id) {
        throw new Error("The API did not return a consent ID.");
      }

      const init = await apiPost<{
        garment_instance: { garment_instance_id: string };
        image: { image_id: string; gcs_object_key: string };
        upload: {
          mode: "gcs" | "local";
          upload_url: string;
          method: string;
          gcs_object_key: string;
          headers?: Record<string, string>;
        };
        extraction_job: { job_id: string };
      }>("/v1/garments/images:initiate", {
        source_customer_id: customerId.trim(),
        source_order_id: orderId.trim(),
        source_order_item_id: orderItemId.trim(),
        source_order_commodity_line_id: lineId.trim(),
        physical_item_index: index,
        consent_id: consent.data.consent_id,
        order_pin_code: pin.trim() || null,
        content_type: file.type,
        capture_source: "ops_upload",
      });

      const initError = firstError(init.errors);
      if (initError) throw new Error(initError);

      const data = init.data;
      if (!data?.image?.image_id || !data?.upload?.upload_url) {
        throw new Error("The API returned incomplete upload details.");
      }

      setStatusType("success");
      setStatus("Step 1/3: Garment registered. Uploading photo…");

      const token = localStorage.getItem("suffai_token");
      let uploadResponse: Response;

      if (data.upload.mode === "local") {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("gcs_object_key", data.upload.gcs_object_key);
        formData.append("image_id", data.image.image_id);

        uploadResponse = await fetch(
          `${API_BASE}/v1/garments/images/upload-local`,
          {
            method: "POST",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: formData,
          },
        );
      } else {
        uploadResponse = await fetch(data.upload.upload_url, {
          method: "PUT",
          headers: {
            ...(data.upload.headers || {}),
            "Content-Type": file.type,
          },
          body: file,
        });
      }

      if (!uploadResponse.ok) {
        if (data.upload.mode === "gcs") {
          throw new Error(
            `Photo upload failed (${uploadResponse.status}). Check the GCS bucket CORS configuration and signed-URL permissions, then retry.`,
          );
        }
        throw new Error(await responseError(uploadResponse));
      }

      if (data.upload.mode === "local") {
        const uploadResult = await uploadResponse.json();
        const uploadError = firstError(uploadResult.errors);
        if (uploadError) throw new Error(uploadError);
      }

      setStatus("Step 2/3: Photo uploaded. Starting extraction…");

      const ext = await apiPost<{
        job: { job_id: string; status: string };
      }>("/v1/garments/extractions", {
        image_id: data.image.image_id,
        garment_instance_id: data.garment_instance.garment_instance_id,
        gcs_object_key: data.image.gcs_object_key,
      });

      const extractionError = firstError(ext.errors);
      if (extractionError) throw new Error(extractionError);

      if (!ext.data?.job) {
        throw new Error("The API did not return an extraction job.");
      }

      // Keep the existing demo attributes for the Review page.
      const patchResponse = await fetch(
        `${API_BASE}/v1/garments/${data.garment_instance.garment_instance_id}/attributes`,
        {
          method: "PATCH",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            attributes: {
              brand_name_raw: brandHint.trim() || "unknown",
              commodity_type: "shirt",
              material_type: "cotton",
              primary_color: "navy",
              size_normalized: "M",
            },
            review_status: "needs_review",
          }),
        },
      );

      if (!patchResponse.ok) {
        throw new Error(await responseError(patchResponse));
      }

      const patchResult = await patchResponse.json();
      const patchError = firstError(patchResult.errors);
      if (patchError) throw new Error(patchError);

      setStatusType("success");
      setStatus(
        `Photo uploaded successfully. Garment ${data.garment_instance.garment_instance_id.slice(0, 8)}… · ` +
          `extraction ${ext.data.job.status}. Open Review to inspect the garment.`,
      );
      setFile(null);

      const fileInput = document.getElementById(
        "garment-photo",
      ) as HTMLInputElement | null;
      if (fileInput) fileInput.value = "";
    } catch (error) {
      showError(
        error instanceof Error
          ? error.message
          : "Capture failed. Please try again.",
      );
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
            Register the physical item, upload its photo, queue extraction,
            and review the resulting attributes.
          </p>
        </div>
      </div>

      <form className="panel" onSubmit={onSubmit}>
        <div className="split-2">
          <div className="field">
            <label htmlFor="customer-id">Customer ID (customers._id)</label>
            <input
              id="customer-id"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="order-id">Order ID</label>
            <input
              id="order-id"
              value={orderId}
              onChange={(e) => setOrderId(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="order-item-id">Order item ID</label>
            <input
              id="order-item-id"
              value={orderItemId}
              onChange={(e) => setOrderItemId(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="line-id">Commodity line ID</label>
            <input
              id="line-id"
              value={lineId}
              onChange={(e) => setLineId(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="order-pin">Order PIN</label>
            <input
              id="order-pin"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="physical-index">Physical item index</label>
            <input
              id="physical-index"
              type="number"
              min={1}
              value={index}
              onChange={(e) => setIndex(Number(e.target.value))}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="brand-hint">Brand hint (for review)</label>
            <input
              id="brand-hint"
              value={brandHint}
              onChange={(e) => setBrandHint(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="garment-photo">Garment photo (required)</label>
            <input
              id="garment-photo"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => selectFile(e.target.files?.[0] || null)}
              required
            />
            <small>JPEG, PNG, or WebP · maximum 10 MB</small>
            {file && (
              <p className="sub">
                Selected: {file.name} · {(file.size / 1024 / 1024).toFixed(2)} MB
              </p>
            )}
          </div>
        </div>

        <div className="field">
          <label>
            <input
              type="checkbox"
              checked={consentConfirmed}
              onChange={(e) => setConsentConfirmed(e.target.checked)}
              required
            />{" "}
            I confirm that the required customer consent has been obtained for
            this image and its stated processing purpose.
          </label>
        </div>

        <button className="btn" type="submit" disabled={busy}>
          {busy ? "Uploading and processing…" : "Upload photo & queue extraction"}
        </button>

        {status && (
          <div
            className={`alert ${statusType === "error" ? "error" : "info"}`}
            role="status"
            aria-live="polite"
          >
            {status}
          </div>
        )}
      </form>
    </RequireAuth>
  );
}

