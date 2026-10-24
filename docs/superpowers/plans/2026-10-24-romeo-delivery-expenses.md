# Romeo Delivery Expenses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store optional actual Romeo delivery costs and deduct them from Romeo's financial result.

**Architecture:** Nullable order DecimalField with database validation; admin-only audited form on the order details page. Existing ORM reporting includes costs independently of payment/cancellation and leaves partner balances unchanged.

**Tech Stack:** Existing Django 5.2, Oscar, pytest-django; no additional dependency.

**Spec:** docs/superpowers/specs/2026-10-24-romeo-delivery-expenses-design.md

## Global Constraints

- Internal Romeo expense: no changes to buyer prices, shop balances or payouts.
- Only delivery orders owned by platform accept costs; zero differs from unknown.
- Period follows order creation date, including costs of cancelled/refunded/unpaid orders.
- Publish verified develop to dev; promote to master after dev QA, no production deployment.

## Review Focus

- Forged partner POST must not change cost or order status.
- Negative, nonfinite, oversized or overprecise input must preserve prior value.
- Zero and blank must produce distinct saved/displayed states.
- Cancellation and refund must not erase already-incurred expense.
- Historical and gifted deliveries must support cost recording without new shop balance entries.

### Task 1: Persist, edit and report delivery expenses

**Files:** market/models.py, migration, market/forms.py, market/orders.py, market/portal_views.py, market/templatetags/market_tags.py, market/table_views.py, market/reporting.py, order/report templates, tests/test_delivery_expenses.py, tests/test_reporting.py.

**Interfaces:** `ShopOrder.actual_delivery_cost: Decimal | None`; `set_delivery_cost(part, actor, amount)` validates, locks, saves and audits; `DeliveryCostForm` validates input. `dashboard_report` adds delivery_cost, delivery_margin, platform_gross_total and missing_delivery_costs, and subtracts costs from platform_total.

- [x] Write regression tests for saving/clearing/zero, invalid input, authorization, immutable prices/balances, cancelled costs and report scope.
- [x] Run tests and confirm failure from missing functionality.
- [x] Implement field/constraints/migration, form/service/admin endpoint behavior, reports and admin-only templates.
- [x] Run feature tests, existing payment/report/order tests, full pytest, frontend tests, Ruff, Django checks and migration drift check.
- [x] Review final diff and commit feature.

Validation: initial tests failed on missing cost field and save/error behavior; history test failed before the table amount included costs. Focused regression group: 58 passed. Full run: 761 passed, 9 PostgreSQL tests skipped, 6 existing MAX SDK Pydantic deprecations. All 40 frontend tests, Ruff, Django system check, migration drift and diff checks passed. Fresh independent reviewer found no actionable issues; its separate feature/report run passed 24 tests. Initial sandbox run could not create pytest's default temporary directory; full verification used an explicit project-local basetemp outside sandbox restrictions.

### Task 2: Release and verify dev

**Files:** docs/deployment.md; ignored .superpowers/deploy task scripts and manifests.

- [ ] Merge into develop; verify current dev release and hashes before packaging full develop snapshot/delta.
- [ ] Backup DB/media/runtime, prepare new release, install frozen dependencies, run release tests/checks, migrate and collectstatic.
- [ ] Switch atomically, restart and verify services, HTTPS, admin input and report result, partner isolation.
- [ ] Record release and promote verified develop into master; do not deploy production.
