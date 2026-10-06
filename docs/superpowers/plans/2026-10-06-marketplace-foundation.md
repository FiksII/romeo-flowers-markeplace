# Marketplace Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Working Django Oscar marketplace foundation for Moscow and Moscow Oblast, retaining the Romeo visual identity.

**Architecture:** Oscar owns products, stock, baskets and the parent order. The `market` application owns shops, access, fulfillment context and shop orders. Address suggestions are server-side, signed and limited to the two launch regions.

**Tech Stack:** Python 3.13, Django 5.2, Oscar 4.2, PostgreSQL, Django templates, local JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-06-marketplace-foundation-design.md`

## Global Constraints

- Only Moscow and Moscow Oblast; RUB; Russian UI.
- No required address/time/login for browsing.
- Delivery and pickup selected separately for each shop; one shared delivery address.
- No changes to `../shop`, no copying secrets or runtime data.
- Production PostgreSQL; isolated SQLite for tests/demo.
- No real payment claims or payouts without a configured provider.
- Preserve the existing palette, fonts, source imagery and component styling.

## Review Focus

- Out-of-region or tampered signed addresses must never authorize delivery.
- Overnight hours, holidays, prep time and exact slot boundaries must agree between listing and checkout.
- Cross-shop direct URLs and POSTs must not permit product, order or bank-data access.
- Concurrent or repeated checkout must not oversell stock or duplicate parent/shop orders.
- A changed address, shop suspension or changed price must be rechecked at checkout.

## Task 1: Project and visual foundation

**Files:** `pyproject.toml`, `manage.py`, `romeo_market/{settings,test_settings,demo_settings,urls,wsgi,asgi}.py`, `market/apps.py`, `templates/market/base.html`, source static assets, `tests/test_bootstrap.py`.
**Interfaces:** Django settings; namespace `market`; Oscar basket middleware; `GET /` and `/catalogue/`.

- [ ] Write `test_public_pages_need_no_address`, asserting 200 for `/` and `/catalogue/`, Romeo brand and no forced address dialog; run and observe missing feature.
- [ ] Create project settings and template shell; copy only whitelisted source assets; use pinned existing commerce dependencies.
- [ ] Run `pytest tests/test_bootstrap.py -q`; expected PASS. Commit foundation if Git is available.

## Task 2: Shops, catalog ownership and regional access

**Files:** `market/models.py`, `market/access.py`, `market/forms.py`, `market/admin.py`, migrations, `tests/test_shops.py`.
**Interfaces:** `Settlement`, `Shop`, `Membership`, `Listing`; `shops_for_user(user)` and `get_shop_for_user(user, slug)`; Oscar Product/StockRecord.

- [ ] Test region validation; member ownership; outsiders cannot edit/read private shop resources; admin can manage all shops.
- [ ] Implement models and constrained forms. Only approved active shops are public; bank fields are private; settlement region codes are `77` and `50`.
- [ ] Run shop tests; expected PASS. Generate migration; verify no pending model changes.

## Task 3: Optional address/time and fulfillment availability

**Files:** `market/{context,availability,addresses,catalogue,strategy}.py`, context/filter templates, `static/market/market.js`, `tests/test_availability.py`, `tests/test_addresses.py`, `tests/test_catalogue.py`.
**Interfaces:** `FulfillmentContext`, `next_slot(shop, method, now, day=None)`, `shop_can_receive(shop, context)`, `public_listings(context, filters)`, signed address tokens; hourly slots validated at checkout.

- [ ] Test no-context browsing, city and radius filters, delivery/pickup schedules, overnight intervals, next-day availability, closed dates, prep time, geocoder region rejection and token tampering.
- [ ] Implement deterministic services and the DaData adapter (configured token only); demo uses local signed sample addresses.
- [ ] Apply filtering consistently to home, catalogue, detail and recommendations; add price/type/shop/time filters and appropriate empty states.
- [ ] Run Task 3 tests; expected PASS. Exact address selection is optional for browsing but required for delivery checkout.

## Task 4: Mixed basket and shop orders

**Files:** `market/orders.py`, checkout/basket views and templates, `tests/test_orders.py`.
**Interfaces:** `place_market_order(user, basket, choices, address, contact, now)` returns parent Oscar order with immutable `ShopOrder` parts; `cancel_shop_order(part, actor)` releases only its reservation.

- [ ] Test two-shop mixed delivery/pickup, independent slots, common address, immutable totals/commissions, duplicate submission, insufficient stock, invalid slot, suspension and cancellation isolation.
- [ ] Implement atomic order creation with basket and stock row locks, server recalculation and Oscar reservations. All orders start awaiting payment; no offline payment is silently added.
- [ ] Run Task 4 tests; expected PASS.

## Task 5: Seller, customer and operator dashboards

**Files:** `market/views.py`, `market/urls.py`, account/dashboard templates, product/profile/hours forms, demo seed command, `tests/test_dashboards.py`.
**Interfaces:** seller `/partner/`, operator `/operator/`, buyer `/account/orders/`; shop order transitions scoped to owner; demo seed idempotent.

- [ ] Test seller CRUD boundaries, scheduling configuration, stats limited to own parts, all-region operator view, unpaid fulfillment protection and idempotent demo seed.
- [ ] Implement functional forms for listings/prices/images, shop details, bank data, weekly hours and date exceptions; customer split-order display; operator moderation and audit entries.
- [ ] Run dashboard tests; expected PASS.

## Task 6: Verification, review and delivery

**Files:** `README.md`, `.env.example`, remaining integration tests and UI refinements.

- [ ] Run complete pytest suite, Django check, migration drift check and Ruff.
- [ ] Migrate/seed the demo database; inspect home/catalogue, optional receiving context, checkout and both dashboards at desktop/mobile sizes.
- [ ] Obtain one independent whole-project code review; fix material findings with reproducing tests and run the complete suite again.
- [ ] Document local demo startup, test commands, demo accounts, production PostgreSQL, DaData token and the pending payment-provider integration. Report verified scope and remaining external integrations.
