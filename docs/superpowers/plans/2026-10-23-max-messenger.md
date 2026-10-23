# MAX Messenger Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the shared dialogue and webhook; independent SDK and channel integration may run in parallel. Track each check here.

**Goal:** MAX has all existing Telegram bot functions, including bouquet galleries, through the MAX-team-verified Python SDK.

**Architecture:** Pin max-messenger/max-botapi-python at 4a286baa06d38f2db9f7244d94f89508743f8fee (MIT, Python >=3.10). Its Bot and attachment models handle MAX API calls; a small connection adapter supplies the current endpoint, Authorization header, and trusted TLS certificates. Reuse the existing durable catalogue dialogue with explicit channel scoping. Durable MAX webhook receipts prevent duplicated actions and allow retry after restarts through the existing notification timer.

**Tech Stack:** Django 5.2/Python 3.13, PostgreSQL, maxapi 0.9.4, aiohttp, existing forms/outbox.

**Spec:** User request: the library recommended by MAX, all our Telegram bot functions, dev deployment by AGENTS.md. No further design questions requested. Existing Telegram behaviour and tests define parity.

## Global Constraints

- Current API: https://platform-api2.max.ru, Authorization token header, HTTPS webhook with X-Max-Bot-Api-Secret.
- Preserve role-specific payment instructions, invitation ownership and opt-in catalogue grants.
- Keep Telegram and VK functional, their links and drafts unchanged; isolate chat IDs by channel.
- Secrets only in ignored files/server runtime.env. No production deployment.
- Notifications keep the existing transactional outbox/retry mechanism; webhook updates also durable.

## Review Focus

- Duplicate and concurrent callbacks cannot create two bouquets; revoked grants stop publishing.
- Same numeric chat ID in two messengers must not share grants, drafts or invitations.
- Multiple images and image documents preserve ordering and the 20-photo limit.
- Media URLs, redirects, size limits and credentials must not permit SSRF or token disclosure.
- A missing token disables only MAX; network failures preserve retryable state and working existing bots.

## Tasks

### 1. SDK transport (market/max_transport.py, tests/test_max_transport.py)
- [x] RED: mock only HTTP boundary; assert current endpoint/header, real SDK serialization, keyboard/media mapping and errors.
- [x] Implement call(method, **params), send(chat_id, text, photos=(), reply_markup=None, message_id=None), preview_photo(chat_id, file_id, document=False), download_photo(file_id).
- [x] GREEN: focused tests; no separate server or polling runtime.

### 2. Channel integration (messengers.py, portal_views.py, settings, templates, setup command, tests/test_max_messenger.py)
- [x] RED: MAX enabled/disabled, invitations and settings, recipients and photograph/payment notification parity, SDK webhook registration.
- [x] Implement channel enum consumer support; SDK calls and setup --channel max without re-registering other bots.
- [x] GREEN: focused tests and existing notification/settings tests.

### 3. Shared catalogue and durable webhook (models, migration, telegram_catalogue.py, max_bot.py, messenger_views.py, urls, send_notifications)
- [x] RED: full MAX dialogue through public webhook; channel isolation, receipts/retries, malformed/forged/group updates, photo gallery/documents and stale buttons.
- [x] Scope existing draft table by channel (default telegram), retain existing class/table; persist MAX receipt event key, raw update, response, attempts/status.
- [x] Reuse catalogue handlers with channel parameter; MAX converts SDK events into shared dialogue input and shared reply markup into SDK messages.
- [x] Drain pending receipts through existing timer; webhook returns promptly after durable acceptance.
- [x] GREEN: focused tests, complete Python and frontend suites, Ruff, check/migration checks; independent review.

### 4. Release
- [x] Merge feature into latest develop, verify combined result, package all source/dependencies/migrations/static.
- [x] Inspect actual dev release; backup, fresh venv with locked dependencies, migrate, collectstatic, atomic switch/restart, HTTPS and scenario checks.
- [x] When token is supplied, validate identity and register only MAX webhook/commands. Otherwise report live activation awaiting token explicitly.
- [x] Record exact verification/deployment evidence; promote verified develop to master and push authorized repository; production untouched.

## Ledger

- Base: develop 1e870bc, dev release 20261023-170634-bouquet-gallery (runtime 235caca).
- Token absent locally/server; ignored .superpowers/deploy/max-bot.env created and opened for owner.
- SDK pinned and inspected: upstream still uses an older URL/query token, requiring a connection adapter while retaining its Bot/method/model implementation.

- Completed: feature a28caae, develop 71f22a3; 687 Python/35 frontend tests, 105 isolated PostgreSQL tests; review fixes verified.
- MAX token verified and configured privately; webhook/commands live on dev 20261023-173933-max-integration. Real recipient chats await owner invitations.
- Deployment held before switch for temporary test-role SET permission/cleanup, then resumed only after 105 passing tests and source hash re-verification.
