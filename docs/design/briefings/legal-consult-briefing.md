# Legal Consult Briefing (RU юрист по ФЗ-152 / ФЗ-54)

**Date drafted:** 2026-05-18
**Consult length:** ~2–4 hours
**Specialization needed:** RU юрист по ФЗ-152 (персональные данные), ФЗ-54 (онлайн-кассы), договор-оферта SaaS
**Outcome:** 3 items confirmed (Q14 tax profile, Q-C3 retention policy, Q12-ε договор-оферта attribution clause) → billing and conversations module unblocked for ship

This document is a focused pre-read for a single ~3-hour legal consult covering 3 RU-law items. Each has a detailed proposed policy — consult is **validation + refinement**, not exploration.

---

## Item 1 — Q14 — Tax profile fields (Settings → Налоговый профиль)

**Context:** Before first billing event for a tenant, we collect tax-identification fields to issue УПД, формировать чек per ФЗ-54, и обеспечить договорные отношения. Tenants are RU legal entities (ИП / ООО / самозанятый).

**Proposed scope (MVP):**

### Supported tenant types
- ✅ **ИП** (индивидуальный предприниматель)
- ✅ **ООО** (общество с ограниченной ответственностью)
- ✅ **Самозанятый** (плательщик НПД через приложение «Мой налог»)
- ❌ **Физлица** — NOT supported as paying tenants in MVP (reasoning below)

### Proposed field list per type

**ИП:**
- ФИО полностью (ФЛ собственника)
- ИНН (12 цифр)
- ОГРНИП (15 цифр)
- Адрес регистрации
- Email для документов
- Система налогообложения (УСН доходы / УСН доходы-расходы / ОСН / Патент / НПД)
- ⚠ Расчётный счёт + БИК — **только если требуется для документов/возвратов** (lazy collect)

**ООО:**
- Название юр.лица
- ИНН (10 цифр)
- КПП
- ОГРН (13 цифр)
- Юридический адрес
- ФИО подписанта/директора
- Email для документов
- ⚠ Расчётный счёт + БИК — only when required

**Самозанятый:**
- ФИО
- ИНН физлица (12 цифр)
- Email для чеков/документов
- Подтверждение статуса самозанятости (через ФНС API или скриншот из «Мой налог»)
- ⚠ Карта/счёт для возврата — only at refund time

### Questions for юрист

1. **Validation**: подтвердите list полей per type — что-то лишнее? что-то критично отсутствует?
2. **Паспортные данные**: подтвердите, что в MVP **не нужны** паспортные данные ни для одного из 3 типов tenants. (наша позиция: повышает PII risk без обоснования)
3. **Физлица exclusion**: подтвердите, что физлица как paying tenant можно legally **отложить** до v1.1+. Альтернативы: предоставлять услугу безвозмездно до оформления статуса? отказывать в обслуживании? рекомендация?
4. **ФНС API для самозанятых**: разрешено ли нам автоматически проверять статус через API «Мой налог»? Какие согласия нужны от tenant?
5. **Расчётный счёт хранение**: если мы храним БИК + расчётный счёт — это персональные/банковские данные требующие особой защиты?
6. **Lazy-collect стратегия**: можем ли мы НЕ собирать расчётный счёт на onboarding, а запросить только при первом refund/документе? Не нарушает ли это требований ФЗ-54?

### What we need to walk away with
- ✅ Approved field list (or modified)
- ✅ Confirmation физлица-exclusion = legally allowed in MVP
- ✅ Confirmation passport-not-collected = legally allowed
- ✅ ФНС API usage rules + required consents

---

## Item 2 — Q-C3 — Retention policy (4-layer model)

**Context:** Conversations module stores chat transcripts (PII), audit events, booking records, sensitive/medical flags. Need retention policy compliant with ФЗ-152 и healthcare data laws.

**Proposed 4-layer model:**

### Layer 1 — Operational transcripts (full conversation text)
- **Retention**: 180 days
- **After 180d**: PII removed (names → UUID, phones → masked), individual messages purged, anonymized aggregate retained for analytics
- **Customer-deletion request**: honored within 30d soft-delete + hard-delete

### Layer 2 — Audit trail
- **Retention**: 365+ days (longer for billing/payment audit, TBD with you)
- **Contents**: event_type / actor_id / timestamp / hash — NO full message content
- **Purpose**: incident investigation, billing disputes, regulatory

### Layer 3 — Booking and payment records
- **Retention**: up to 7 years per бухгалтерия / налоговая отчётность
- **Contents**: `BookingRequest`, `BillingEvent`, attribution metadata, refunds, disputes
- **NOT included**: conversation content

### Layer 4 — Sensitive/medical data
- **Default principle**: minimize. Prefer structured flags over full text.
- Example BAD: storing «у меня диабет, принимаю метформин 1000 мг» verbatim
- Example GOOD: storing `sensitive_flag=True`, `reason=medical_contraindication`, `decision=handoff_to_master` + audit
- If full text needed: separate explicit customer consent + 6 months full + 1 year anonymized-only

### Physical infrastructure (proposed)
- All RU PD stored on RU-located servers per ФЗ-152
- Backup also RU-located
- No cross-border transfer без consent + Roskomnadzor notification

### Customer-deletion workflow (proposed)
- Customer e-mails support@ → CSM verifies identity via initData phone match + manual confirmation step
- Soft-delete 30-day reversal window → hard-delete
- Audit log (Layer 2) retained after deletion (for dispute defense — legal?)
- Customer profile (name, phone, notes) deleted; bookings (Layer 3) keep customer reference as UUID-only

### Questions for юрист

1. **Layer 1 — 180 days for full transcripts**: достаточно или нужно меньше/больше? Особенности при наличии медицинских данных в transcripts?
2. **Layer 2 — Audit retention**: что обязательно по ФЗ-152 / OFD requirements? 365 days хватит или нужно дольше?
3. **Layer 3 — 7 years bookings**: соответствует ли НК РФ / 402-ФЗ о бухучёте?
4. **Layer 4 — Medical minimization**: правильна ли стратегия «structured flags вместо full text»? Какие explicit consents нужны для full medical text?
5. **Physical RU storage**: подтвердите, что для наших типов PD это обязательно. Допустимы ли CDN-кэширования вне РФ для не-PD (фото услуг и т.п.)?
6. **Customer-deletion**: 30-day reversal — соответствует ли GDPR-like ожиданиям + ФЗ-152? Audit retention after deletion = legal?
7. **Sensitive data access logging**: достаточно ли «role-gated + audit on access» для медицинских заметок, или нужно formal consent per access?
8. **Tenant retention overrides**: «only longer, not shorter» — есть ли cases где tenant может legally requested shorter (e.g., высокий privacy bar)?

### What we need to walk away with
- ✅ Approved retention values per layer (or modified)
- ✅ Confirmation на physical infrastructure требования
- ✅ Customer-deletion workflow approved
- ✅ Medical data handling policy approved

---

## Item 3 — Q12-ε — договор-оферта attribution clause

**Context:** Billing model — гибридный: 590 ₽/мес база + 100 ₽ за каждую Бронь classified as `ai_direct`. Договор-оферта должен явно описать правила attribution, refund, dispute.

### Proposed 8-element clause (RU draft)

> **Раздел N. Расчёт за Брони.**
>
> **N.1.** Платформа взимает с Заказчика 100 (сто) рублей за каждую Бронь Клиента, классифицированную системой Платформы как `ai_direct` — то есть Бронь, созданную автоматизированным ассистентом Платформы через программную функцию `execute_confirm` без прямого участия персонала Заказчика, по инициативе Клиента (роль `customer`).
>
> **N.2.** Не подлежат оплате следующие категории Броней:
> - `ai_assisted` — Бронь, где ассистент участвовал в подготовке, но финальное создание выполнил персонал Заказчика;
> - `human_direct` — Бронь, созданная персоналом Заказчика без участия ассистента;
> - `external` — Бронь, поступившая из сторонних систем (YClients UI, телефонный звонок и пр.);
> - `test_admin` — тестовые Брони или Брони, созданные пользователями с ролями владельца/администратора/мастера Заказчика;
> - Перенос существующей Брони (классификация `ai_direct` + действие `execute_reschedule`) — не считается новой Бронью.
>
> **N.3.** При статусе «не пришёл» (`NO_SHOW`), переданном из системы записи Заказчика (YClients), Платформа автоматически возвращает Заказчику 100 (сто) рублей за соответствующую Бронь в следующем счёте.
>
> **N.4.** При отмене Брони Клиентом в течение 1 (одного) часа с момента создания, Платформа автоматически возвращает 100 (сто) рублей в следующем счёте.
>
> **N.5.** При отмене Брони в период от 1 (одного) часа до 24 (двадцати четырёх) часов, решение о возврате принимается службой Customer Success Платформы по обращению Заказчика. Решение фиксируется в журнале аудита.
>
> **N.6.** При отмене Брони позднее 24 (двадцати четырёх) часов возврат не производится.
>
> **N.7.** Заказчик имеет право оспорить классификацию Брони или начисление в течение 30 (тридцати) календарных дней с даты выставления счёта. Спор подаётся через личный кабинет Платформы или электронную почту support@. Служба Customer Success Платформы рассматривает спор в течение 48 (сорока восьми) часов и принимает решение: оставить в силе / частичный возврат / полный возврат. Все решения фиксируются в журнале аудита.
>
> **N.8.** В случае несогласия Заказчика с решением Customer Success, спор эскалируется к руководителю Customer Success или к Платформе (founder-level) — финальное решение принимается в течение 14 (четырнадцати) дополнительных календарных дней. После этого срока решение Платформы является окончательным для целей расчётов.

### Questions for юрист

1. **ФЗ-54 compliance**: соответствует ли механика billing (recurring base + variable per-event) требованиям онлайн-касс? Все ли правильно про чеки и ОФД?
2. **ГК РФ — договор-оферта format**: достаточно ли явно описано «оферта» или нужно добавить акцепт mechanism?
3. **Терминология**: «Бронь Клиента» / «Заказчик» / «классификация» — корректные термины или нужно уточнить?
4. **30-day dispute window**: разумно для B2B SaaS? Не противоречит ли другим требованиям?
5. **Final decision authority**: «Платформа принимает окончательное решение» — допустимо или нужен арбитраж?
6. **Refund mechanics**: правильно ли «автоматический возврат в следующем счёте» — нужны ли отдельные документы для каждого возврата?
7. **Anti-fraud language**: нужно ли добавить пункт про anomaly detection (если salon abuses NO_SHOW flag)? Какая правовая защита?
8. **Cross-reference**: оферта ссылается на «классификация системой Платформы» — нужно ли inline-описать алгоритм классификации или достаточно reference на attribution-policy.md как тех.документ?

### What we need to walk away with
- ✅ Approved final clause text (or modified)
- ✅ ФЗ-54 compliance confirmed
- ✅ Dispute process legally sound
- ✅ Refund mechanics legally sound

---

## Cross-cutting questions

1. **Договор отношения Платформа↔Заказчик**: что мы — поставщик услуг? Налоговый агент? Что-то ещё?
2. **Tenant-customer relationship**: Заказчик (салон) принимает оплату от своих клиентов через YClients, мы только взимаем плату с Заказчика. Правильно?
3. **Recurring billing legal framework**: акцепт на повторные списания — как оформить?
4. **Cross-document consistency**: будем ли поддерживать публичную privacy-policy + пользовательское соглашение для Customers (клиенты салонов)? Это отдельный artifact или integrated?

---

## Outcomes checklist

- [ ] **Q14** — налоговый профиль field list approved
- [ ] **Q-C3** — retention policy 4-layer approved (или modified)
- [ ] **Q12-ε** — договор-оферта clause approved (или modified)
- [ ] Legal opinion on physical RU infrastructure requirement
- [ ] Legal opinion on physлица-exclusion in MVP
- [ ] Legal opinion on ФНС API usage for самозанятый
- [ ] Final draft of договор-оферта (juridically clean version)
- [ ] Recommendation на privacy policy + user agreement structure

**3 items closed → billing and conversations module unblocked for ship.**

---

## Linked artifacts (for юрист context)

- [`attribution-policy.md`](../policies/attribution-policy.md) — full attribution + billing spec
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — conversations module, see §6 retention
- [`assistant-persona.md`](../policies/assistant-persona.md) — voice/identity (relevant for «вы бот?» disclosure rule per ФЗ-152 honest-disclosure)
- [`decisions-log.md`](../decisions-log.md) — full decision history
