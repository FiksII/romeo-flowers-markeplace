# Публикация на flowers.aestory.space

Развёрнуто 6 октября 2026 года на сервере `155.212.137.171` по запросу владельца. Адрес: https://flowers.aestory.space/.

## Текущая версия

- Релиз: `/srv/romeo-marketplace/releases/20261006-colour-tags-v2`; `/srv/romeo-marketplace/current` указывает на него. Точный коммит исходников записывается в файл `RELEASE_COMMIT` внутри релиза.
- Python 3.13.15, Django 5.2, Gunicorn, PostgreSQL 18. Отдельные пользователь Linux и роль/база PostgreSQL: `romeo-marketplace` и `romeo_marketplace` соответственно. У рабочей роли нет прав суперпользователя и создания баз.
- `romeo-marketplace.service` включена и слушает только `127.0.0.1:8002`. Caddy обслуживает HTTPS, статику и media. `romeo-reservations.timer` каждую минуту освобождает истёкшие резервы.
- Служебный сокет Gunicorn находится в `/run/romeo-marketplace/gunicorn.ctl`; каталог создаёт systemd с владельцем приложения и правами `0750`. Защита `ProtectSystem=strict` сохранена.
- Настройки: `romeo_market.preview_settings`, `DEBUG=False`, HTTPS и защищённые cookies. Демо включено: 168 заказов за шесть недель, 252 части магазинов, 24 аккаунта. Реальные платежи, выплаты и DaData не подключены.
- Постоянные файлы: `/srv/romeo-marketplace/shared/media`; окружение с секретами: `/srv/romeo-marketplace/shared/runtime.env` (root, доступ только группе приложения).
- Начальные пароли генератора заменены случайными до публикации. Закрытая копия доступов на сервере: `/srv/romeo-marketplace/shared/preview-access.json`, права `0600`. Локальный файл для владельца: `.superpowers/deploy/server-access.md`; он исключён из Git. Не копировать эти файлы в публичные документы.

Старая служба `romeo-shop.service` остановлена и отключена. Старые исходники `/srv/romeo-flowers` и база `romeo_flowers` сохранены. Посторонние службы не менялись.

## Цветные теги — 6 октября 2026 года

Форма состава использует цветные теги со скруглёнными углами, поиск по названию и отдельный блок выбранных цветов. Выбранные цветы остаются видны при поиске; повторный клик снимает выбор. Остальные теги возвращаются в порядок частоты использования магазином. Цвета тегов совпадают с типичными цветами растений; в карточке товара используется та же палитра.

Справочник расширен с 50 до 53 фиксированных вариантов: добавлены «Роза красная», «Роза белая» и «Василёк», старые записи и состав товаров сохранены. Миграция — `market.0005`. До публикации локально прошли 84 теста, четыре проверки PostgreSQL пропущены в SQLite-окружении. Резервная копия этого обновления: `/srv/backups/romeo-marketplace-before-colour-tags-20261006`; предыдущий релиз — `/srv/romeo-marketplace/releases/20261006-git-013aad7`.

## Предыдущее обновление через Git — 6 октября 2026 года

На сервере создана Git-копия `/srv/romeo-marketplace/repository`, репозиторий `https://github.com/FiksII/romeo-flowers-markeplace.git`, ветка `feat/marketplace-foundation`. Выполнен `git pull --ff-only origin feat/marketplace-foundation`. Релиз собран из проверенного коммита через `git archive`; рабочая служба использует отдельный снимок кода, чтобы обновление Git не меняло файлы под работающими процессами.

Перед обновлением сохранены PostgreSQL-база (`marketplace.dump`), media (`media.tar.gz`) и путь предыдущего релиза (`previous-release`) в закрытой папке `/srv/backups/romeo-marketplace-before-git-013aad7`. Дамп проверен через `pg_restore --list`. Предыдущий релиз `/srv/romeo-marketplace/releases/20261006-marketplace-v1` сохранён.

В новом релизе установлены зависимости по `uv.lock`, прошли 83 теста; четыре проверки конкурентных операций PostgreSQL пропущены, поскольку этот запуск использовал отдельное SQLite-окружение тестов. Миграция `market.0004` применена к рабочей PostgreSQL-базе; собраны 368 статических файлов. Проверка защищённых настроек сохранила прежние два предупреждения о HSTS includeSubDomains/preload.

После переключения проверены HTTP 200 главной, каталога и CSS, отсутствие публичных приглашений для партнёров, четыре категории и ровно 50 цветов. Реальные HTTPS-входы проверены отдельно: покупатель попадает в свои заказы, партнёр — в кабинет своего магазина, оператор — в управление. Форма партнёра содержит 50 вариантов цветов без поля свободного ввода. Исправлена запись служебного сокета Gunicorn в защищённую папку: сокет перенесён в каталог systemd `/run/romeo-marketplace`, права самого сокета — `0600`.

Для следующих обновлений получать код командой:

```sh
git -C /srv/romeo-marketplace/repository pull --ff-only origin feat/marketplace-foundation
```

После получения кода создавать новый каталог в `releases`, экспортировать туда проверенный коммит, устанавливать зависимости по lock-файлу, выполнять тесты, миграции и `collectstatic`. Переключать `current` атомарно и перезапускать `romeo-marketplace.service` только после проверки нового релиза. Перед каждым обновлением отдельно сохранять текущую базу и media.

Чтобы вернуть предыдущую версию приложения после этого обновления, от root:

```sh
ln -s /srv/romeo-marketplace/releases/20261006-marketplace-v1 /srv/romeo-marketplace/current.rollback
mv -Tf /srv/romeo-marketplace/current.rollback /srv/romeo-marketplace/current
systemctl restart romeo-marketplace.service
```

Добавленная миграцией схема совместима с предыдущим кодом; для возврата приложения откатывать базу не требуется. Восстановление дампа отдельно согласовывать: оно перезапишет изменения после резервной копии.

## Проверка релиза

Архив исходников передан без `.env`, локальных БД, media и виртуального окружения. Проверенный SHA256: `af79a38773c8d8fa66832cdb4ea1693f6bed78deccf8e28cb80509799dd2204c`.

На сервере прошли **82 теста** на отдельной тестовой PostgreSQL-базе, включая конкурентное оформление и проверки защищённых настроек. Тестовые базы и роль удалены после проверки. Выполнены миграции и сборка статики. Проверены доверенный HTTPS с сервера и компьютера владельца, каталог, вход, локальные Chart.js/DataTables, доступность media, службы и журналы. Серверные проверки входа подтвердили разграничение покупателя, партнёра и администратора: чужие магазины скрыты, кабинет оператора закрыт для остальных. После переключения ошибок в журнале приложения нет.

Автоматизация браузера не выполнилась из-за тайм-аута подключения к браузеру; её результат не считается проверкой интерфейса. HTTP и проверки ролей выполнены отдельно. Django deploy-check сообщает только предупреждения о необязательных HSTS includeSubDomains/preload: эти режимы не включались для неизвестных поддоменов.

## Резервные копии и откат

Закрытая папка `/srv/backups/romeo-before-marketplace-20261006T1055Z` содержит:

- `romeo_flowers.dump` — старая база;
- `romeo-shop-files.tar.gz` — старые исходники, окружение, media и статика;
- `Caddyfile` и `romeo-shop.service` — прежние настройки;
- `marketplace-initial.dump` — новая база после публикации.

Чтобы вернуть старый сайт, от root на этом сервере:

```sh
systemctl enable --now romeo-shop.service
install -m 644 /srv/backups/romeo-before-marketplace-20261006T1055Z/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
systemctl disable --now romeo-marketplace.service romeo-reservations.timer
curl -fsS -o /dev/null -w '%{http_code}\n' https://flowers.aestory.space/
```

Восстановление старой базы не требуется: публикация её не меняла. Не удалять новые файлы и базу при откате. Перед последующими изменениями отдельно сохранять текущую базу и media; начальный снимок не содержит будущих изменений.

## Управление

```sh
systemctl status romeo-marketplace.service romeo-reservations.timer
journalctl -u romeo-marketplace.service -n 100 --no-pager
systemctl restart romeo-marketplace.service
```

Разовые Django-команды запускать из `/srv/romeo-marketplace/current` с явным `--settings=romeo_market.preview_settings` и от пользователя приложения. Для реальных продаж потребуется закончить платёжную интеграцию, настроить адресный сервис и перейти на `romeo_market.production_settings` без демо.
