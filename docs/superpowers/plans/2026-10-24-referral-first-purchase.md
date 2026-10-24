# First-purchase referral flowers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** One extra loyalty stamp for the first paid, received purchase by each invited friend.

**Architecture:** Extend the existing loyalty card. Store a random personal link and one immutable referral relationship per friend; capture its first successful purchase in the same transaction as payment/completion. Redeem purchase and referral stamps together while locking source parts before referral rows, retaining the existing gift checkout.

**Tech Stack:** Existing Django 5.2 sessions/ORM/transactions, Oscar 4.2, templates, CSS and native browser Clipboard API; no dependencies.

**Spec:** ../specs/2026-10-24-referral-first-purchase-design.md (approved 2026-10-24).

## Global Constraints

- Exactly one stamp per friend's first paid/completed purchase; gifts, self-referrals and previous buyers do not qualify.
- First valid link lasts 30 days before authenticated verified-phone binding, then persists for the account.
- Referral value is source part's mean paid bouquet price excluding delivery; own `paid_total` never includes referral money.
- Source refund removes unspent credit, never reprices spent credit or allows another award.
- Share links expose no personal data; account shows aggregate credit only.
- Separate feature branch, merge tested develop, backup/migrate/collectstatic/switch dev, then promote checked develop to master; no production deployment.

## Review Focus

- Completion and gift redemption locking must not create a User/ShopOrder/Referral cycle.
- A pending order made before attribution must not award a stamp after binding.
- A later link or login must not replace the first eligible owner; self/invalid links must not poison pending attribution.
- Multiple parts, repeated events and a refunded first purchase must never produce a second award.
- Ten referral stamps without own purchases must yield a valid gift limit and zero own paid_total.

---

### Task 1: Attribution and automatic first-purchase award

**Files:** Create `market/referrals.py`, `market/referral_views.py`, migration and `tests/test_referrals.py`; modify `market/models.py`, `market/middleware.py`, `market/urls.py`, `romeo_market/settings.py`, `market/orders.py`, `market/payments.py`.

**Interfaces:** `personal_link(user) -> ReferralLink`; `bind(user, code) -> Referral | None`; `qualify_part(part) -> Referral | None` (inside the existing locked part transaction); `remember(request, code)` / `bind_pending(request)` for guest and verified-phone login. `Referral` owns friend, referrer, created_at, nullable first_purchase, bonus_value, awarded_at and consumed reward. Null bonus with a source records a disqualified first purchase permanently.

- [x] Write failing `tests/test_referrals.py` cases: stable opaque link; verified phone; first valid link/30-day expiry; guest/login; self/invalid links; previous paid/refunded purchases; pending old order; no award until paid and completed; cash/online event order; multiple bouquets/parts/events; refund prevents repeat. Assert 1 row per friend, 1 stamp, mean 2500.00 excluding delivery, no sensitive account data.
- [x] Run `python -m pytest tests/test_referrals.py -q`: expect missing implementation failures.
- [x] Add link and referral models/constraints, generate schema migration. Bind under the invited User lock shared with checkout, reject existing successful histories. On first qualifying part event lock only its Referral row, keep the owner User unlocked, update only source/value/time fields, never retarget a recorded first purchase. Record old-order or preexisting-success disqualification. Hook both payment confirmation and completion, which already lock source parts; later paid callbacks remain idempotent.
- [x] Add fixed referral redirect route and middleware after authentication. Server session stores first valid code/time for 30 days; verified login binds and clears pending attribution. Reject partners/admins through existing access rules. Use SITE_URL for displayed absolute URLs.
- [x] Run referral and existing loyalty/payment/order tests; expect all pass. Commit backend and record evidence in ledger.

### Task 2: Shared redemption and customer interface

**Files:** Modify `market/loyalty.py`, `market/loyalty_views.py`, `market/storefront_views.py`, `templates/market/loyalty.html`, `templates/market/orders.html`, `templates/market/partials/loyalty_card.html`, `static/market/loyalty.css`; create referral partial, `static/market/referral.js`, `tests/frontend/referral.test.cjs`; extend `tests/test_referrals.py` and `tests/test_loyalty_postgres.py` or add referral concurrency file.

**Interfaces:** Extend `progress` with referral count; merge own `(ShopOrderItem, units)` with `(Referral, 1)` sorted by earned time and stable tie-breakers. `Referral.unit_price` supplies the frozen bonus value. `claim` snapshots source IDs first, locks the union of own and captured referral source parts in ascending PK order, then locks only captured Referral rows; newly arriving bonuses wait for the next snapshot. Existing LoyaltyPurchase remains own purchases only; consumed referral points directly to its gift reward.

- [x] Write failing mixed-card tests: 9 x 3000 + referral 2000 => limit 2900; ten referrals => available 1 and paid_total 0; gift replay once; refund before/after redemption; old rewards unaffected; source concurrent refund/new completion cannot alter claim snapshot. Account and program show own URL/copy button/aggregate, no friend data; copy wording says stamps.
- [x] Write frontend tests: Clipboard API success, rejection and unsupported API select the visible input and explain manual copying without navigation.
- [x] Run new Python/frontend cases: expect assertion failures or missing script.
- [x] Extend progress/claim as above, preserve ordinary basket and existing immutable purchases. Update card rules and add a reusable referral partial on both pages, responsive CSS and explicit per-page script assets. Retain free gift payment and courier instructions.
- [x] Run referral/loyalty/frontend checks: expect pass; commit and ledger results.

### Task 3: Verification, review and release

**Files:** Update this plan, the spec status if needed and `docs/deployment.md`; add only necessary regression fixes/tests.

**Interfaces:** Entire feature and migration from Tasks 1–2; existing deployment workflow and isolated PostgreSQL test settings.

- [x] Run full Python suite, all `node --test tests/frontend/*.test.cjs`, Ruff, Django check, makemigrations --check --dry-run, diff check. Expected no new failures; SQLite skips explicitly reported.
- [x] Review the whole branch with a fresh reviewer under requesting-code-review; fix important findings with reproducing RED/GREEN tests. Check mobile layout and copying in a browser against synthetic local data.
- [x] Refresh develop, merge the feature and rerun required integration checks; push tested develop. Inspect actual dev release before packaging and create backup/new release using docs/deployment.md, excluding secrets/media/local databases/venv. Apply migration and collectstatic, verify full suite plus real PostgreSQL race tests before switching.
- [x] Switch/restart dev and check HTTPS, services, migration and synthetic referral-to-gift/refund flow, clean synthetic data. Record release/hash/backup/verification in deployment documentation. Promote verified develop to master without production deployment. Final response links dev and reports checks and any limitations.

## Execution ledger

Implementation runs inline in the already isolated feature worktree; user approved the complete design and implementation scope. No further permission checkpoint is needed for reversible implementation or ordinary dev deployment. Task outcomes and rulings are recorded in the plan-scoped ignored workspace.

## Result

2026-10-24: complete. Dev release c19c432 deployed to 20261024-135638-referral-first-purchase. Local/server 804 passed, 15 SQLite skips; frontend 50 passed; isolated PostgreSQL 93 passed including six referral races. Both review findings reproduced RED to GREEN and fixed. Full manifest, backup, migration 0029, collectstatic, HTTPS and synthetic referral/gift/refund checks passed. Verified develop promoted to remote master; production not deployed. Detailed evidence: docs/deployment.md.
