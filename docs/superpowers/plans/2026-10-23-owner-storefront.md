# Owner storefront Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Дать владельцу общую витрину доступных ему магазинов.

**Architecture:** Отдельный GET раздел кабинета использует shops_for_user, Listing, штатные цены и карточки в существующей сетке. Фильтр проверяется до загрузки товаров; покупки не добавляются.

**Tech Stack:** Django 5.2, Django Oscar 4.2, текущие шаблоны и стили; без зависимостей.

**Spec:** docs/superpowers/specs/2026-10-23-owner-storefront-design.md

## Global Constraints
- Отдельная ветка feature/owner-storefront от актуального develop и отдельное рабочее дерево.
- Только прямо доступные магазины; публичный статус, наличие, собственные цены и размеры через существующую модель.
- Не открывать партнёру покупательские маршруты. Без миграций и новых библиотек.
- Выпустить проверенный develop на dev; production не выкладывать.

## Review Focus
- Общий сотрудник двух владельцев не должен давать транзитивный доступ к третьему магазину.
- Старый контекст получения в другом городе не скрывает ассортимент владельца.
- Снятый доступ или остановленный магазин исчезает при следующем запросе.
- Скрытые товары, отсутствующие/зарезервированные остатки и отсутствующая цена не попадают в витрину.
- При собственной доставке и нескольких размерах сохраняются реальные цены; фильтр не теряется при пагинации.

### Task 1: Страница общей витрины и навигация
**Files:** market/portal_views.py, market/urls.py, templates/market/partner_storefront.html, templates/market/partials/partner_storefront_card.html, templates/market/partner_index.html, templates/market/partials/partner_nav.html, tests/test_owner_storefront.py, docs/shop-management.md.
**Interfaces:** portal.partner_storefront(request) -> authenticated GET response, context shops, selected_shop, page. `/partner/shops/storefront/?shop=<slug>&page=<number>`.
- [x] Написать HTTP-тесты объединения, изоляции, запрещённого фильтра, наличия/публикации, цен, контекста, отзыва доступа, пагинации и ссылок.
- [x] Запустить тесты; Expected: FAIL/404, так как страница отсутствует.
- [x] Добавить обработчик, маршрут перед динамическим slug, шаблон и карточку; ссылки из выбора магазинов и кабинета. Переиспользовать Listing.price_variants и listing_photo.
- [x] Запустить новый файл и существующие проверки доступа/каталога. Expected: PASS.
- [x] Выполнить весь pytest, frontend-тесты, Ruff, Django check и makemigrations --check. Expected: PASS (PostgreSQL-only тесты пропускаются в SQLite).
- [x] Обновить пользовательскую документацию, выполнить отдельное независимое ревью и исправить существенные замечания. Зафиксировать код и документы.

### Task 2: Проверка и выпуск develop
**Files:** docs/deployment.md; временные скрипты в игнорируемой .superpowers/deploy.
**Interfaces:** полный git archive проверенного develop; manifest commit/files/sha256/expected_release.
- [x] Сверить текущий серверный релиз и исходники, объединить ветку в develop и повторно проверить merge. Expected: все проверки зелёные.
- [x] Сохранить БД, media, окружение и путь старого релиза; подготовить новый релиз из полного снимка develop, проверить манифест, зависимости и тесты. Expected: проверки успешны.
- [x] Выполнить migrate/collectstatic, атомарно переключить current, перезапустить службу. Expected: служба и таймер активны.
- [x] Проверить HTTPS, общую витрину нескольких магазинов с временным доступом в откатываемой транзакции, изоляцию и статические ресурсы. Expected: 200/запрещённый доступ как в контракте.
- [x] Записать подтверждённый выпуск, продвинуть develop в master без production-выкладки.
