# Flower filter illustrations and bouquet counts

> **For agentic workers:** Use superpowers:executing-plans; independent image production uses superpowers:dispatching-parallel-agents and imagegen.

**Goal:** Give all 53 directory flowers an illustration and show available bouquet counts beside the catalogue flower filters.
**Architecture:** Keep nine existing cutout WebP assets and generate 44 missing standalone transparent botanical thumbnails. Centralize name-to-image mapping; reuse the existing location/time/stock/filter-aware flower_options counts in the rail. The mobile flower menu and desktop rail share pictures. No dependencies or migrations.
**Tech Stack:** Existing Django templates, Pillow asset export, pytest, built-in image_gen.

## User-approved scope and decisions

- User explicitly requested generated small pictures and number of available bouquets, and confirmed the entire 53-entry directory.
- Match existing photorealistic cutout flowers with transparent backgrounds; no text, bouquets, wrapping or vases in filter icons. Distinct botanical images for named varieties.
- One built-in generation call per missing asset; independent groups handle disjoint files. Keep original PNGs in ignored scratch; optimized 240px alpha WebP assets in static/market/flowers.
- Counts mean matching available bouquet listings, not stock stems or sizes; respect current address/time and other filters. Preserve zero-result selected filters and existing AND behavior.
- Existing native Django/Pillow and imagegen tools meet the task. Do not add image/network runtime dependencies.

## Tasks

- [x] Write failing tests for mapping coverage and real asset files, counts in rail/menu, AND filters and selected zero-count behavior.
- [x] Generate/copy/inspect 44 PNGs; export optimized assets and record prompts/provenance.
- [x] Add image mapping/property and catalogue context, wire counts/thumbnail markup and CSS in both desktop rail and mobile filter menu.
- [x] Run tests, review, real browser checks, merge to develop and deploy with full static assets and backup; verify all 53 URLs and filters after cutover; promote source to master and push.

## Ledger

- Live catalogue currently has 6 available tags; five use generic glyphs. The directory contains 53 entries, nine already illustrated. Existing counts are computed per filter but not attached to rail items.
- Red: missing tag_image and count reproduced in three tests. All44 built-in generated PNGs saved with exact prompt sidecars, exported240px alphaWebP; total975312bytes. Original9 untouched. Prompt set and hashes saved in static/market/flowers/README.md and assets.json.
- Full local suite: 578 passed,5 PostgreSQL skips. Ruff, Django check, migration check passed. Independent review clean. Contact sheet of all53 visually inspected; actual local IAB confirmed rail thumbnails/counts and filter-menu thumbnails with all53 available on disposable data.
- Dev deployed from develop1818ac1ae21b90907044e6e08dc736a56ca92714 to /srv/romeo-marketplace/releases/20261023-160547-flower-tags. Server578 passed/5skips;634file manifest checked. Backup /srv/backups/romeo-marketplace-before-flower-tags-20261023-160547. migrate/collectstatic, pre/post-cutover image/count/filter smoke, all53HTTPSimage+CSS SHA256 checks passed. Live browser confirmed six available tags with matching icons and counts. Sources promoted to master, no production deployment.
