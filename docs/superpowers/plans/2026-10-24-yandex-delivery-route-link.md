# Yandex delivery route link

Add «Построить маршрут» beside the destination in the shared administrator Romeo delivery route partial, covering board and details. Open a new tab at fixed HTTPS yandex.ru/maps with mode=routes, rtt=auto and rtext=shop latitude,longitude~destination. Prefer validated stored delivery-point coordinates; otherwise use the known address encoded with stdlib urllib.parse.urlencode. Hide without an address or outside Romeo deliveries. Only routing points go into the URL; no contacts/order IDs and no automatic request to Yandex. Reuse existing address validation and button styles, add one justify-self CSS rule and bump CSS to v27. No dependency, API key, migration or paid routing API.

Official Yandex support recommends generating links via its URL scheme: https://yandex.ru/support/maps/ru/concept/get-map-reference (the linked legacy developer URL is currently inaccessible). Verify the resulting public synthetic-coordinate URL in Yandex Maps directly.

- [x] Write link parsing, coordinate precedence/fallback, unknown address, partner isolation and live revision tests; confirm RED.
- [x] Implement template tag/shared button and add shop coordinates to live revision.
- [x] Run feature/full tests, frontend checks, Ruff/Django/migration checks; review and inspect a synthetic route in Maps.
- [x] Merge verified feature into develop, backup and deploy full checked develop to dev with migrate/collectstatic and HTTPS QA.
- [x] Record release, promote develop to master without production deployment.

Validation: RED showed no route button; focused 30 tests and all 40 frontend tests passed. Independent review found no actionable issues. Native button checked at 320/390/1280 px without overflow; only known destinations have links, new-tab attributes verified. Direct public synthetic-coordinate map opening was blocked by automatic browser review because Yandex redirected yandex.ru to yandex.com.tr. No alternate browser/network workaround was attempted; URL contents are verified locally and will be checked via dev HTML without following the external Maps URL. This limits verification of the external service itself, not the deployed link.

Final verification: local/server suites 775 passed, nine PostgreSQL checks skipped, six existing MAX SDK warnings; Ruff/Django/migration/diff and 40 frontend tests passed. Release `20261024-061334-yandex-route`, develop `2b80fa9`, all 687 tracked files verified. Backup `/srv/backups/romeo-marketplace-before-yandex-route-20261024-061334`. Actual PostgreSQL transaction smoke and authenticated live HTTPS verified route link contents in card/details, unknown-address hiding and CSS v27 without opening Yandex. Temporary orders/items/session removed; no production deployment.
