"""GCS object naming + id resolution helpers."""

from suffai.garments.object_naming import (
    build_garment_object_key,
    parse_ids_from_object_key,
    resolve_source_ids,
)


def test_build_garment_object_key_jpg():
    key = build_garment_object_key(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
        content_type="image/jpeg",
    )
    assert key == (
        "11111111-1111-1111-1111-111111111111_"
        "22222222-2222-2222-2222-222222222222_"
        "33333333-3333-3333-3333-333333333333.jpg"
    )


def test_parse_ids_from_object_key():
    key = (
        "prefix/11111111-1111-1111-1111-111111111111_"
        "22222222-2222-2222-2222-222222222222_"
        "33333333-3333-3333-3333-333333333333.jpg"
    )
    assert parse_ids_from_object_key(key) == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    )


def test_resolve_source_ids_prefers_payload():
    ids = resolve_source_ids(
        {
            "user_uuid": "u1",
            "order_uuid": "o1",
            "item_uuid": "i1",
        },
        "other_other_other.jpg",
    )
    assert ids == {"user_uuid": "u1", "order_uuid": "o1", "item_uuid": "i1"}


def test_resolve_source_ids_from_key():
    key = "user-a_order-b_item-c.jpg"
    ids = resolve_source_ids({}, key)
    assert ids == {
        "user_uuid": "user-a",
        "order_uuid": "order-b",
        "item_uuid": "item-c",
    }
