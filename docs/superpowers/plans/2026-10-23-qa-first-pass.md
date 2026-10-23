# First QA fixes

Requirements supplied by the owner on 23 October 2026. Work proceeds inline,
one independently reviewable fix per branch from current develop, followed by
merge and verified dev deployment. Production is excluded.

## Scope and acceptance

1. Populate the existing Settlement directory with all Moscow Oblast cities and
   Moscow city locations, including Odintsovo, Zelenograd and Zvenigorod. Preserve
   existing settlement IDs and shop relations. Use an additive data migration.
2. Trace exact-address filtering through signed address, receiving context,
   delivery geometry and timetable. Reproduce the reported address before fixing.
3. Show the minimum customer price with «от» only when prices differ. Ensure the
   product detail, listing cards and size selection agree.
4. Include every flower composition present in available bouquets in the quick
   filter rail; keep the existing full composition picker. Extra occasion tags
   are explicitly outside this request.
5. Romeo administrator orders: new, in progress, completed/cancelled history
   below. Retain delivery photos, recipient contacts and courier payment guidance.
6. Restore visibility of the published bouquet in Kakashkinsk after configuring
   an owner-confirmed timetable. Never publish a closed shop by bypassing hours.
7. Customer views hide shop identity and marketplace language except selected
   pickup, which shows shop name and address. Cover cards, detail, basket,
   checkout, customer orders, links and help text; partner/admin views retain it.
8. Offer today whenever timetable and prep time permit. Diagnose actual data
   before changing availability rules.
9. Any delivery requires online payment; pickup supports online and cash on
   receipt. Enforce on both the interface and order-creation service, including
   mixed orders and disabled online payments.
10. Messenger «Проверить» uses an authenticated CSRF-protected POST and inline
    status, with no document reload or scroll change. Preserve fallback POST.

## Existing tools and approach

Keep Django 5.2, Oscar, Shapely 2.2, existing signed DaData addresses, Django
forms/transactions/migrations, vanilla fetch and existing frontend tests. No new
framework is needed for directory data or these corrections. Avoid duplicating
availability, pricing or access logic.

## Investigation evidence

- Dev current: 20261021-155228-telegram-latency, develop 66f180f.
- Settlement contains Moscow, Khimki and Balashikha only.
- Shop 7 (Kakashkinsk) is active; listing 18 is public/in stock; zero weekly
  timetable rows produce zero receiving options.
- Shop 1 lacks Friday work hours while delivery/pickup hours remain all day;
  preparation consequently begins Saturday. Owner confirmation requested.
- Flower rail is hard-coded to nine flower names.
- Operator bottom table currently includes active orders as well as history.
- Checkout currently permits cash on delivery and defaults to cash.

## Verification and release

For every behavioral fix, add and run a reproducing regression, implement, run
focused tests, inspect diff and commit on its own branch. Before deployment run
the full Python suite, frontend tests, Ruff, Django checks and migration checks
on merged develop. Back up current dev, export the verified Git snapshot, test
the prepared release, migrate and collectstatic, switch atomically and smoke
test HTTPS and each changed scenario. Record factual outcomes in deployment docs.

## Progress

- [x] Clean isolated worktree and QA branch created at develop 5614f03.
- [x] Read-only dev diagnosis completed for timetable and catalogue visibility.
- [x] Cities directory.
- [x] Exact-address include/exclude verified against the real configured zone; reported absence was caused by missing hours. City spelling normalization fixed separately.
- [x] Price labels.
- [x] Composition filter rail.
- [x] Administrator order history.
- [x] Timetable configuration: owner confirmed shop 7 daily 09:00–21:00 and shop 1 round-the-clock Friday work.
- [x] Customer shop identity.
- [x] Delivery online-only payment.
- [x] Messenger inline check.
- [x] Full verification, develop integration, dev release and smoke checks.


## Verified result — 23 October 2026

- Dev release `/srv/romeo-marketplace/releases/20261023-134910-first-qa`,
  verified develop source `58352aad062bab9f6262e0471eee37e00cbd413e`.
- All 564 PostgreSQL tests passed, including row-lock/concurrency checks;
  isolated database and role removed. SQLite: 559 passed, five PostgreSQL skips.
  All 26 frontend tests, Ruff, Django and migration checks passed.
- Additive city data migration `0023` applied; 74 oblast entries and five Moscow
  locations available. Existing settlement IDs and shop relations retained.
- Confirmed operational repair: 21 rows added to shop 7 (daily 09:00–21:00);
  missing Friday work row restored to shop 1 (00:00–24:00). Audit rows recorded.
- Backup: `/srv/backups/romeo-marketplace-before-first-qa-20261023-134910`.
  Database dump, media, runtime environment and previous-release path retained.
- New bouquet 18 visible in catalogue and detail; exact signed point inside its
  actual zone includes it and a point outside excludes it. Today has delivery
  and pickup intervals for both affected shops. No zone geometry was changed.
- Confirmed price prefix, pickup identity, online-only delivery rejection,
  operator history and inline messenger feedback after cutover. Live messenger
  check used a mocked sender, with transactional diagnostic data rolled back.
- Browser confirmed new bouquet, its composition, today's intervals and Romeo
  branding. Legal offer intentionally retains actual legal roles; owner did not
  change Romeo's role to seller. Production was not deployed.
- Independent code review found one important shared-copy gap; the footer and
  banner were corrected and their regressions passed before deployment.
