# Customer live fulfillment statuses

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update customer fulfillment statuses without a reload in the order list and detail page.
**Architecture:** Existing Django session authorization and native browser fetch/timers. Read-only GET returns status-only data belonging to the authenticated customer. Small DOM updates preserve scrolling, focus and payment forms.
**Tech Stack:** Django 5.2, browser Fetch API, node:test and pytest. No dependencies added.

## Agreed behavior and implementation decisions

User requested automatic updates in both customer views, retaining the already
agreed delivery/pickup labels. Use the project's existing polling approach:
5-second interval, hidden-tab pause and immediate refresh when visible. SSE or
WebSockets would add connection infrastructure for this small status payload.
Only statuses and existing cancellation availability change; no navigation,
scroll calls, bank requests, message sending or payment-form replacement.
Failures retain the last known status and retry; expired login stops polling.
Server responses are private/no-store and never include another user's data,
shop identity, contacts or monetary details.

## Tasks and review focus

- [x] Write failing Python tests for endpoint ownership, all status labels, no-store, safe GET and DOM hooks. Write failing JS tests for in-place updates, hidden tabs, concurrent requests, retries, timeout, signed-out session and no navigation.
- [x] Implement GET view/route, shared page hooks and standalone customer-orders.js with polling lifecycle and per-request timeout. Keep payment and fulfillment writers unchanged.
- [x] Run relevant Python and all frontend tests, lint, Django/migration checks and independent review.
- [x] Merge verified branch into develop; back up, deploy exact develop with collectstatic, and verify live endpoint and static asset. Promote checked sources to master and push without production deployment.

## Execution ledger

- Current list/detail already show shared get_status_display, but only at page load. Existing partner.js uses fetch polling; native fetch/timers meet this scope without another library.
- Red: missing route failed 3 Python tests; missing JS failed 6 frontend tests. Green: 575 passed, 5 PostgreSQL skips; all 32 frontend tests passed. Ruff, Django check and migration check passed; independent review clean.
- Initial full run hit a missing parent directory for pytest temp files. Created the ignored scratch parent and repeated the full suite successfully; no product change required.
- Real IAB test against an isolated local SQLite instance: an open list changed preparing to ready, then open details changed ready to courier, solely after service transitions in another process. No reload/navigation was performed for either change. Providers disabled; disposable local data only.
- Fetch cancellation, visibility and cache options checked against MDN platform documentation. No dependencies added.
- Dev verified at /srv/romeo-marketplace/releases/20261023-153910-customer-live from develop 5f2dcc225cb8578877573cefd3ffe5091f1e1725. Backup /srv/backups/romeo-marketplace-before-customer-live-20261023-153910. Server full SQLite 575 passed, 5 skips; transactional live API/labels/ownership and static HTTPS checks passed before/after switch. Connection interrupted by tool update after tests; resumed only after file/environment verification. No production deployment.
