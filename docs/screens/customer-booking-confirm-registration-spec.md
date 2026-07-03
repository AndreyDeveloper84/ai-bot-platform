# CustomerBookingConfirm — гость → регистрация в Ayla + consent (P0)

> **Статус:** решения по пилоту LOCKED, 2026-07-02 (founder).
> **Рефрейм:** это не «экран подтверждения брони», а **момент, когда гость становится пользователем Ayla**. UX строится вокруг этой идеи.
> **Экран:** `CustomerBookingConfirmScreen` (`/customer/booking/confirm`).
> **Зависимость:** функциональный CTA за `VITE_MAX_OAUTH_ENABLED` — MAX OAuth endpoint (W4) ещё не выкачен.

---

## 1. Что уже ЗАФИКСИРОВАНО founder'ом (не открытые вопросы)

| Принцип | Источник |
|---|---|
| Регистрация «в **Ayla**», не в салоне (салон = provider) | Ayla-first pivot · ayla-personal-ai |
| Consent — **soft-гейт долговременной памяти**, не пользования ботом (до: чат/поиск/подбор; после: память/персонализация/история) | Variant A (global onboarding + consent gate). В коде: декоратор `@consent_required(type)`, document-versioned, tenant-scoped |
| Возврат после OAuth без потери контекста = **P0** | «anonymous gate context preserve = P0 bug if lost». В коде: `sessionStorage` PRIMARY + серверный кэш (W4 #844) |
| **OAuth один раз на экосистему**, потом любые подключённые салоны без повторной регистрации | cross-tenant-invisible-relationship (membership неявно при первой брони) · identity-bridging |

---

## 2. Pending-intent — набор полей (LOCKED)

**Назначение:** `pending-intent` — это **снимок намерения пользователя**, а не объект брони. Задача: после любого прерывания (OAuth / сбой сети / закрытие приложения) Ayla продолжает сценарий, будто ничего не было.

**Семантическое ядро снимка — 6 полей** (существующие вспомогательные поля кода сохраняются, см. ниже):

| Поле | Зачем |
|---|---|
| `service_id` | без него сценарий не восстановить |
| `master_id` | обязательно |
| `slot_iso` | обязательно (ISO 8601 с offset, = `visit_at`) |
| `note` | нельзя терять («буду с ребёнком», «тихая музыка») |
| `tenant_id` | **добавить.** multi-tenant: через год пользователь начал бронь в одном салоне, открыл другой — без `tenant_id` можно восстановить неверно. Стоимость ≈ 0 |
| `entry_point` | **добавить.** Ради **поведения AI** (какой сценарий привёл к брони: recommendation / search / food_scanner / ai_avatar / symptom / direct). Вход в locked `attribution-extensible-model` |

**НЕ храним (осознанно):**
- `coupon` — нет полноценной системы сертификатов (certificate DEFERRED post-pilot). Не усложняем.
- `step` — это **состояние интерфейса**, а intent = состояние **бизнес-процесса**. Поменяется UX → step другой, intent тот же. Плохая зависимость.
- `goal` (похудение/расслабление) — это часть **профиля пользователя**, должна жить отдельно, не в снимке брони.

**Существующие поля кода — СОХРАНЯЮТСЯ** (не входят в «6 семантических», но и НЕ удаляются): `price_rub?` (⚠️ **P0-контракт** «цена сохраняется как известная», `pending-booking-intent.ts:36-39` — никогда не слать `0` как sentinel), `loyalty_choice?`, `service_name?`, `master_name?` (display). Решение founder про «6 полей» = семантическое ядро + что ДОБАВИТЬ (`tenant_id`, `entry_point`) и что НЕ добавлять (`coupon`/`step`/`goal`); это НЕ команда удалить существующие price/display-поля.

**Реализация:** `tenant_id` + `entry_point` добавить и в клиент `PendingBookingIntent` (`apps/miniapp/src/lib/pending-booking-intent.ts`), и в сервер `ServerPendingBookingIntent` (W4 #844, `/auth/verify`). Формы **не идентичны** (сервер: `price_quoted`/`loyalty_apply`, без display-имён; экран нормализует обе — `api.ts:56-57`) — «синхронно» = добавить оба новых поля в обе формы с учётом их именования, а не зеркалить один-в-один. `entry_point` — enum: `recommendation | search | food_scanner | ai_avatar | symptom | direct`.

---

## 3. Booking lifecycle — Variant A (пилот) / Variant B (post-pilot)

**Пилот = Variant A** (регистрация → создание брони). Проще, меньше рисков, иначе можно не успеть; OAuth endpoint ещё не готов.

**Требование к архитектуре: остаться готовыми к Variant B без переписывания** — это ТРЕБОВАНИЕ, не текущее состояние. hold-движок (TTL / авто-снятие / one-hold-per-user) должен жить **в Ayla**. В bot-platform `apps/booking/models.py` — только `RemoteBookingProxy`, **read-only зеркало** канонических Appointment'ов Ayla; `TENTATIVE` (`:779`) там — **mirror-значение статуса**, а НЕ hold-движок. TTL/cleanup в bot-platform нет.
> ⚠️ Cross-repo: booking-lifecycle (в т.ч. hold слота) — **канонический домен Ayla** (ADR-0009). В bot-platform подтверждён enum `TENTATIVE` (`apps/booking/models.py:779`); **TTL + cleanup + one-hold-per-user НЕ подтверждены и должны быть обеспечены в Ayla.** Это задача для Ayla, а не bot-platform.

**Variant B (когда включим post-pilot) — правила против slot-squatting:**
- hold живёт 3 минуты;
- авто-снятие после OAuth;
- максимум один hold на пользователя;
- нельзя открыть второй, пока не завершён первый.

**Триггер включения B — по данным, не интуитивно** (см. §4).

---

## 4. KPI — заложить СЕЙЧАС (домен bot-platform / analytics)

**Метрика:** доля пользователей, которые нажали «Записаться», но **не дошли до подтверждённой брони именно из-за этапа регистрации**.

Воронка (события): `booking_confirm_shown(anonymous)` → `registration_started` → `registration_completed` → `booking_confirmed`. Drop с атрибуцией к регистрации.

**Пороги решения:**
- **2–3%** → Variant A достаточен даже post-pilot.
- **15–20%** → Variant B становится приоритетом всей платформы.

Instrumentation — bot-platform (analytics/observability по ADR-0009; шина `apps/events` snake_case sync).

---

## 5. LOCKED UI (чисто-UI, согласуется с locked-голосом Ayla)

- **Копирайт-рамка:** не «для записи зарегистрируйтесь», а benefit — «Чтобы сохранить запись, историю посещений и персональные рекомендации, заверши регистрацию».
- **Consent (152-ФЗ) человеческим языком:** «Смогу хранить историю твоих записей и помнить предпочтения — только с твоего согласия» + `Подробнее` / `Даю согласие`. Юр-текст за «Подробнее» (consent версионируется, privacy-v2.0). → `progressive-disclosure`.
- **Ошибка OAuth:** «Не получилось завершить регистрацию. **Твоя запись не потерялась.** Попробуй ещё раз» + кнопка **«Повторить регистрацию»**. → `error-clarity` + `error-recovery`. Главный страх — «потеряю время».
- **Невидимая регистрация:** «Остался последний шаг» → OAuth → «Готово. Записала тебя на пятницу в 16:00». Без ощущения отдельной регистрации.

---

## 6. Итог по пилоту

| Пункт | Решение |
|---|---|
| Pending-intent | ядро 6 полей (`service_id, master_id, slot_iso, note, tenant_id, entry_point`); существующие `price_rub`(P0)/`loyalty_choice`/display — **сохраняются**; `coupon/step/goal` — нет |
| Booking lifecycle | Variant A; готовность к B = требование к **Ayla** (hold-движок; bot-platform `TENTATIVE` = mirror-enum `RemoteBookingProxy`) |
| KPI | registration-attributable drop-off — заложить сейчас (bot-platform) |
| UI copy / consent / error / invisible-reg | зафиксировано выше |
| Зависимости | MAX OAuth endpoint (W4); consent-гейт P0 (global onboarding, Variant A) |
| Post-pilot | Variant B по данным KPI; coupon/goal — отдельно |
