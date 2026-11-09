# 07 — Supplier Shipment Routing

How a supplier hands a product into the supply chain, and how it can skip tiers.

Added in the "supplier role shipment" work. Does **not** change pricing.

---

## The idea

The normal chain is **Supplier → Stockist → Distributor → Shop → Customer**.

Some goods are wasteful to move through every tier — a heavy item (e.g. a
washing machine) shouldn't be loaded and unloaded at the stockist, then the
distributor, then the shop. So a supplier can route a product to **skip tiers**
and hand it to a later level directly.

Settings that control this:

| Setting | Field | Choices |
|---|---|---|
| **Ships to** | `Product.delivery_target` | `stockist` (default), `distributor`, `shop`, `customer` |
| **Handover (per product)** | `Product.shipment_mode` | `collect`, `send` |
| **Handover (global)** | `Supplier.default_shipment_mode` + `Supplier.use_global_shipment_mode` | `collect`, `send` |

### Global shipment mode (global wins)

A supplier can set **one global mode** instead of configuring handover per
product. When `Supplier.use_global_shipment_mode` is on, `default_shipment_mode`
**overrides every product's own `shipment_mode`**. The effective mode is
computed by `Supplier.effective_shipment_mode(product)`.

It's applied at shipment creation and **re-asserted on all still-`awaiting`
lines** whenever the supplier toggles the global switch, so pending requests
update immediately. Lines the supplier has already acted on are never rewritten.

### Self-dispatch cost: receiver pays

When a line is **self-dispatched** (`mode='send'`), the **receiving party bears
the pickup / freight cost** — not the supplier. The receiver enters the actual
amount from their own login later; it's stored on
`SupplierShipment.receiver_pickup_charge` for the record and **does not change
the order price automatically**. (Receiver-side entry UI is a planned follow-up.)

- **delivery_target** — the first level that will handle the product. Any level
  *before* the target is **skipped**: it does not see the order and is not notified.
- **shipment_mode**
  - `collect` — after the supplier confirms/prepares the line, the target party
    comes and picks it up.
  - `send` — the supplier dispatches it and records transhipment details
    (dispatched-to, carrier/vehicle, tracking/LR reference, note).

> The supplier proposes both settings on the product add/edit form. **Admin can
> override them** (admin also sees them at product-approval time). *(Admin
> override UI is a planned follow-up.)*

There is deliberately **no "both" option** — offering collect and send on the
same product would change the final price, which we chose to avoid.

---

## What the supplier sees

The supplier **Orders** page (`/supplier/orders/`) has:

- a **"What to pack"** summary (qty, stock, value per product + size; red when stock is short) with **Print** and **CSV** download,
- a **global-mode switch**,
- **filters**: product, date range, dispatch target, order-number search and oldest/newest sort,
- **status tabs** (Needs action · Completed · All) and **pagination** (15 orders per page),
- **bulk actions**: tick awaiting lines (per line, per order, or all on the page) and press *Mark selected ready*; then *Dispatch N to <destination> together* for ready send lines,
- per line: *Mark ready*, *Report delay*, *Can't supply*, and undo for each,
- a **waiting-age tag** per order (turns red after 3 days),
- a **per-order, per-product request list**:

- Lines are grouped under each **Order #**.
- Each product line is its own request row showing: product, variant, qty,
  amount, **ship-to target**, **mode**, and **shipment status**.
- Action per line:
  - `collect` mode → **Prepare & Confirm** button → status `confirmed`
    (target will pick it up).
  - `send` mode → **Dispatch…** form (transhipment details) → status `dispatched`.

So if one order contains products from three suppliers, each supplier sees only
their own lines, each as a separate request.

---

## The shipment record

Each supplier order line gets one `orders.SupplierShipment` row, created by a
`post_save` signal on `OrderItem` (`orders/signals.py`) the moment the line is
saved. The Orders page is therefore a read: it only backfills legacy lines that
predate the signal, and it re-syncs the mode of still-awaiting lines with set-based
UPDATEs (no per-line queries).

**One docket = one consignment.** Bulk dispatch groups send lines by
*destination* within an order. A Shop line and a Customer line on the same order
never share a docket.

**Dispatch prefill.** The "Dispatched to" box is prefilled with the receiver's
name, address and phone. The customer's address is only ever prefilled when the
line's target is `customer`.

| Field | Meaning |
|---|---|
| `order_item` | One-to-one with the `OrderItem` line. |
| `supplier` | Denormalised owner (fast per-supplier queries + stable history). |
| `target` | Snapshot of `Product.delivery_target` at creation. |
| `mode` | Snapshot of `Product.shipment_mode` at creation. |
| `status` | `awaiting → confirmed (shown as "Ready") → dispatched → received`. Side states: `delayed`, `unavailable`, `cancelled`. |
| `issue_reason`, `expected_ready_on`, `issue_note`, `issue_at` | Why a line is delayed / unavailable. The reason and date are public; the note is private (supplier + admin). |
| `dispatched_to_name`, `carrier`, `tracking_reference`, `transhipment_note` | Transhipment details (only for `send`). |
| `confirmed_at`, `dispatched_at`, `received_at` | Timestamps. |

The target/mode are **snapshotted** at creation so later product edits never
rewrite shipments already in flight.

---

## Ready, delayed and unavailable

```
Awaiting ──Mark ready──▶ Ready ──Dispatch──▶ Dispatched      (self-dispatch)
    │                      └──(receiver collects)──▶ Ready for pickup   (collect)
    ├──Report delay──▶ Delayed ──Mark ready──▶ Ready
    └──Can't supply──▶ Unavailable ──Available again──▶ Awaiting
```

- **Ready** means packed. For self-dispatch it is **compulsory before dispatch**: the dispatch form and bulk dispatch only work on Ready lines. Reverting a dispatch returns the line to Ready (it is still packed).
- **Delayed** records a reason and an expected date, and moves the line out of *Needs action* into its own **Delayed** tab (it still has to be packed, so it stays in the packing summary for that tab). The tab appears only while something is delayed. Reasons: production delay, waiting for stock, quality check, transport issue, other.
- **Marking an unavailable line available again** depends on how long ago it was flagged (`SupplierShipment.UNAVAILABLE_UNDO_MINUTES`, 120 minutes). Within the window it is an accidental-click undo and goes straight back to awaiting. After that the customer may no longer want it, and only the shop knows, so the supplier's button becomes **Request to resume**: the line stays Unavailable (`resume_requested_at` is set), the shop sees "Does the customer still want it?" with *Yes, resume* / *No, keep unavailable*, and the line returns to the supplier only when the shop says yes. The supplier can withdraw a pending request, and may ask again after a decline. If the shop cancels the order, the line drops off the supplier's page.
- **Unavailable** is a **flag only**. Nothing is cancelled, refunded or re-priced automatically; the shop and admin decide (wait, or cancel the order). Reasons: out of stock, discontinued, quality rejected, other. The order line's own `fulfillment_status` is deliberately left untouched, because "back-ordered" is tied to the partial-shipment flow.
- Every transition is written to the activity log (`SUPPLIER_READY`, `SUPPLIER_DELAYED`, `SUPPLIER_UNAVAILABLE`, ...).

### Who sees the stage

| Viewer | Where | Sees |
|---|---|---|
| Customer | My order page, only while the order is `pending`/`confirmed` and supplier-sourced | Being prepared · Packed, ready · Dispatched by supplier · Delayed (reason, expected date) · Unavailable (reason). Carrier/docket only for lines the supplier delivers straight to them. |
| Stockist | Order page | Stage of lines routed to the stockist; "Goes direct to ..." for the rest; a hint if lines are not packed yet. |
| Shop | Order page | Stage of every line, plus a banner when the supplier reports a delay or cannot supply. |
| Admin | Order page | Everything, including the supplier's private note. |

The private note is never shown to the customer, stockist or shop. A distributor-tier (wholesaler) screen for incoming orders does not exist yet, so that tier cannot see these stages.

## Skip-tier rule (who sees what)

The important guarantee: **a skipped level never sees the order and is never
notified.**

Implemented today in the **stockist order list** (`stockist/views.py`
`order_list`): a stockist only sees an order if **at least one** of its supplier
lines has `delivery_target == 'stockist'`. If every line skips the stockist
(target is distributor / shop / customer), the order is filtered out entirely.

| Product `delivery_target` | Stockist sees it? | Distributor | Shop | Customer |
|---|---|---|---|---|
| `stockist` | ✅ (normal chain) | ✅ later | ✅ later | ✅ final |
| `distributor` | ❌ skipped | ✅ first | ✅ later | ✅ final |
| `shop` | ❌ skipped | ❌ skipped | ✅ first | ✅ final |
| `customer` | ❌ skipped | ❌ skipped | ❌ skipped | ✅ direct |

> The downstream "receive" acknowledgement for the distributor/shop/customer
> side is a planned follow-up. The stockist skip-filter is live now.

---

## Activity log

Supplier actions are recorded to `logs/activity.log`:

- `ORDER | SUPPLIER_CONFIRMED | #<id> | … | item#<n> <product> → <target>`
- `ORDER | SUPPLIER_DISPATCHED | #<id> | … | carrier=<…> ref=<…>`

---

## Files

| File | Role |
|---|---|
| `products/models.py` | `delivery_target`, `shipment_mode` fields + choices. |
| `products/migrations/0023_*` | Adds the two product fields. |
| `orders/models.py` | `SupplierShipment` model. |
| `orders/migrations/0012_suppliershipment.py` | Creates the shipment table. |
| `suppliers/views.py` | `orders` view (per-order/per-line requests, confirm/dispatch), `_ensure_shipment` helper, product add/edit save the two fields. |
| `templates/suppliers/orders.html` | Grouped request list. |
| `templates/suppliers/partials/shipment_action.html` | Per-line confirm/dispatch controls. |
| `templates/suppliers/product_form.html` | "Shipment routing" block (ship-to + handover selects). |
| `stockist/views.py` | Skip-tier filter in `order_list`. |
| `orders/signals.py` | Creates the `SupplierShipment` when an `OrderItem` is saved. |
| `orders/apps.py` | Registers the signal in `ready()`. |

---

## Interactive explainer

A live, DB-backed visual of this feature is served at **`/supplier-shipment/`**
(see `templates/project/supplier_shipment_explainer.html` and the
`supplier_shipment_api` view). It reads real products and their routing from the
database so you can see which tiers each product skips.

_Last reviewed: 2026-11-02._
