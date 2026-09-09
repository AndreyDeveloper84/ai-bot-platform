# DRF-956 / T-05 — Privacy Pre-Pilot Hardening

**Отчёт о проделанной работе**

| | |
|---|---|
| **Ticket** | DRF-956 — Wave 1 / T-05 Privacy pre-pilot hardening |
| **PR** | [#1152](https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1152) → `dev`, OPEN / MERGEABLE |
| **Branch** | `fix/wave1-drf956-privacy-prepilot` |
| **HEAD** | `8eb6f9a` |
| **Baseline** | `origin/dev` = `9e86892` на старте |
| **Статус** | **CODE COMPLETE — MERGE HELD.** Не мержил, не деплоил. |
| **Дата** | 2026-08-07 |

---

## 1. Verdict

Оба подтверждённых release blocker закрыты. Owner ruling выполнен полностью. Три финальных owner-условия закрыты. Пройдено три раунда независимого ревью (два из них adversarial).

Merge и deploy **не выполнялись** — по прямой директиве «merge STOP. Не deploy».

---

## 2. Privacy entry points — карта, составленная ДО патча

Реально достижимых точек удаления оказалось **три**, а не две. Это важно: находка B из тикета относится к пути **B**, а не к C.

| # | Entry point | Confirmation? | Какой сервис | Что удаляет | ProtectedError? | Reachable в пилоте? |
|---|---|---|---|---|---|---|
| **A** | Chat keyword `PrivacyConsentSkill` («удали мои данные») | **НЕТ** — одно сообщение | `resolver.delete_bot_user_data` | диалоги+сообщения, строку `BotUser`, CASCADE-хвост (consents, prefs, reminders, loyalty) | **ДА** | только legacy per-tenant путь |
| **B** | Mini App `/customer/profile` → `DELETE /me/personal-data/` | UI-шит, серверной проверки не было | `privacy.delete_personal_data` | Ayla upstream, green-память, согласия | нет | **ДА — канонический C5** |
| **C** | Mini App legacy `/me` → `POST /me/delete` | ДА — токен `УДАЛИТЬ`, на сервере | `profile.soft_delete_user` | `client_name`, `phone`, `context`, prefs; ставит `deleted_at` | нет | да (маршрут смонтирован) |

C уже чистил `phone` и `client_name` — значит формулировка «остаётся PII в phone/display_name/client_name» описывает именно **B**.

---

## 3. Blocker A — chat path

**Было:** одно сообщение → полный деструктивный каскад без подтверждения. И это падало на обычном пользователе: `delete_bot_user_data` делает hard-delete строки `BotUser`, а на неё ссылаются `on_delete=PROTECT`:

- `observability.AIRequestMetric` — пишется на каждый AI-тёрн,
- `handoff.AdminTask`,
- `tenancy.StaffAssignment`.

Любой, у кого был хоть один AI-тёрн, получал `ProtectedError` — 500 **после** записи audit-строки «requested», и пользователю не сообщалось ничего.

**Стало (Option A — gated):** детерминированный редирект в подтверждённый Mini App flow, без единой мутации, с честным текстом «Сейчас я ничего не удалила». Новую confirmation state machine не строил. `data_delete` оставлен как admin-only helper с предупреждением в докстринге и тестом, фиксирующим `ProtectedError`. Export (недеструктивный) не тронут.

---

## 4. Blocker B — Mini App erase

**Было:** стирались Ayla, память, согласия — но `BotUser.phone`, `display_name`, `client_name` оставались читаемыми, при том что шит обещает «Удалю во всех наших системах».

**Стало:** шаг `profile_pii_erase` гасит `phone`, `display_name`, `client_name`, `avatar_url`, `context` и удаляет `UserPreferences` на всех оболочках человека.

---

## 5. Referential integrity — почему строка сохраняется

Строка `BotUser` **удерживается**, а не удаляется:

- это ключ маршрутизации канала;
- PROTECT-ссылки на неё — аудит/метрик-след, который 152-ФЗ сам и требует хранить;
- `booking.BookingRequest` ссылается `SET_NULL` (законная ретенция транзакционных записей).

Физическое удаление либо бросает `ProtectedError`, либо разрушает след. Поэтому — **erase-in-place**: техническая оболочка сохраняется, идентифицирующие значения стираются.

`ayla_user_id` удержан осознанно: это ключ, под которым живёт `UserPersonalContext` и записан tombstone `forget_all`. Отвязка осиротила бы надгробие, следующий ход создал бы свежий UPC, и стирание тихо откатилось бы. Выведено из кода, не угадано.

**Миграций нет** — все стираемые колонки уже `blank=True, default=""`. `on_delete` не менялся, CASCADE не добавлялся.

---

## 6. Owner ruling — выполнение

| Пункт | Что сделано |
|---|---|
| **§1-2** server-side confirmation | `DELETE /me/personal-data/` проверяет `DELETE_CONFIRMATION_TOKEN` в теле — тот же примитив, что у sibling `POST /me/delete`, второй не заведён. Mini App заставляет ввести токен и релеит его; кнопка мертва до совпадения. |
| **§3-4, §6** no success for work not done | `ayla_delete` без связки — **failure**: адресовать Ayla нечем, нельзя ни выполнить обязательный remote-шаг, ни доказать, что там пусто. `memory_delete` остаётся зелёным при реальном отсутствии связки — память ключуется на `ayla_user_id` и без него существовать не может (единственное локально **доказуемое** «нечего удалять»). |
| **§5** consents ≠ linkage | `ConsentRecord` висит на FK `bot_user`, отзыв идёт по локальной identity через новый `withdraw_personal_data_for_bot_users()`. Старый `withdraw_personal_data()` — тонкая делегирующая обёртка. |

### Последствие, вынесенное наружу намеренно

`BotUser.ayla_user_id` **в проде не пишет никто** (проверено отдельно, см. §7). Значит реальные пилотные пользователи получают честный `502 partial` вместо ложного `200 deleted`. Локальные данные при этом реально стираются, а лист показывает терминальный экран «здесь удалила всё, для основной системы — в поддержку», без бесконечного ретрая.

Настоящее лечение — бэкфилл связки, и он обязан ехать **вместе** с person-level резолвом из этого PR, иначе включит латентные баги вместо починки.

---

## 7. Ключевой факт, найденный по ходу

**Ничто в продакшене не пишет `BotUser.ayla_user_id`.**

- Единственный писатель — `resolve_or_create_global_bot_user`, и только через явный аргумент, которого ни один прод-вызов не передаёт (`apps/channels/max/handler.py` передаёт только `chat_id`).
- Все eventbus-консьюмеры (`booking`, `payment`, `reviews`, `identity`) только **фильтруют** по нему.
- Когда его всё-таки проставят, он ляжет на sentinel-оболочку `global_bot`, а Mini App резолвит оболочку `MAX_BOT_TENANT_SLUG` — это две **разные** строки по `unique_together (tenant, channel, channel_user_id)`.

Отсюда person-level резолв субъекта: по `ayla_user_id` **и** по `(channel, channel_user_id)`. Иначе стирание отрапортовало бы успех, оставив телефон на строке, с которой бот и разговаривает, и объявив живую память отсутствующей.

Факт сохранён в память проекта (`project_ayla_user_id_never_written`).

---

## 8. Финальные owner-условия

### Условие 1 — test evidence без вранья про «CI coverage»

Проверил `.github/workflows/ci.yml`: pytest-гейт запускает **только** `tests/smoke/`, `apps/integrations/ayla/tests/test_contract_route_table.py` и `apps/eventbus/tests/`. **Ни одна privacy-сьюта туда не входит.** Фронтовые тесты не гоняет ни один workflow — ни `vitest`, ни `npm` в workflows нет.

Прежняя формулировка «CI green» вводила в заблуждение. В PR опубликован evidence-комментарий с явной таблицей **AUTHORITATIVE CI vs LOCAL TARGETED TESTS** и точным выводом команд.

### Условие 2 — пустой `channel_user_id` → FAIL CLOSED

**Мой собственный дефект**, не pre-existing. `_person_shell_ids` фильтровал по `(channel, channel_user_id)` безусловно — строка с пустым ключом матчила **все остальные строки с пустым ключом**, то есть посторонних. Self-service удаление превращалось в cross-user деструктив.

Теперь `channel` и `channel_user_id` обязаны быть непустыми до любого sibling-lookup; иначе lookup пуст и каскад сужается до аутентифицированной строки.

### Условие 3 — конфликт `ayla_user_id` → FAIL CLOSED

Тоже мой дефект: `.values_list(...).first()` недетерминирован — при двух оболочках с разными `ayla_user_id` база решала, чей upstream-аккаунт удалить.

Теперь явная триада:

| distinct non-null ids | поведение |
|---|---|
| 0 | unlinked |
| ровно 1 | это человек |
| 2+ | `identity_conflict` — upstream-удаление и стирание памяти **не выполняются** |

На конфликте локальные шаги сужаются до своей строки и рапортуют `own_row_only`. В лог пишется только **количество** различных id, никогда сами id.

Фронт: `identity_conflict` тоже структурный — кнопки ретрая нет; смешанный структурный+транзиентный отказ ретрай сохраняет.

---

## 9. Тесты

### LOCAL TARGETED TESTS (единственное место, где privacy-сьюты реально прогонялись)

```
apps/identity/services/tests/test_privacy.py   [71/71]
apps/skills/privacy_consent/tests/             [30/30]
apps/consent/                                  [36/36]
apps/miniapp_api/tests/                        [179/179] — 3 FAILED (pre-existing)

ruff check / ruff format / mypy                clean
vitest (targeted privacy)                      26 passed
vitest (весь miniapp)                          184 passed
tsc --noEmit                                   0 errors
```

Три падения в `miniapp_api` (`test_c7_payments` ×2, `test_create_booking_ayla` ×1) — booking/payments; воспроизведены на чистом `origin/dev`, к privacy отношения не имеют.

Фронт прогонялся после `npm install --no-save --no-package-lock`: `package-lock.json` рассинхронизирован с `package.json` на `dev` (pre-existing), `npm ci` падает. Лок-файл этим PR не тронут.

### Что покрыто

Чат не мутирует / не заявляет успех / не падает на protected-ссылках; полнота стирания (unlinked sibling-оболочка, prefs, кросс-тенантный охват, изоляция пользователей, иммунитет к PROTECT, идемпотентный повтор с реальным 404, честный partial на 502, audit без PII); полная матрица подтверждения (без тела / пустое / шесть неверных токенов / не-объектные тела / нестроковые confirmation / malformed → 400 и ноль мутаций); person-level резолв; форма unretryable-vs-transient 502; fail-closed на пустом ключе и на конфликте id; фронт — релей токена, мёртвая кнопка, очистка поля, терминальный экран; гард, пиннящий фронтовый токен к бэкенд-константе.

---

## 10. Ревью

**Раунд 1 (privacy/correctness).** P1: `UserPreferences` переживал стирание — `allergies` это свободный текст про здоровье, `birthday_date` прямой идентификатор, а шит обещает «настройки»; legacy-путь это удалял, то есть канонический был строго слабее. Плюс слишком узкий охват шага 4. Исправлено.

**Раунд 2 (adversarial).** Подтвердил оба, вскрыл, что охват ключуется на поле, которое в проде не заполняется, детавтологизировал шесть тестов, вынес два пункта под owner decision. Устояло: чат полностью немутирующий, оболочка и PROTECT-ссылки целы, миграция не нужна, конкурентный DELETE идемпотентен, полустёртого состояния нет.

**Раунд 3 (adversarial, по поверхности подтверждения).** Гейт пробить не смог — пустое тело, чужой content-type, не-объектные тела, нестроковые и null-токены, регистр/пробелы/юникод-двойники, гигантские тела: всё fail-closed, проверка строго раньше любой мутации. Нашёл четыре реальных дефекта, все исправлены:

1. **Поле ввода рендерилось невидимым** — три из четырёх CSS-переменных не существуют в `tokens.css`; невалидный `var()` выигрывает каскад у UA-стилей и схлопывается в `unset`. Вместе с disabled-кнопкой это делало единственный UI реализации права на удаление тупиком. Худшая ошибка в этой работе; ни один тест поймать её не мог — jsdom CSS не вычисляет.
2. `memory_delete` мог отрапортовать `no_state` над живой памятью.
3. 502 отправлял 100% пилотных пользователей в невыигрываемый цикл ретраев.
4. Тест repeated-taps перестал что-либо проверять.

---

## 11. Коммиты

| SHA | Что |
|---|---|
| `592b3cf` | gate чат-пути + `profile_pii_erase` |
| `1358a10` | раунды 1-2 |
| `f0621c7` | owner ruling — server-confirm + правдивый каскад |
| `4437502` | раунд 3 |
| `8eb6f9a` | финальные условия 2-3 — fail-closed |

---

## 12. Runtime actions

**NONE.** Ни деплоя, ни рестартов, ни правок runtime-конфигов, ни операций над реальными данными, prod- или staging-БД.

---

## 13. Открытое (доложено, не чинил)

По ruling вне scope: global-bot scripted redirect; identity EventBus resurrection vector; transcript/memory sweep redesign; объединение двух delete-поверхностей; privacy export subject alignment.

Отдельно:

1. `ayla_user_id` не пишется в проде — корень честного 502; бэкфилл должен ехать вместе с person-level резолвом.
2. Export резолвит субъекта по строке, delete — по человеку.
3. `eventbus.consumers.identity` пересинхронизирует `display_name`/`avatar_url` — вектор воскрешения, сейчас инертен.
4. Транскрипты `Conversation`/`Message` не стирает никто; у `forget_all` нет sweep-задачи.
5. Две расходящиеся кнопки удаления.
6. **Фронтовые тесты не покрыты CI вообще** — 184 зелёных теста существуют только локально.
7. DELETE с телом может резаться прокси — стоит проверить на staging через реальный ingress.

Pending отдельной работой: deployed privacy smoke, pilot `ayla_user_id` provisioning/backfill.

---

## 14. Linear

Комментарий в DRF-956 не ставился — жду решения по merge, чтобы не писать «merged», когда это не так. Готов запостить evidence-комментарий со статусом `In Progress` + «CODE FIX MERGED — runtime smoke pending» сразу после слияния.

---

## 15. Операционное замечание

Параллельный агент трижды переключал checkout основного рабочего каталога (последний раз — на `fix/wave1-drf958-chroma-config-normalization`), один раз снеся несохранённые правки посреди работы. Часть работы пришлось переделать. Дальше работа велась в изолированном worktree `../ai-bot-platform-drf956` — как и предписывает конвенция репозитория. Закоммиченное всё это время было цело на origin.

---

**Merge за владельцем.**
