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
- [ ] Cities directory.
- [ ] Exact address reproduction/fix (awaiting one address example).
- [ ] Price labels.
- [ ] Composition filter rail.
- [ ] Administrator order history.
- [ ] Timetable configuration (awaiting owner confirmation).
- [ ] Customer shop identity.
- [ ] Delivery online-only payment.
- [ ] Messenger inline check.
- [ ] Full verification, develop integration, dev release and smoke checks.
