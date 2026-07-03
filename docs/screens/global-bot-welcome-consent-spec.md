# Global bot — Welcome + Consent gate (#1046, S1.FE) — P0

> **Статус:** UX-спек для ShiroPy Wave 1B. P0-блокер пилота. Extends #1026.
> **Поверхность:** **разговорная нить бота в MAX** (сообщения + inline-кнопки + callbacks) — НЕ Mini App экран.
> **Домен:** bot-platform (`apps/consent`, `apps/channels/max`). Реализация — ShiroPy (`apps/*`); этот док — UX-вход.
> **Узел пилот-нити:** `consent` в `MAX → **consent** → discovery → слоты → confirm/registration → reminder`.

---

## 1. Проблема (gap)

- Welcome сейчас только на `/start` (`channels/max/handler.py:184`, ported `GREETING_NEW_USER`).
- **Discovery-путь обходит welcome/consent** — пользователь, вошедший через discovery (не `/start`), не видит ни приветствия, ни запроса согласия.
- `@consent_required` (`consent/decorators.py`) существует, но **нигде не применён** → память/проактив фактически не гейтятся.

## 2. Consent-модель (grounded)

`ConsentRecord.ConsentType` (`consent/models.py:63`): `PERSONAL_DATA` (152-ФЗ baseline), `PHOTO_BIOMETRIC` (food scanner), `MARKETING`, `HEALTH`. Document-versioned (`privacy-v2.0`). **Пилотный гейт = `PERSONAL_DATA`.** `PHOTO_BIOMETRIC` — в точке food scanner (не здесь). `MARKETING`/`HEALTH` — отдельно, вне пилота.

Глобальный бот: `current_tenant()=None` → consent пишется в **user-scope** (tenant может быть null = «global user scope», per CLAUDE.md JWT-правило).

## 3. Soft-gate (Variant A — LOCKED; legal ACK #947 pending)

| | Действия |
|---|---|
| **БЕЗ согласия — РАЗРЕШЕНО** | discovery · chat · **одноразовая** booking |
| **ТРЕБУЕТ согласия (`PERSONAL_DATA`)** | персистентная AI-память (G2) · проактивные сообщения |

> ⚠️ **Legal caveat:** enum-docstring говорит «PERSONAL_DATA required for any user contact» (строгое чтение 152-ФЗ). Variant A — мягче (пускает discovery/chat/one-off). **Юрист (#947) может флипнуть A→B** (жёсткий гейт до любого контакта). Спек описывает A; при флипе §5 timing меняется на «до первого ответа».

## 4. Welcome (первое касание) — LOCKED voice

- Голос — **Ayla как персональный AI** (не салон; глобально). Benefit-рамка, как в `customer-booking-confirm-registration-spec`.
- Не «я бот салона», а «я Ayla — помогу найти, записаться и запомню твои предпочтения».
- **Surfaces на ЛЮБОМ первом касании** — и `/start`, и discovery-first (фикс bypass §1).
- Даже без согласия диалог продолжается (soft-gate).

## 5. Consent-момент и копирайт

**Копирайт (человеческим языком, 152-ФЗ):**
> «Смогу хранить историю твоих записей и помнить предпочтения — только с твоего согласия.»
> Inline-кнопки: **`Подробнее`** · **`Даю согласие`**

- `Подробнее` → полный юр-текст (progressive-disclosure); при согласии штампуем `document_version="privacy-v2.0"`.
- Никакой юр-стены в первом сообщении.

**Timing (LOCKED 2026-07-03):** welcome вводит Ayla + **мягко предлагает** согласие (одна строка + кнопки), но **не блокирует** discovery/chat. Жёсткая точка требования — момент, где гейтенная возможность реально включается: **registration в booking-confirm** (уже в его спеке) + **перед первой записью в память**. Т.е. согласие всплывает 2 раза максимум: мягко на welcome, обязательно — на регистрации/memory-write. Без нытья на каждом сообщении.

## 6. Состояния (разговорные)

| Состояние | Поведение |
|---|---|
| never-consented | welcome + soft-gate активен (память off); мягкое предложение согласия |
| granted (version match) | full — память + проактив on |
| declined | soft-allowed действия продолжаются; **не переспрашивать каждое сообщение** (freq-cap); пере-предложить в естественный момент (registration) |
| withdrawn | память/проактив off; explicit-decline audit (`consent/models.py`) |
| version bump (`privacy-v2.0`→next) | ре-consent нужен для гейтенных операций |

## 7. Anti-patterns (НЕ делать)

- ❌ Блокировать discovery на согласии (нарушает Variant A).
- ❌ Спамить запрос согласия каждое сообщение — **freq-cap**; согласовать с **Nudge Arbiter** ([[project_nudge_architecture]]), а не слать напрямую.
- ❌ Юр-стена вместо человеческого текста (progressive-disclosure).
- ❌ Проверять consent только на `/start` — **применить `@consent_required` к гейтенным операциям** (memory-write G2 + proactive), чтобы фикс не зависел от точки входа (устраняет bypass §1).
- ❌ Внутренняя логика наружу («обработка ПДн по ст. …») — benefit, не юр-жаргон.

## 8. UX-принципы (`ui-ux-pro-max`)
- `progressive-disclosure` (юр-текст за «Подробнее»), `primary-action` (один CTA «Даю согласие»), `error-recovery`, benefit-framing.
- **Empty/decline ≠ тупик** ([[project_empty_state_is_next_dialogue_step]]): отказ от согласия → диалог продолжается, не «до свидания».

## 9. Связки
- `customer-booking-confirm-registration-spec` — registration = обязательная точка согласия (consent surfaces там же).
- Память G2 — то, что гейтит `PERSONAL_DATA`.
- Food scanner — `PHOTO_BIOMETRIC` в своей точке (не здесь).
- Nudge Arbiter — проактив только после `PERSONAL_DATA` + через арбитра.

## 10. Governance / scope
- P0-блокер пилота; **реализация — ShiroPy Wave 1B** (`apps/consent`, `apps/channels/max`). Этот док — UX-вход, `apps/*` не трогаю.
- Legal ACK #947 гейтит A→B.
- **Out of scope:** `MARKETING`/`HEALTH` consent-флоу, жёсткий Variant B (если юрист не флипнет), Mini App consent-экран (нить — разговорная).

## 11. Решения (LOCKED 2026-07-03 founder)
1. **Timing:** мягко на welcome + **обязательно на registration / перед memory-write**; discovery/chat не блокируем; не спрашиваем каждое сообщение. (Variant A soft-gate.)
2. **Welcome предлагает, не требует.** Копирайт + кнопки — §5.
3. **A→B флип:** если юрист по #947 флипнет на Variant B — timing меняется на **жёсткий гейт до первого ответа**. Для текущего пилота — мягкий.
