# Shared loyalty implementation plan

**Goal:** One flower card across every shop; ten paid, completed bouquets earn a free bouquet and free delivery.

**Architecture:** Adapt the adjacent shop's loyalty accounting to immutable marketplace order items. Reuse marketplace checkout, availability, stock and Oscar order creation. Store consumed purchase units so refunds cannot shift already redeemed purchases. Gift orders have their own payment method/status and zero customer, shop and commission amounts.

**Stack:** Existing Django 5.2, Oscar 4.2, templates and CSS; no dependencies.

**Spec:** ../specs/2026-10-23-shared-loyalty-design.md. Owner confirmed all shops together, gift funded by issuing shop, delivery also completely free.

## Constraints and decisions

- Ten bouquets, not ten orders; only paid/completed parts, excluding refunds and gifts.
- Price cap is the mean price of the ten consumed bouquets, rounded down to kopecks.
- Gift claim preserves the ordinary basket. One quantity of one size; existing slot, address and stock validation.
- No bank payment, partner payout or commission; gift instructions explicitly say no money to collect.
- Cancelled gifts do not restore consumed purchases, matching shop.
- Ruling: use the existing delivery owner; free delivery produces no customer charge and no artificial settlement entry.

## Tasks

- [x] 1. Accounting and redemption: tests for mixed shops, partial completion, quantities, mean cap, refund/consumed units, double submission, invalid size/stock/slot; models, migration, service and checkout integration.
- [x] 2. Customer pages: card and rules, size/fulfillment choice, free checkout, account and home links; tests for authentication, ordinary basket preservation and request tampering.
- [x] 3. Partner/operator/bot instructions: distinct free payment state, execution and cancellation, all lists/detail/notifications, no bank/settlement. Tests for pickup and both delivery owners.
- [ ] 4. Verification and release: focused/full Python and frontend tests, lint, migration/check/static checks, independent review, merge into current develop, backup/migrate/collectstatic/restart dev and verify real scenario. Promote tested develop to master without production deployment.

## Review focus

Concurrent requests, partial mixed orders, history changes after a redemption, removed sizes/unavailable stock, gift cancellation, free delivery when online payment disabled, correct gift copy in partner and courier surfaces.

## Progress

2026-10-23: Native isolated worktree created from origin/develop 047a3aa; branch feature/shared-loyalty. Implementation performed inline with TDD; final fresh review requested separately.

Verification: 720 Python tests passed, 9 PostgreSQL-only tests skipped locally; 36 frontend tests passed. Django check, migration drift and Ruff passed. Independent review found three issues; cancellation and gift history reproduced RED to GREEN, redemption now uses one snapshot restricted to locked purchase parts. PostgreSQL race regression is included for server verification before cutover.
