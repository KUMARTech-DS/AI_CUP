"""SKU image validation + comparable visual attributes for the ranking API.

Images stay in memory: Vertex receives inline bytes (same prompt/normalisation as the
extraction worker), so nothing is written to GCS. Without Vertex (local / tests) a
deterministic heuristic reads the dominant colour with Pillow and garment hints from the
file name, flagged as `local_heuristic` with low confidence.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config import settings

log = logging.getLogger("suffai.sku_vision")

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = int(os.getenv("SKU_RANK_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("SKU_RANK_MAX_TOTAL_BYTES", str(30 * 1024 * 1024)))
MAX_PIXELS = 40_000_000
LOCAL_MODEL = "local-heuristic-v1"


class ImageRejected(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class UpstreamUnavailable(Exception):
    pass


@dataclass
class SkuImage:
    input_index: int
    filename: str
    content_type: str
    data: bytes = field(repr=False)
    sha256: str = ""
    width: int = 0
    height: int = 0

    @property
    def image_id(self) -> str:
        return f"img_{self.sha256[:16]}"


def sniff_type(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_filename(name: Optional[str]) -> str:
    base = os.path.basename(str(name or "")).strip()
    base = re.sub(r"[^\w.\- ]+", "_", base)[:120]
    return base or "image"


def validate_image(input_index: int, filename: Optional[str], declared_type: Optional[str], data: bytes) -> SkuImage:
    """415 unsupported type, 413 too large, 422 empty / undecodable."""
    from PIL import Image

    declared = (declared_type or "").split(";")[0].strip().lower()
    if not data:
        raise ImageRejected(422, "UNPROCESSABLE_IMAGE", f"Image {input_index} is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected(413, "PAYLOAD_TOO_LARGE", f"Image {input_index} exceeds {MAX_IMAGE_BYTES} bytes")
    sniffed = sniff_type(data)
    if sniffed is None:
        if declared in ALLOWED_TYPES:
            raise ImageRejected(422, "UNPROCESSABLE_IMAGE", f"Image {input_index} is not a decodable {declared}")
        raise ImageRejected(415, "UNSUPPORTED_MEDIA_TYPE", f"Image {input_index}: use JPEG, PNG or WebP")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise ImageRejected(413, "PAYLOAD_TOO_LARGE", f"Image {input_index} exceeds {MAX_PIXELS} pixels")
            img.load()
            width, height = img.size
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected(422, "UNPROCESSABLE_IMAGE", f"Image {input_index} could not be decoded") from None
    return SkuImage(
        input_index=input_index,
        filename=safe_filename(filename),
        content_type=sniffed,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
    )


PALETTE = {
    "black": (25, 25, 28),
    "charcoal": (55, 58, 64),
    "grey": (128, 128, 132),
    "white": (240, 240, 238),
    "cream": (238, 226, 200),
    "beige": (212, 192, 155),
    "khaki": (182, 160, 114),
    "brown": (107, 74, 50),
    "navy": (29, 42, 77),
    "blue": (47, 95, 179),
    "light blue": (143, 182, 221),
    "teal": (31, 111, 115),
    "green": (47, 122, 74),
    "olive": (85, 96, 59),
    "yellow": (226, 185, 59),
    "mustard": (199, 154, 42),
    "orange": (217, 118, 43),
    "red": (179, 38, 46),
    "maroon": (92, 26, 36),
    "pink": (224, 143, 176),
    "purple": (94, 52, 130),
    "lavender": (184, 164, 216),
}

COMMODITY_HINTS = (
    ("tshirt", ("tshirt", "t-shirt", "tee", "tees", "polo", "tank")),
    ("hoodie", ("hoodie", "hoody", "sweatshirt", "pullover")),
    ("jacket", ("jacket", "blazer", "coat", "bomber", "parka")),
    ("jeans", ("jeans", "jean", "denim")),
    ("trouser", ("trouser", "trousers", "pant", "pants", "chino", "chinos", "jogger", "joggers", "cargo", "shorts")),
    ("kurta", ("kurta", "kurti")),
    ("saree", ("saree", "sari", "lehenga")),
    ("dress", ("dress", "gown", "skirt")),
    ("shirt", ("shirt", "oxford", "blouse")),
)
PATTERN_HINTS = {
    "striped": ("stripe", "striped", "stripes"),
    "checked": ("check", "checked", "checks", "plaid", "gingham"),
    "printed": ("print", "printed", "graphic", "floral", "logo"),
    "solid": ("solid", "plain", "basic"),
}
GENDER_HINTS = {
    "female": ("women", "womens", "woman", "female", "ladies"),
    "male": ("men", "mens", "man", "male"),
    "kids": ("kids", "kid", "boys", "girls"),
    "unisex": ("unisex",),
}
MATERIAL_HINTS = ("cotton", "linen", "denim", "wool", "silk", "polyester", "fleece")


def _tokens(filename: str) -> list[str]:
    stem = filename.rsplit(".", 1)[0].lower()
    return [t for t in re.split(r"[^a-z0-9-]+|_", stem) if t]


def _dominant_color(data: bytes) -> tuple[str, float]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
        w, h = rgb.size
        crop = rgb.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8))).resize((48, 48))
        quant = crop.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette() or []
        counts = sorted(quant.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    picked = None
    for count, idx in counts:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        near_white = min(r, g, b) > 228
        if near_white and count / total < 0.85 and len(counts) > 1:
            continue
        picked = ((r, g, b), count / total)
        break
    if picked is None:
        return "unknown", 0.0
    (r, g, b), share = picked
    name = min(PALETTE, key=lambda n: sum((a - c) ** 2 for a, c in zip(PALETTE[n], (r, g, b))))
    return name, round(min(0.6, 0.3 + share * 0.4), 2)


def local_attributes(image: SkuImage) -> dict[str, Any]:
    tokens = _tokens(image.filename)
    token_set = set(tokens)
    commodity = next((c for c, words in COMMODITY_HINTS if token_set & set(words)), "unknown")
    pattern = next((p for p, words in PATTERN_HINTS.items() if token_set & set(words)), "unknown")
    gender = next((g for g, words in GENDER_HINTS.items() if token_set & set(words)), "unknown")
    material = next((m for m in MATERIAL_HINTS if m in token_set), "unknown")
    if material == "unknown" and commodity == "jeans":
        material = "denim"
    try:
        color, color_conf = _dominant_color(image.data)
    except Exception:
        color, color_conf = "unknown", 0.0
    hint_conf = 0.4 if commodity != "unknown" else 0.0
    return {
        "commodity_type": commodity,
        "material_type": material,
        "primary_color": color,
        "pattern": pattern,
        "gender": gender,
        "style_tags": [],
        "attribute_evidence": {
            "commodity_type": {"source": "filename_hint", "confidence": hint_conf},
            "material_type": {"source": "filename_hint", "confidence": 0.3 if material != "unknown" else 0.0},
            "primary_color": {"source": "pixel_dominant_color", "confidence": color_conf},
            "pattern": {"source": "filename_hint", "confidence": 0.3 if pattern != "unknown" else 0.0},
            "gender": {"source": "filename_hint", "confidence": 0.3 if gender != "unknown" else 0.0},
        },
        "model_version": LOCAL_MODEL,
        "source": "local_heuristic",
    }


def vertex_attributes(image: SkuImage) -> dict[str, Any]:
    try:
        from worker.vertex_extract import extract_attributes_from_bytes

        result = extract_attributes_from_bytes(image.data, image.content_type)
    except Exception as exc:
        log.warning("vertex extraction failed input_index=%s err=%s", image.input_index, type(exc).__name__)
        raise UpstreamUnavailable("Vertex AI attribute extraction unavailable") from exc
    keep = ("commodity_type", "material_type", "primary_color", "pattern", "gender", "style_tags",
            "attribute_evidence", "model_version")
    return {**{k: result.get(k) for k in keep}, "source": "vertex_ai"}


class _LRU:
    def __init__(self, size: int = 256):
        self.size = size
        self.data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


_cache = _LRU()


def default_extractor() -> Callable[[SkuImage], dict[str, Any]]:
    return vertex_attributes if settings.ENABLE_VERTEX else local_attributes


def extract_all(
    images: list[SkuImage],
    extractor: Optional[Callable[[SkuImage], dict[str, Any]]] = None,
    *,
    max_workers: int = int(os.getenv("SKU_RANK_VERTEX_CONCURRENCY", "6")),
) -> dict[str, dict[str, Any]]:
    """sha256 → attributes; duplicates are extracted once, results cached in memory only."""
    from concurrent.futures import ThreadPoolExecutor

    fn = extractor or default_extractor()
    cacheable = fn is vertex_attributes

    def key(img: SkuImage) -> str:
        return f"{settings.VERTEX_MODEL}:{img.sha256}"

    unique: dict[str, SkuImage] = {}
    for img in images:
        unique.setdefault(img.sha256, img)
    out: dict[str, dict[str, Any]] = {}
    todo = []
    for sha, img in unique.items():
        hit = _cache.get(key(img)) if cacheable else None
        if hit is not None:
            out[sha] = hit
        else:
            todo.append(img)
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(todo)))) as pool:
            for img, attrs in zip(todo, pool.map(fn, todo)):
                out[img.sha256] = attrs
                if cacheable:
                    _cache.put(key(img), attrs)
    return out
