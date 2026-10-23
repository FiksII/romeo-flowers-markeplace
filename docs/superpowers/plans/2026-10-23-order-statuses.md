# Order fulfillment statuses implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the agreed fulfillment chain and completion responsibilities.
**Architecture:** Retain persisted ShopOrder status codes; share role-aware next actions between cards and detail views, enforce transitions in the service.
**Tech Stack:** Existing Django 5.2, Oscar 4.2, pytest; no new dependencies.
**Spec:** docs/superpowers/specs/2026-10-23-order-statuses-design.md

## Constraints and review focus

- Delivery must pass ready; pickup must never pass courier.
- A shop must not complete Romeo delivery, including via forged POST.
- Unpaid online orders cannot advance; completed/cancelled cannot advance twice.
- Legacy courier/completed states remain usable; completion preserves accounting.
- Cards, details, customer screens and table JSON agree on status labels/actions.

## Task 1: State transitions and UI

- [x] Add tests for all three fulfillment routes, skipped-step rejection, payment and role checks, and role-aware card/detail actions; observe failures.
- [x] Update market/models.py labels and next_step, market/access.py role-aware next action, market/orders.py server enforcement, market/templatetags/market_tags.py and order templates. Add choices migration. Update demo history command and old test helpers for mandatory ready and final actor.
- [x] Run focused tests then full Python suite, lint, Django checks and migration checks; review the branch independently.
- [x] Merge verified branch to develop; package exact develop, verify current server release, back up database/media/env, migrate, collectstatic, restart and smoke-test dev.
- [x] Record validation/deployment, promote verified develop to master without production deployment, push branches.

## Execution ledger

- User explicitly confirmed chains and completion responsibilities in chat. Existing administrative override retained; accepted remains the incoming order stage.
- Red: 6 failures reproduced missing ready, old labels, forbidden completion and inconsistent UI. Green: 569 passed, 5 PostgreSQL-only skips; Ruff, Django check and migration checks passed. Independent reviewer found no important issues. One old board assertion was updated to require ready instead of the former skipped stage.
- Existing Django choices and authorization were checked against official Django 5.2 documentation; no dependency changes.
- Dev: /srv/romeo-marketplace/releases/20261023-145624-order-statuses, exact develop 06278d25a753dc148029e24b7a03c9ce4e00f4dc. All 580 file hashes checked. Server SQLite: 569 passed, 5 skipped; PostgreSQL: 574 passed. Migration 0024 and collectstatic applied. Backup: /srv/backups/romeo-marketplace-before-order-statuses-20261023-145624.
- Preflight initially rejected a diagnostic user without email; corrected the fixture only and reran successfully before and after cutover. Transactional smoke checked all routes, stock, order completion, role-aware UI and forbidden direct POST; no persistent test data or messages. HTTPS, assets, service/timer verified. Runtime configuration unchanged, live T-Bank retained.
