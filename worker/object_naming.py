"""Re-export shared GCS naming helpers for the extraction worker image."""

from suffai.garments.object_naming import (  # noqa: F401
    build_garment_object_key,
    parse_ids_from_object_key,
    resolve_source_ids,
)
