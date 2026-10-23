# Photo loading implementation plan

> Execute inline using superpowers:executing-plans; the user authorized implementation and dev deployment.

**Goal:** Faster repeat photo loading and in-site fullscreen viewing.
**Architecture:** Existing sorl-thumbnail, signed same-origin image delivery, bounded Django file cache, native dialog.
**Spec:** ../specs/2026-10-23-photo-loading-design.md

- [x] Add regression tests for secure stable photo URLs, cache hits, conditional requests and transient failures; run RED. Implement market/photo_delivery.py and photo route, settings, test cache and warm_photo_cache command; run GREEN.
- [x] Add regressions for intermediate card widths/eager first row, implement thumbnail sizes/quality/sizes; run targeted tests.
- [x] Add dialog interaction regressions, implement single/multiple photo modal, controls/swipe/keyboard and versioned assets; run frontend and gallery tests.
- [x] Full Python/frontend checks, Ruff, Django checks and migration check; independent review and fixes.
- [ ] Merge into develop, rerun checks, prepare verified full manifest and dev release. Backup database/media/config, migrate/collectstatic, warm private cache, atomically switch/restart. Verify HTTPS images/cache/dialog/mobile. Promote checked develop to master without production deployment. Record release.

Review focus: invalid/modified tokens, failed storage reads, cache across workers/restarts, single photo without thumbnails, focus/Escape/swipe and original aspect ratio.

Local verification: 739 Python tests passed, 9 PostgreSQL-only checks skipped; all 39 frontend tests passed. Ruff, Django check, migration check and diff checks passed. Independent review has no remaining Critical/Important findings. Review fixes reproduced RED first: reject oversize objects using loaded metadata before django-storages downloads the full file; no-store for 404 and 412 as well as 503.
