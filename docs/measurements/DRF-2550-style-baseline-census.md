# DRF-2550 — что лежит в храповике стилевого контракта мини-приложения

Замер 26.09.2026, окно `mini`, дерево `origin/dev` `0a0dd57f`. Только чтение: ни одна строка кода и ни одно имя храповика не изменены.

## Предмет

`tools/lint/miniapp_style_contract.py` (DRF-1066) требует, чтобы каждое имя из статического `className="…"` встречалось как `.имя` в `apps/miniapp/src/styles/`. Имена, для которых правила нет, заморожены в двух храповиках:

- `BASELINE` — «правила нет нигде»: **59** записей `файл::класс`;
- `DESCENDANT_ONLY` — «правило есть только под предком» (DRF-2380): **11** записей.

Сторож отвечает «новых нет». Что внутри замороженного, до этого замера никто не мерил.

## Метод

- Оба списка **импортированы из самого сторожа** (`import miniapp_style_contract`), а не переписаны руками: перепись не может разойтись с тем, что сторож прощает.
- Охват — **132** не-тестовых `.tsx` под `apps/miniapp/src`.
- Для каждой записи `файл::класс` в этом файле посчитаны открывающие теги, у которых в статическом `className` есть этот класс. Записей, у которых не нашлось ни одного тега, — **0**: каждое имя найдено.
- Каждый тег отнесён к одной из трёх групп. **Их нельзя сводить в одну: у них разная цена.**
  - **поверхность инлайном** — `style` несёт `background` / `backgroundColor` / `border` / `borderRadius` / `boxShadow`. Вид есть, но живёт в разметке, а не в словаре;
  - **прочий инлайн** — `style` есть, но только раскладка или текст;
  - **без style** — ни правила, ни инлайна: **вида нет вовсе**. Разметка просит вид, которого не существует.
- «Зовущих» — для файлов из `components/`: сколько других не-тестовых `.tsx` содержат `<ИмяФайла`. У экранов прочерк: их зовёт маршрут.

## Главное

| храповик | имён | тегов | поверхность инлайном | прочий инлайн | вида нет вовсе |
|---|---|---|---|---|---|
| `BASELINE` | 59 | 85 | 3 имени / 3 тега | 11 имён / 13 тегов | **45 имён / 69 тегов** |
| `DESCENDANT_ONLY` | 11 | 18 | 0 | 1 имя / 1 тег | 10 имён / 17 тегов |

- **Поверхность инлайном** — три имени:
  - `snackbar` (`components/Snackbar.tsx:77`) — компонент зовут **19** не-тестовых файлов, **21** место вызова;
  - `modal` и `modal__sheet` (`CustomerBookingDetailScreen.tsx:367`, `:388`).
- **Вида нет вовсе** — 45 имён. Среди них есть имена, которые явно обещают вид:
  - `muted` — 8 тегов в `AdminSalonDayScreen` (пояснения рисуются основным цветом, а не серым);
  - `callout--warning` — 5 тегов в `NewBookingForm` (предупреждение выглядит как обычный текст);
  - `section__title` — 2 тега в `NewBookingForm` и 3 в `AdminSalonDayScreen`;
  - `profile-cards__*` и `profile-payout__*` — 15 тегов в двух экранах.
- Отличить **инертный контейнер** от **потерянного вида** по коду нельзя: это делается глазами или по макету. Каждое снятие имени из храповика — правка вида, то есть решение владельца (§6-фи). Этот замер ничего не снимает.

## Названные пределы

- Вычисляемые имена (`className={cx(…)}`, шаблонная строка) не учтены — как и в самом стороже.
- Строки в таблицах напечатаны по первым шести тегам записи.
- «Зовущих» посчитано только для `components/` (по `<ИмяФайла`); у экранов — по маршруту, числом не выражено.
- Группа «поверхность инлайном» не ловит `style={переменная}`: объект вычисляется, текст слеп.
- Замер не отвечает, правильно ли выглядит элемент там, где вид есть, — только есть ли вид вообще.

## Таблицы

### BASELINE — 59 записей

| файл | класс | тегов | поверхность инлайном | прочий инлайн | без style | зовущих | строки |
|---|---|---|---|---|---|---|---|
| `components/AlreadyNoted.tsx` | `already-noted` | 1 | 0 | 0 | 1 | 1 | 103 |
| `components/AnketaStepInput.tsx` | `anketa-scale` | 1 | 0 | 0 | 1 | 1 | 168 |
| `components/CatalogEmptyState.tsx` | `catalog-empty` | 1 | 0 | 0 | 1 | 1 | 69 |
| `components/InviteMessage.tsx` | `invite-message` | 1 | 0 | 0 | 1 | 1 | 113 |
| `components/MasterCard.tsx` | `master-card__body` | 1 | 0 | 0 | 1 | 3 | 46 |
| `components/OwnServiceForm.tsx` | `master-services__similar` | 1 | 0 | 0 | 1 | 3 | 225 |
| `components/Snackbar.tsx` | `snackbar` | 1 | 1 | 0 | 0 | 19 | 77 |
| `components/SurfaceSwitch.tsx` | `surface-switch` | 1 | 0 | 1 | 0 | 0 | 73 |
| `components/booking/NewBookingForm.tsx` | `callout--warning` | 5 | 0 | 0 | 5 | 2 | 583, 729, 826, 833, 1058 |
| `components/booking/NewBookingForm.tsx` | `draft-rows` | 1 | 0 | 0 | 1 | 2 | 557 |
| `components/booking/NewBookingForm.tsx` | `section__title` | 2 | 0 | 0 | 2 | 2 | 602, 885 |
| `screens/CustomerBookingDetailScreen.tsx` | `modal` | 1 | 1 | 0 | 0 | — | 367 |
| `screens/CustomerBookingDetailScreen.tsx` | `modal__sheet` | 1 | 1 | 0 | 0 | — | 388 |
| `screens/CustomerBookingSuccessScreen.tsx` | `customer-success__payment-note` | 1 | 0 | 0 | 1 | — | 132 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__brand` | 1 | 0 | 0 | 1 | — | 184 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__consent` | 1 | 0 | 0 | 1 | — | 208 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__item` | 1 | 0 | 0 | 1 | — | 161 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__last4` | 1 | 0 | 0 | 1 | — | 187 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__list` | 1 | 0 | 0 | 1 | — | 159 |
| `screens/CustomerCardsScreen.tsx` | `profile-cards__revoke-confirm` | 1 | 0 | 0 | 1 | — | 163 |
| `screens/CustomerNotificationSettingsScreen.tsx` | `profile-notifications__prefs` | 1 | 0 | 0 | 1 | — | 143 |
| `screens/CustomerWellnessDashboardScreen.tsx` | `wellness-dash__pulse` | 1 | 0 | 0 | 1 | — | 1144 |
| `screens/FoodScannerResultScreen.tsx` | `food-scanner-clarify` | 1 | 0 | 0 | 1 | — | 668 |
| `screens/MasterBillingScreen.tsx` | `profile-billing__payout-sum` | 1 | 0 | 0 | 1 | — | 397 |
| `screens/MasterBillingScreen.tsx` | `profile-billing__status-line` | 1 | 0 | 0 | 1 | — | 176 |
| `screens/MasterBillingScreen.tsx` | `profile-cards__brand` | 1 | 0 | 0 | 1 | — | 277 |
| `screens/MasterBillingScreen.tsx` | `profile-cards__consent` | 1 | 0 | 0 | 1 | — | 292 |
| `screens/MasterBillingScreen.tsx` | `profile-cards__item` | 1 | 0 | 0 | 1 | — | 276 |
| `screens/MasterBillingScreen.tsx` | `profile-cards__last4` | 1 | 0 | 0 | 1 | — | 280 |
| `screens/MasterBillingScreen.tsx` | `profile-payout__item` | 1 | 0 | 0 | 1 | — | 412 |
| `screens/MasterBillingScreen.tsx` | `profile-payout__item-amount` | 1 | 0 | 0 | 1 | — | 416 |
| `screens/MasterBillingScreen.tsx` | `profile-payout__item-meta` | 1 | 0 | 0 | 1 | — | 419 |
| `screens/MasterBillingScreen.tsx` | `profile-payout__item-state` | 1 | 0 | 0 | 1 | — | 413 |
| `screens/MasterBillingScreen.tsx` | `profile-payout__list` | 1 | 0 | 0 | 1 | — | 410 |
| `screens/MasterCustomersScreen.tsx` | `master-customers__body` | 1 | 0 | 0 | 1 | — | 116 |
| `screens/MasterDashboardScreen.tsx` | `master-dashboard__day` | 1 | 0 | 0 | 1 | — | 643 |
| `screens/MasterDirectionsScreen.tsx` | `master-services__own` | 1 | 0 | 0 | 1 | — | 224 |
| `screens/MasterInternalChatListScreen.tsx` | `internal-chat-list__group` | 1 | 0 | 0 | 1 | — | 542 |
| `screens/MasterInternalChatThreadScreen.tsx` | `internal-chat-bubble__stamp` | 3 | 0 | 0 | 3 | — | 591, 605, 629 |
| `screens/MasterServiceSelectScreen.tsx` | `master-services__own` | 1 | 0 | 0 | 1 | — | 403 |
| `screens/MasterServicesScreen.tsx` | `master-services__own` | 1 | 0 | 0 | 1 | — | 476 |
| `screens/MasterServicesScreen.tsx` | `own-service-card` | 1 | 0 | 0 | 1 | — | 424 |
| `screens/MasterSettingsScreen.tsx` | `master-settings` | 1 | 0 | 0 | 1 | — | 133 |
| `screens/MasterSettingsScreen.tsx` | `master-settings__coming-soon` | 1 | 0 | 1 | 0 | — | 136 |
| `screens/MasterSetupLandingScreen.tsx` | `setup-landing` | 3 | 0 | 0 | 3 | — | 149, 157, 176 |
| `screens/MasterWorkingHoursScreen.tsx` | `working-hours` | 4 | 0 | 0 | 4 | — | 570, 578, 586, 599 |
| `screens/admin/AdminAvailabilityRequestsScreen.tsx` | `screen__header` | 1 | 0 | 1 | 0 | — | 537 |
| `screens/admin/AdminInternalChatListScreen.tsx` | `internal-chat-list__group` | 1 | 0 | 0 | 1 | — | 368 |
| `screens/admin/AdminInternalChatThreadScreen.tsx` | `internal-chat-bubble__stamp` | 3 | 0 | 0 | 3 | — | 679, 697, 723 |
| `screens/admin/AdminInternalChatThreadScreen.tsx` | `internal-chat-thread__sign-helper` | 1 | 0 | 1 | 0 | — | 796 |
| `screens/admin/AdminInternalChatThreadScreen.tsx` | `internal-chat-thread__sign-toggle` | 1 | 0 | 1 | 0 | — | 776 |
| `screens/admin/AdminSalonDayScreen.tsx` | `badge` | 2 | 0 | 2 | 0 | — | 169, 178 |
| `screens/admin/AdminSalonDayScreen.tsx` | `callout--warning` | 1 | 0 | 1 | 0 | — | 812 |
| `screens/admin/AdminSalonDayScreen.tsx` | `muted` | 8 | 0 | 0 | 8 | — | 262, 265, 333, 335, 407, 409 |
| `screens/admin/AdminSalonDayScreen.tsx` | `salon-day__visit` | 1 | 0 | 1 | 0 | — | 128 |
| `screens/admin/AdminSalonDayScreen.tsx` | `screen__header` | 1 | 0 | 1 | 0 | — | 732 |
| `screens/admin/AdminSalonDayScreen.tsx` | `section__title` | 5 | 0 | 2 | 3 | — | 257, 326, 402, 841, 862 |
| `screens/admin/AdminSettingsPlaceholderScreen.tsx` | `screen__header` | 1 | 0 | 0 | 1 | — | 56 |
| `screens/admin/AdminTeamScreen.tsx` | `screen__header` | 1 | 0 | 1 | 0 | — | 385 |

**Итог BASELINE:** тегов=85 пов-инлайн=3 др-инлайн=13 без-style=69; имён с поверхностью инлайном=3, имён только без style=45, имён с 0 тегов (статич. не найдено)=0

### DESCENDANT_ONLY — 11 записей

| файл | класс | тегов | поверхность инлайном | прочий инлайн | без style | зовущих | строки |
|---|---|---|---|---|---|---|---|
| `App.tsx` | `solo-surface` | 1 | 0 | 0 | 1 | — | 1065 |
| `components/SetupProgressCard.tsx` | `setup-card__item` | 1 | 0 | 0 | 1 | 1 | 66 |
| `screens/MasterCustomersScreen.tsx` | `master-customers__header` | 4 | 0 | 0 | 4 | — | 113, 126, 139, 193 |
| `screens/MasterDashboardScreen.tsx` | `master-dashboard__day-part` | 3 | 0 | 0 | 3 | — | 703, 727, 761 |
| `screens/MasterDirectionsScreen.tsx` | `master-services__header` | 1 | 0 | 0 | 1 | — | 153 |
| `screens/MasterPlaceScreen.tsx` | `master-services__header` | 1 | 0 | 0 | 1 | — | 298 |
| `screens/MasterScheduleScreen.tsx` | `schedule-free` | 1 | 0 | 1 | 0 | — | 888 |
| `screens/MasterServiceSelectScreen.tsx` | `master-services__header` | 1 | 0 | 0 | 1 | — | 292 |
| `screens/MasterServicesScreen.tsx` | `master-services__chip` | 2 | 0 | 0 | 2 | — | 368, 378 |
| `screens/MasterServicesScreen.tsx` | `master-services__header` | 2 | 0 | 0 | 2 | — | 596, 621 |
| `screens/MasterWorkingHoursScreen.tsx` | `working-hours__item` | 1 | 0 | 0 | 1 | — | 619 |

**Итог DESCENDANT_ONLY:** тегов=18 пов-инлайн=0 др-инлайн=1 без-style=17; имён с поверхностью инлайном=0, имён только без style=10, имён с 0 тегов (статич. не найдено)=0
