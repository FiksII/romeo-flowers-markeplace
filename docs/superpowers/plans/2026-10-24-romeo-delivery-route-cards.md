# Romeo delivery route cards

**Goal:** Show explicit pickup and destination addresses on compact Romeo administrator delivery cards.

**Approach:** The current card repeats destination/time and spreads contact labels across grid rows. Use a dedicated administrator Romeo delivery card and a shared route partial for the board/details; preserve the existing partner card. Keep origin shop/address, destination/address details, time, recipient/phone, product photo, actual payment instruction and primary actions visible. Put customer contact, pickup instructions and order notes in native HTML details/summary, already used in the project and supported by current browsers ([MDN](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/details)). No new library or migration.

**Files:** templates/market/partials/order_card.html, new romeo_delivery_card.html and delivery_route.html, templates/market/partner_order.html, static/market/market.css, templates/market/base.html, market/templatetags/market_tags.py, tests/test_delivery_route_cards.py.

- [x] Add regressions for both route ends, destination displayed once, explicit unknown destination, preserved payment/actions, unchanged partner card and live revision on shop address updates; observe failure.
- [x] Implement scoped compact card/CSS, route reuse and CSS version bump.
- [x] Run focused and full tests, frontend suite, Ruff/checks; review; inspect desktop/mobile layouts in browser with real rendered templates.
- [ ] Merge into develop, verify current dev release, backup and deploy the full checked develop snapshot with migrate/collectstatic.
- [ ] Verify HTTPS route cards and services, record release and promote to master without production deployment.

**Checks:** Long shop/destination/recipient strings wrap at 320/390 px; photo and payment stay visible; unknown address is never replaced by shop address; confirmed online payment never requests cash; changing shop address refreshes live card.

Validation: route regressions first failed because the route block was absent. Focused group passed 58 tests; a reviewer found the missing no-call instruction, reproduced with a failing regression and corrected in both board/details. Final full suite: 767 passed, 9 PostgreSQL tests skipped; 40 frontend tests passed. Ruff, system check, migration drift and diff checks passed; no migrations. Browser QA on 320/390/1280 px found no horizontal overflow, mouse and Space toggle native details. Same 1280 px synthetic one-bouquet example: prior card 941 px high, new card 559 px (additional recipient/notes variants 537/584 px).
