# Bouquet Gallery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task. User authorized execution without further approvals.

**Goal:** Upload and manage up to 20 bouquet photographs in the partner cabinet and Telegram and show a customer gallery.
**Architecture:** Reuse Oscar ProductImage; retain Listing.photo as the primary image pointer. Shared validation and transactional persistence serve both entry points.
**Tech Stack:** Existing Django 5.2, django-oscar 4.2, Pillow, sorl-thumbnail, HTML/CSS/JS.
**Spec:** docs/superpowers/specs/2026-10-23-bouquet-gallery-design.md

## Global Constraints
20 images per bouquet, 5 MiB each, JPEG/PNG/WebP. Preserve current photos, roles, storage and payment wording. No new dependencies. Separate feature branch from origin/develop. Deploy verified develop to dev, no production deployment.

## Review Focus
- Invalid first file followed by valid image must reject the whole upload.
- Foreign or stale image identifiers must never delete or select another bouquet's image.
- Concurrent uploads must not bypass 20-image limit.
- Telegram albums and retries must retain every image once, including before explicit continuation.
- Storage errors must leave no partial gallery or changed primary photo.

### Task 1: Shared gallery and cabinet
Files: market/photos.py (new), market/forms.py, market/portal_views.py, market/migrations/0025_bouquet_gallery.py, templates/market/form.html, templates/market/partials/bouquet_photos.html (new), tests/test_bouquet_photos.py (new).
Interfaces: validate_photo(photo) -> uploaded image; save_photos(listing, uploads, remove_ids=(), primary_id=None) -> None, under save_listing transaction. ProductForm accepts listing and multiple photos; legacy photo accepted for compatibility.
- [x] Write tests: upload two photos and verify two ProductImages and primary pointer; corrupt earlier file rejects all; 21 files rejected; remove primary promotes another; foreign IDs reject all; legacy retained; storage failure rolls back.
- [x] Run new tests and confirm feature failures.
- [x] Implement shared validation, locked persistence, migration without copying files, per-listing form choices, and cabinet preview/remove/primary fields.
- [x] Run new tests plus cabinet/pricing/thumbnail regressions; commit.

### Task 2: Telegram uploads
Files: market/telegram_catalogue.py, tests/test_telegram_catalogue.py.
Consumes: shared ProductForm and save_listing multi-photo support. Produces: persistent ordered photo list in draft, backwards-compatible existing drafts.
- [x] Write tests for photo accumulation, explicit continuation, album messages, duplicate file, max20, publish all and preserve retry behavior.
- [x] Observe new tests fail.
- [x] Add photo step continuation/clear buttons; persist all file IDs; download and validate all before publishing with rollback cleanup; summary count. Update existing dialog fixture for explicit photo continuation.
- [x] Run Telegram suite and commit.

### Task 3: Customer gallery and delivery
Files: market/thumbnails.py, market/templatetags/market_tags.py, templates/market/partials/product_gallery.html (new), templates/market/product.html, static/market/bouquet-gallery.js/css (new), templates/market/base.html, tests/test_bouquet_photos.py.
Consumes Oscar ordered gallery with primary pointer. Produces responsive thumbnails and accessible image switcher with no-JS photo links.
- [x] Write rendered detail assertions showing every image with correct primary and legacy fallback; observe failure.
- [x] Implement image thumbnail helper and gallery template, standalone small JS integration and CSS with versioned assets.
- [x] Run full pytest, frontend node tests, Ruff, Django check/migration check; fresh whole-branch reviewer.
- [x] Commit, merge develop, package tracked develop with SHA256 manifest, inspect live release, back up, migrate/collectstatic, atomically switch/restart, verify live upload and gallery scenario, document deployment, promote master without production deployment.

## Verification record

Feature commits 1fe56f0 and 05a1163 merged into develop 235caca. User refinement places primary/delete controls directly on each image and supports choosing any new image as primary. Local/merged/server SQLite: 631 passed, 6 PostgreSQL skips. Server PostgreSQL: 637 passed. Frontend: 35 passed. Independent review findings were reproduced RED then fixed; none deferred. Browser upload, preview, primary selection, removal, undo, save and customer switching verified. Dev release 20261023-170634-bouquet-gallery verified over HTTPS; 14 legacy photos retained. See docs/deployment.md for backup, manifest and release checks.
