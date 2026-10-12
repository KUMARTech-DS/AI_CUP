# Suffai — Engineering Assumptions & Gaps

Status: Phase 0 foundations (12 Oct 2026). Open decisions from PRD §19 remain unsigned.

## Source-of-truth rules (locked)

1. `orders.customer_id` joins `customers._id`, never `customers.user_id`.
2. `orders.orderItems[].orderedAccommodities[]` are quantity-level service lines — not physical garments.
3. Do **not** fabricate garment brand/color/size/material from order line quantity.
4. `commodities` = laundry catalog; `stores` = laundry providers — not fashion brands.
5. `bill_number` may be null; `order_id` (`orders._id`) is authoritative.
6. Laundry interactions are garment-servicing evidence, not purchase or market sales.
7. Historical geo uses `schedule.address.pin_code`, not current customer PIN.
8. Brand/competitor dashboards require verified, consented garment observations (empty until then).

## Snapshot audit (data_offline)

| Collection | Rows | Notes |
|---|---:|---|
| orders | 19,908 | 15,819 delivered; 3,767 cancel |
| customers | 22,713 | DOB/gender sparse |
| ordered commodity lines | 39,769 | qty sum 259,377 |
| commodities | 280 | service catalog |
| stores | 84 | laundry providers |
| Join `orders.customer_id → customers._id` | 19,623 / 19,908 | ~1.4% unmatched |
| Wrong join → `user_id` | 0 / 19,908 | confirms PRD rule |
| Orders with schedule PIN | 19,870 / 19,908 | use for historical geo |

## Eligibility (RFM / monetary defaults)

- Include: `order_status = delivered_to_customer` AND `payment_status` in `{paid, Paid (Online)}` (case-insensitive), `refund_flag` not true.
- Exclude: cancelled; unpaid/null payment from default monetary RFM (configurable later).
- Window: trailing 365 days as-of snapshot date for F/M; R = days since last eligible order.
- Premium preference = share of eligible orders at `stores.premium_laundry = true` — service preference, not affluence.

## Privacy defaults

- Suppress aggregate cells with fewer than **k = 30** distinct customers.
- Brand APIs never return names, phones, emails, addresses, or image object keys.
- No individual-level brand access.

## Demo / local mode

- Offline JSON under `data_offline/` is read-only.
- New entities (`garment_instances`, consent, corrections, catalogs) live under `data_derived/`.
- Runtime is **Next.js + FastAPI on Cloud Run** (Blueprint §1). Streamlit prototypes are archived.
- No live paid GCP API calls by default (`ENABLE_VERTEX=false`); no real credentials in repo.

## Explicit gaps (awaiting owner sign-off)

- GCP region / residency, Mongo CDC method, garment upload owner, consent legal basis,
  brand identity resolution, map polygon license, RFM policy finalization, budget/team.
