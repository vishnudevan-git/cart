# Core Domain Model — read this first

_Recorded 2026-07-24 at the user's request. This is a foundational rule that
shapes the whole product; check it before designing any customer-facing page._

## The platform is distributor-based

- **Every storefront belongs to a distributor.** A distributor decides which
  **suppliers** to connect to and which **products** to list in their own store.
- A product is therefore only ever shown **inside a specific distributor's
  context** — never in a generic, distributor-less listing.
- A shopper enters a distributor's context by:
  - logging in (their account is tied to a distributor), or
  - following that distributor's **invite link / store URL**.

## Consequence for the public home page

- **Without a distributor context, there are no products to show.** So a generic
  public home page (no login, no distributor) must **not** display a product
  grid or a product search bar — there is nothing meaningful to search.
- The public home page's job is to get the visitor **into a context**: it leads
  with **Login** and **Sign up** (and, later, distributor store links).
- Product browsing / search lives **after** login or inside a distributor store.

## How this differs from a plain marketplace

This is NOT "one big catalog everyone browses." It is many independent
distributor storefronts, each curated from the suppliers/products that
distributor chose. The many-to-many **distributor ↔ supplier connection layer**
(see PROJECT_NOTES.md §2) is what lets a distributor pick who they source from.
