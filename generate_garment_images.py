#!/usr/bin/env python3
"""
Generate garment catalog images with Gemini on Vertex AI
and upload them to Google Cloud Storage.

Input columns:
customer_id, order_id, commodity_id, name, color, brand,
pattern, size, reason, sleeve_type, collar_type, gender, material
"""

import argparse
import io
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import types
from google.cloud import storage
from PIL import Image


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "suffai-dev")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
BUCKET_NAME = os.getenv(
    "GCS_BUCKET", "suffai-dev-suffai-garment-images"
)
MODEL_NAME = os.getenv(
    "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
)

REQUIRED_COLUMNS = [
    "customer_id", "order_id", "commodity_id", "name", "color",
    "brand", "pattern", "size", "reason", "sleeve_type",
    "collar_type", "gender", "material",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("garment_image_generation.log"),
    ],
)
log = logging.getLogger(__name__)


def clean(value):
    """Convert a dataframe value into safe, readable prompt text."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def safe_filename_part(value):
    """Prevent slashes and unsafe characters in object filenames."""
    value = clean(value)
    value = re.sub(r"[^A-Za-z0-9.-]+", "-", value)
    return value.strip(".-") or "unknown"


def make_filename(row):
    customer = safe_filename_part(row["customer_id"])
    order = safe_filename_part(row["order_id"])
    commodity = safe_filename_part(row["commodity_id"])
    return f"{customer}_{order}_{commodity}.jpg"


def make_prompt(row):
    """Build a garment-specific image prompt from the row."""
    attrs = {
        "garment": row["name"],
        "color": row["color"],
        "brand": row["brand"],
        "pattern": row["pattern"],
        "size": row["size"],
        "sleeve type": row["sleeve_type"],
        "collar type": row["collar_type"],
        "intended gender/category": row["gender"],
        "material": row["material"],
    }

    details = "\n".join(
        f"- {key}: {clean(value)}"
        for key, value in attrs.items()
        if clean(value)
    )

    return f"""
Generate one photorealistic e-commerce catalog photograph of a garment.

GARMENT ATTRIBUTES:
{details}

IMAGE REQUIREMENTS:
- Show one complete garment, clearly visible and centered.
- Use a clean, plain, light neutral background.
- Use professional, even studio lighting and realistic fabric texture.
- Show the garment's actual color, pattern, sleeves, collar and construction.
- Make the garment the primary subject, with no distracting objects.
- Do not add text, captions, watermarks, logos or graphic overlays.
- Do not invent visible brand logos or trademarks.
- Do not show multiple alternative garments.
- If a model is needed to display the garment, use a neutral,
  professional fashion-catalog pose; otherwise use a product-only
  display or invisible mannequin.
- Do not add accessories unless essential to displaying the garment.

Produce a realistic product photograph, not an illustration or collage.
""".strip()


def extract_image(response):
    """Return the first generated image as JPEG bytes."""
    for candidate in response.candidates or []:
        if not candidate.content:
            continue

        for part in candidate.content.parts or []:
            if part.inline_data and part.inline_data.data:
                image = Image.open(
                    io.BytesIO(part.inline_data.data)
                ).convert("RGB")

                output = io.BytesIO()
                image.save(output, format="JPEG", quality=92, optimize=True)
                return output.getvalue()

    raise RuntimeError(
        "The model returned no image. The response may have been "
        "blocked, or image generation may be unavailable."
    )


def generate_and_upload(client, bucket, row, retries=4):
    filename = make_filename(row)
    blob = bucket.blob(filename)

    # Resume safely: do not regenerate successfully uploaded images.
    if blob.exists():
        log.info("SKIP already exists: gs://%s/%s", BUCKET_NAME, filename)
        return "skipped"

    prompt = make_prompt(row)
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    candidate_count=1,
                ),
            )

            jpeg_bytes = extract_image(response)

            blob.upload_from_string(
                jpeg_bytes,
                content_type="image/jpeg",
                checksum="auto",
            )

            log.info(
                "SUCCESS gs://%s/%s (%s bytes)",
                BUCKET_NAME, filename, len(jpeg_bytes)
            )
            return "success"

        except Exception as exc:
            last_error = exc
            log.warning(
                "Attempt %s/%s failed for %s: %s",
                attempt, retries, filename, exc
            )
            if attempt < retries:
                time.sleep(min(2 ** attempt, 30))

    log.error("FAILED %s: %s", filename, last_error)
    return "failed"


def main():
    parser = argparse.ArgumentParser(
        description="Generate garment images and upload to GCS."
    )
    parser.add_argument(
        "--input", required=True,
        help="Input .csv or .parquet file"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Process only the first N rows; 0 means all rows"
    )
    parser.add_argument(
        "--start-row", type=int, default=0,
        help="Zero-based row offset, useful for batching"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to wait between rows"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if input_path.suffix.lower() == ".csv":
        df = pd.read_csv(input_path, dtype=str)
    elif input_path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(input_path)
    else:
        raise ValueError("Input must be a CSV or Parquet file.")

    df.columns = df.columns.str.strip().str.lower()

    missing = sorted(set(REQUIRED_COLUMNS) - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = df.iloc[args.start_row:]
    if args.limit > 0:
        df = df.head(args.limit)

    if df.empty:
        raise ValueError("No rows to process.")

    log.info("Project: %s | Location: %s", PROJECT_ID, LOCATION)
    log.info("Model: %s | Bucket: gs://%s", MODEL_NAME, BUCKET_NAME)
    log.info("Rows selected: %s", len(df))

    # Uses Cloud Shell Application Default Credentials.
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
    )
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    if not bucket.exists():
        raise RuntimeError(
            f"Bucket gs://{BUCKET_NAME} does not exist or is inaccessible."
        )

    counts = {"success": 0, "skipped": 0, "failed": 0}

    for position, (_, row) in enumerate(df.iterrows(), start=1):
        log.info("Processing row %s/%s", position, len(df))

        result = generate_and_upload(client, bucket, row)
        counts[result] += 1

        # Persist a summary after each row so a long run is auditable.
        pd.DataFrame([counts]).to_csv(
            "garment_image_generation_summary.csv", index=False
        )

        if args.delay > 0 and position < len(df):
            time.sleep(args.delay)

    log.info("Finished: %s", counts)
    log.info("Summary: garment_image_generation_summary.csv")


if __name__ == "__main__":
    main()
