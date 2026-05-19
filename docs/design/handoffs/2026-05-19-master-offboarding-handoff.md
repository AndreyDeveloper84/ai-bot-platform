# Master Offboarding / Termination — Engineering Handoff

**Date:** 2026-05-19 r1
**Status:** Production-blocking — master separations happen weekly across portfolio; need clean termination flow
**Reads:** [`./2026-05-19-master-substitution-handoff.md`](./2026-05-19-master-substitution-handoff.md), [`./2026-05-19-master-time-off-handoff.md`](./2026-05-19-master-time-off-handoff.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md), [`./2026-05-19-master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md), [`../policies/single-assistant-identity.md`](../policies/single-assistant-identity.md), [`../policies/booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md)

> Master leaves the salon. Could be voluntary (new job, moving city), mutual (career change), or terminated (admin decision). Either way: existing bookings, customer relationships, earnings settlement, reviews, master's data — all need clean closure with audit. No silent removals, no customer surprises, no orphaned bookings.

---

## 0. Why this exists

### 0.1 The operational gap

Today master separation = admin manually removes from staff list, customer bookings orphan, customers get «booking cancelled» without context, master's earnings cycle hanging, master's account access unclear. Per [`master-substitution-handoff §Q-MS2`](./2026-05-19-master-substitution-handoff.md): beyond 180 days = de facto separation; no spec.

### 0.2 The promise

Single source for:
- 5 offboarding types §3
- Notice → wind-down → final payout → access revocation flow §4-8
- Customer reassignment + notification flow §6 reusing substitution machinery
- Data retention rules §7
- Earnings final-settlement protocol §8
- Reviews / reputation preservation §9
- Mutual / hostile termination differences §10
- Returning-master path («may be back later») §11
- 4 NEW models + 18 endpoints + 14 events

---

## 1. Scope

### IN
- 5 offboarding types §3 (resignation / mutual / non-renewal / for-cause-termination / extended-leave-conversion)
- Notice period UX (4-week default, can be shorter for cause)
- Existing-booking wind-down: complete, reassign, or cancel
- Customer reassignment: NAMED inheritor / pool / customer-choice
- Customer notification (mediated, non-shaming)
- Earnings final-settlement (final cycle + outstanding tips)
- Review history retention rules §9
- Master account access transitions (active → notice → final-day → revoked)
- Master's data access during notice (audit logs of activity)
- Wellness/AI customer data NEVER transferred (per privacy hierarchy)
- Re-enrollment flow if master returns later §11
- Admin termination workflow with 4-eye for HOSTILE cases §10.2
- 14 NEW events

### OUT
- HR contracts / legal employment law (Russia labor specifics) — salon HR
- Severance pay accounting — salon's matter
- Non-compete enforcement — out of scope
- Master moving to another tenant on platform (could in future allow inter-tenant migration, Phase 4+) — separate
- Master suspension (temporary not permanent) — separate `master-suspension-policy.md` future
- Mass offboarding (whole team leaves) — separate `tenant-shutdown-policy.md` future, partial overlap with `tenant-suspension-pause-ux.md`
- Customer following master to new salon — separate Phase 4+
- Tax-final-report on offboarding for IP/самозанятый — out of scope; salon HR + master directly
- Phase 4+ retrospective analytics on master attrition by tenant

---

## 2. Strategic constraints — non-negotiable

### 2.1 Customer never told why master left
Per [`single-assistant-identity §2.2`](../policies/single-assistant-identity.md): customer message uses neutral «{{master}} больше не работает в {{salon_name}}». NEVER:
- ❌ «{{master}} уволилась»
- ❌ «{{master}} ушла к нам в декрет... [navigates to permanent]»
- ❌ «{{master}} обвинена в...»

### 2.2 Master's data is theirs at offboarding
- Master can export own data §8.4 (earnings history, schedule pattern, reviews about themselves)
- Customer data is NEVER exported by master (privacy boundary)
- 30-day window for master to export after offboarding effective; then revoked

### 2.3 Customer data is the customer's
- Not «inherited» by anyone
- New assigned master sees scoped data per substitution §5 model
- AI memory of customer-master pair stays customer-only (master's view revoked)

### 2.4 Wellness data permanently insulated
- Per [`core-wellness-profile.md`](../policies/core-wellness-profile.md) — customer wellness data is customer-only
- Offboarding master never had it; new master doesn't get it
- Customer's wellness modules continue unchanged with new master assignment

### 2.5 Reviews about offboarded master preserved
- Per [`master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md) — reviews stay
- Aggregate publicly hidden (no public master page MVP)
- Customer who left review can still edit within their 7d window
- Master's aggregate frozen on offboarding date

### 2.6 NO retaliation
- Admin's revoking access doesn't include data deletion
- Master's earnings rights honored to final payout
- Reviews NOT scrubbed/altered post-offboarding (data integrity)

### 2.7 Bilateral consent on for-cause-termination
Per Q-MO5: terminated master gets formal admin-side response + reason if requested. Cannot be silent.

### 2.8 Audit immutability
Every offboarding action immutably audited. Founder review available for hostile cases §10.2.

### 2.9 Notice period default 4 weeks (can be flexed)
- Standard 4 weeks for resignation
- Mutual: 2 weeks default
- Non-renewal: per contract terms (configured per tenant)
- For-cause: immediate possible
- Master can request shorter; admin can require longer (negotiated via internal-admin-chat)

### 2.10 No mass-broadcast customer message
- Per-customer message based on whether they have active relationship
- Customers who never booked offboarded master = no notification
- Customers with future bookings = notified per §6
- Customers with past relationship but no future = no notification (just visible on next book attempt)

### 2.11 Re-enrollment doesn't restore data
If master returns 6 months later: starts fresh staff profile, no auto-restoration of customer assignments. Customer can re-choose them.

### 2.12 Audit trails must capture admin authority chain
- For HOSTILE termination, 4-eye admin (or founder) required to approve
- Single admin can't unilaterally HOSTILE-terminate

---

## 3. Five offboarding types

### 3.1 RESIGNATION
- Master initiates
- 4-week notice default
- Tone: amicable separation
- Customer message: neutral, «{{master}} больше не работает у нас, с радостью передадим вас другому мастеру»

### 3.2 MUTUAL_AGREEMENT
- Both sides agree
- 2-week notice default
- Tone: amicable
- Customer message: same as resignation

### 3.3 NON_RENEWAL
- Contract ends, not renewed
- Notice per contract (configurable)
- Tone: amicable to customer
- Master Bot DM acknowledges non-renewal calmly

### 3.4 FOR_CAUSE_TERMINATION
- Admin/founder initiates
- 4-eye admin required §10.2
- Notice can be immediate
- Tone: matter-of-fact to customer
- Master receives formal communication + reason (in admin-master internal chat, NOT bot)
- Customer message: SAME neutral framing — never «we let her go because...»

### 3.5 EXTENDED_LEAVE_CONVERSION
- Substitution (per substitution-handoff doc #4) exceeded 180 days OR substitution master requests not-return
- Auto-convert active substitution to offboarding
- 0-week notice (already absent)
- Customers already with substitute experience — re-assignment continues with substitute master

### 3.6 Type selection matrix

| Type | Initiator | Default notice | 4-eye admin? | Customer framing | Earnings final settlement |
|---|---|---|---|---|---|
| RESIGNATION | Master | 4 weeks | No | «Больше не работает» | Standard cycle close |
| MUTUAL_AGREEMENT | Either | 2 weeks | No | «Больше не работает» | Standard cycle close |
| NON_RENEWAL | Admin (contract) | Per contract | No | «Больше не работает» | Standard cycle close |
| FOR_CAUSE | Admin | Immediate possible | YES | «Больше не работает» | Standard cycle close + dispute path |
| EXTENDED_LEAVE | Auto | 0 weeks | No | (already informed via substitution) | Final substitution-period close |

---

## 4. Master offboarding flows

### 4.1 RESIGNATION submission flow

Master Mini App → Settings → «Уйти из студии»:

```
┌────────────────────────────────────────┐
│ ← Уйти из студии                        │
├────────────────────────────────────────┤
│ ⚠ Это серьёзный шаг. Если есть          │
│   сомнения — поговорите с                │
│   {{salon_owner}}.                        │
│                                        │
│ Прочитаем что произойдёт:                │
│                                        │
│ 1. Завершите свои записи до             │
│    {{notice_end_date}}                   │
│ 2. {{salon_owner}} согласует             │
│    финальные детали                      │
│ 3. Помощник предложит вашим клиентам    │
│    другого мастера                       │
│ 4. Финальная выплата по обычному         │
│    графику                               │
│ 5. Через 30 дней после {{notice_end}}    │
│    доступ к приложению закроется         │
│                                        │
│ ── Когда уйти? ──                        │
│ Финальный день работы:                   │
│ [_____________] (по умолчанию через 4    │
│                   недели)                 │
│                                        │
│ ── Что хотите сказать студии? ──         │
│ Кратко, только для {{salon_owner}}:      │
│ [_____________________________]         │
│                                        │
│ Если хотите вернуться когда-нибудь —    │
│ можно. [Подробнее]                       │
│                                        │
│ [Отправить запрос]                       │
└────────────────────────────────────────┘
```

After submit:
- Notice period begins
- Master in `OFFBOARDING_NOTICE` status
- Admin notified

### 4.2 Master offboarding dashboard

Once in notice period, Master Mini App home shows banner:

```
┌────────────────────────────────────────┐
│ ⓘ Период ухода: до 16 июня (28 дней)    │
│                                        │
│ Текущие задачи:                          │
│ • Завершить 32 будущих записи           │
│ • Подтвердить замещение                 │
│ • Финальная выплата 17 июня              │
│                                        │
│ [Открыть]                                │
└────────────────────────────────────────┘
```

Detail:

```
┌────────────────────────────────────────┐
│ ← Период ухода                          │
├────────────────────────────────────────┤
│ С: 19 мая                                │
│ По: 16 июня (28 дней)                    │
│                                        │
│ ── Записи на период ──                  │
│ Сегодня и до 16 июня: 32 записи         │
│ Все будут идти как обычно — вы их       │
│ выполняете.                              │
│                                        │
│ ── Записи после 16 июня ──              │
│ После вашего ухода: 23 записи           │
│ Помощник предложит этим клиентам         │
│ другого мастера. Можете                  │
│ выбрать кого предложить:                 │
│ [Выбрать инхеритора ▾]                   │
│                                        │
│ ── Финальная выплата ──                  │
│ Цикл закрывается 17 июня.                │
│ Все накопленные чаевые войдут в неё.    │
│ Способ — на карту (как обычно).          │
│                                        │
│ ── Что хотите сказать клиентам ──        │
│ Можно одно прощальное сообщение         │
│ постоянным клиентам:                     │
│ [Написать ✏]                             │
│                                        │
│ ── Возвращение ──                        │
│ Если когда-нибудь захотите вернуться —  │
│ {{salon_owner}} может вас пригласить    │
│ заново. [Подробнее]                      │
│                                        │
│ ── Изменить даты ──                      │
│ [Обсудить со студией]                    │
│                                        │
│ ── Отозвать решение об уходе ──          │
│ [Передумала]                              │
└────────────────────────────────────────┘
```

### 4.3 Customer reassignment configuration

Same patterns as substitution-handoff §3 but applied permanently:

```
┌────────────────────────────────────────┐
│ ← Кому передать клиентов?                │
├────────────────────────────────────────┤
│ После 16 июня ваши постоянные клиенты   │
│ (47 человек) должны перейти к кому-то.  │
│                                        │
│ Что предложить?                          │
│ ⦿ Одному мастеру — выбрать кого ▾       │
│ ◯ Студия распределит                     │
│ ◯ Каждый клиент решит сам                │
│                                        │
│ ── Тёплое слово на будущее ──            │
│ Если хотите написать пару строк, что    │
│ передать клиентам — впишите:             │
│ [_____________________________]         │
│ (макс 200 знаков)                       │
│                                        │
│ Будет показано клиентам как:             │
│ «{{master}} попросила передать: ...»     │
│                                        │
│ [Сохранить]                              │
└────────────────────────────────────────┘
```

### 4.4 Recall flow («Передумала»)

Within first 7 days of notice period only, master can withdraw:

```
┌────────────────────────────────────────┐
│ Передумали?                              │
├────────────────────────────────────────┤
│ Можете отозвать уход в течение 7 дней   │
│ с момента запроса.                       │
│                                        │
│ Сейчас прошло: 3 дня                     │
│ Осталось: 4 дня                          │
│                                        │
│ После 7 дней решение становится         │
│ окончательным — {{salon_owner}} уже      │
│ строит планы.                            │
│                                        │
│ Уверены?                                  │
│ [Да, остаюсь]   [Нет, ухожу как планировала]│
└────────────────────────────────────────┘
```

«Да, остаюсь» — notice period cancelled, status returns to `ACTIVE`, audit captures.

### 4.5 Day-of-final-shift Bot DM

```
{{master_first_name}}, сегодня ваш последний день. На сегодня — 4 записи.

Помощник передаёт всё спокойно: ваши клиенты получат уведомления, ваш цикл
закрывается завтра по плану.

Доступ к приложению будет ещё 30 дней, чтобы вы могли скачать свою историю.

Спасибо за работу в {{salon_name}}. Если когда-нибудь захотите вернуться —
двери открыты.
```

### 4.6 Post-offboarding period (30-day grace)

Master access tier degrades:
- Day 1-30 after notice_end_date: «read-only access» — can view own earnings history, request export, see audit log
- Day 31+: account revoked; data retention per §7

---

## 5. Admin offboarding flows

### 5.1 Admin Mini App «Уходящие» tab

```
┌────────────────────────────────────────┐
│ 🚪 Уходящие мастера                      │
├────────────────────────────────────────┤
│ ── В период ухода ──                    │
│                                        │
│ Анна — увольняется (Resignation)         │
│ Финальный день: 16 июня (28 дн.)        │
│ Записей на период: 32                    │
│ Записей после: 23                        │
│ Назначен инхеритор: Лена                 │
│ [Открыть]                                │
│                                        │
│ ── Запросы на рассмотрение ──            │
│                                        │
│ Марина — мутуальное расставание          │
│ Запрос 17 мая, ожидает согласования      │
│ [Рассмотреть]                            │
│                                        │
│ ── Завершённые (последние 30 дней) ──   │
│                                        │
│ Лера — non-renewal — ушла 5 мая          │
│ [История]                                │
└────────────────────────────────────────┘
```

### 5.2 Admin reviews resignation

```
┌────────────────────────────────────────┐
│ ← Анна уходит                            │
├────────────────────────────────────────┤
│ Тип: Resignation (по собственному)       │
│ Финальный день: 16 июня                  │
│ Уведомила: 19 мая                        │
│                                        │
│ ── Что написала Анна ──                  │
│ «Переезжаю в Питер, спасибо за всё»     │
│ (видите только вы)                       │
│                                        │
│ ── Что предложила ──                     │
│ Инхеритор: Лена для постоянных клиентов │
│                                        │
│ ── Что затронет ──                       │
│ • 32 записи в период ухода (Анна        │
│   выполняет сама)                        │
│ • 23 записи после ухода (требуется     │
│   реасайн)                               │
│ • 47 постоянных клиентов (получат       │
│   уведомление о переходе к Лене)         │
│                                        │
│ ── Финальная выплата ──                  │
│ Цикл 4-17 июня закроется по плану. К    │
│ выплате ориентировочно ≈ 38 200 ₽       │
│                                        │
│ ── Действия ──                           │
│ [✓ Согласовать]                          │
│ [Изменить даты / детали ▾]               │
│ [💬 Обсудить с Анной]                    │
└────────────────────────────────────────┘
```

«Согласовать» → proceeds. «Изменить» allows admin to negotiate notice period (with master via chat). «Обсудить» opens internal-admin-chat.

### 5.3 Admin FOR_CAUSE termination flow

Different from resignation flow — admin initiates:

```
┌────────────────────────────────────────┐
│ ← Расстаться с мастером                  │
├────────────────────────────────────────┤
│ Это серьёзное решение. Опишите           │
│ обстоятельства:                          │
│                                        │
│ Мастер: [Выбрать ▾]                     │
│                                        │
│ Тип:                                     │
│ ⦿ Мутуальное соглашение                  │
│ ◯ Прекращение контракта (срок)          │
│ ◯ По причине (для-cause)                │
│                                        │
│ ── При for-cause ──                      │
│                                        │
│ Причина (внутренне, аудит):              │
│ [_____________________________]        │
│                                        │
│ ⚠ Решение по «for cause» требует        │
│   подтверждения от двух администраторов  │
│   (4-eye) или одного founder.            │
│                                        │
│ Кто второй подписант?                   │
│ [Выбрать админа ▾]                       │
│                                        │
│ Финальный день: [_____________]          │
│                                        │
│ Сказать мастеру сейчас или дать день?    │
│ ⦿ Помощник пришлёт уведомление сейчас    │
│ ◯ Я скажу лично, потом активирую          │
│                                        │
│ [Подать на 4-eye]                        │
└────────────────────────────────────────┘
```

Until second admin approves, status stays in `PENDING_4_EYE`. Founder can substitute for second admin.

### 5.4 Admin sees post-offboarding history

Read-only view of completed offboardings per master, with all artifacts: notice period, final payout, customer reassignments, audit.

---

## 6. Customer notification

### 6.1 Customers with future bookings — staggered notification

When offboarding takes effect (notice_end_date), customer with future booking with offboarded master receives:

```
{{customer_first_name}}, {{master_first_name}} больше не работает в
{{salon_name}}.

Ваша запись на {{date}} в {{time}} остаётся за нами — с радостью передадим
вас другому мастеру.

{% if inheritor %}
{{master_first_name}} предложила {{inheritor_first_name}} как замену —
{{inheritor_first_name}} {{rating}} ⭐, опыт {{exp_years}} лет.

[Записаться к {{inheritor_first_name}}]
[Выбрать другого мастера]
[Перенести / отменить]
{% else %}
Могу подобрать мастера под ваши предпочтения.

[Подобрать мастера]
[Перенести / отменить]
{% endif %}

{% if farewell_message %}
{{master_first_name}} попросила передать: «{{farewell_message}}»
{% endif %}
```

### 6.2 «Last visit» heads-up (during notice period)

If customer has booking with master during notice period, message is normal (master is still working). No mention of upcoming departure unless customer asks. NEVER announce «her last day» — that's master's choice if she wants to mention.

### 6.3 Customers without future bookings — no proactive message

Per §2.10: no spam. Customer's next book attempt will surface master unavailable + alternatives via standard booking flow.

### 6.4 Customer's wellness profile unchanged

Customer's wellness data, AI memory, photo history NEVER reassigned. New master sees only scoped per substitution model §5.

---

## 7. Data retention rules

### 7.1 Master's own data
- **Earnings history**: retained per legal requirement (typically 3+ years for tax)
- **Schedule history**: retained (helps with «Anna's typical hours» analytics)
- **Reviews about master**: retained, frozen on offboarding day
- **Booking history**: retained
- **Master's profile photo + bio**: archived; new bookings can't reference
- **Master's AI prompt customizations** (Phase 3+ if individual): retained for 30d post-offboarding then deleted

### 7.2 Master access timeline

| Period | Access |
|---|---|
| Active | Full access |
| Notice period | Full access |
| 0-30 days post-final-day | Read-only (own data, history, export) |
| 31-365 days | No access; data retained admin-side |
| 1+ years | Per data retention policy; mostly anonymized aggregate |

### 7.3 Customer-master data scope
- AI memory of (customer, master) pair: when master offboarded, master's view of that AI memory revoked instantly (notice_end_date+0)
- New master assigned to customer: gets scoped view per substitution model §5
- Customer's overall AI memory unchanged

### 7.4 Audit data
- All offboarding actions audit-retained 7 years (consistent with attribution-policy)
- Founder + Q12-δ cohort review access

### 7.5 Master's export availability
Per §4.6 — 30-day read-only window. Master can request export of own data: earnings, schedule pattern, own-reviews aggregate.

---

## 8. Earnings final settlement

### 8.1 Final cycle includes ALL accumulated

- Final cycle covers from previous cycle close to notice_end_date
- All COMPLETED bookings count
- All ACCUMULATED tips count
- Standard cycle close process per master-earnings §8

### 8.2 Outstanding dispute handling

If master has open earnings dispute at offboarding:
- Dispute continues to resolution
- Admin/founder cannot force-close
- Resolution timing may extend past offboarding (audit captures)
- Master's read-only access extended if needed to participate

### 8.3 Tip held in transit

If tips were captured (passthrough mode) but not yet paid out:
- Settled with final cycle
- Customer receives «{{master}} получит вашу благодарность в финальной выплате» if they ask

### 8.4 Bonuses / advances

If admin had given advance against future earnings (Phase 4+):
- Reconciled in final cycle
- Audit captures

### 8.5 Negative balance scenario (rare)

If somehow master owes salon (advance > earnings):
- Salon HR handles, NOT platform
- Platform doesn't enforce collection

### 8.6 Multi-tenant master offboarding from one tenant

Master at tenants A + B leaves only A → only A's ledger closes; B's continues. Master's earnings dashboard shows only B post-offboarding A.

---

## 9. Reviews and reputation

### 9.1 Reviews preserved
Per [`master-reviews-feedback §1`](./2026-05-19-master-reviews-feedback-handoff.md): never deleted. Frozen at offboarding date.

### 9.2 Master sees own reviews 30-day window
Master read-only window allows review history view but no new actions (no «thank customer» / «flag» post-offboarding — past actions remain).

### 9.3 Customer ongoing edit-window
Customers within 7-day edit window can still edit existing reviews. Even past offboarding.

### 9.4 Aggregate computation
- Master's `MasterReviewAggregate` frozen on offboarding day
- Admin and (during 30-day window) master see frozen aggregate
- Aggregate not updated even if old reviews revised post-offboarding (data integrity)

### 9.5 New reviews disabled
Customer cannot leave review for booking with offboarded master that happens post-offboarding (no such booking can exist anyway).

### 9.6 Reviews referenced by salon analytics
Salon aggregate analytics (which master got which themes) remain computable.

---

## 10. Mutual vs hostile termination

### 10.1 Mutual

Per §3.2 — both sides agree. Standard flow, no escalation.

### 10.2 Hostile / FOR_CAUSE termination

Triggers:
- 4-eye admin (or founder substitute) required §5.3
- Audit captures both admins
- Master receives formal notification via internal-admin-chat OR external (admin's choice per §5.3 «I'll tell in person» option)
- Master can request reason in writing
- Master's earnings dispute right intact
- Founder Q12-δ cohort review available

### 10.3 Customer messaging
Same neutral framing per §6 — NEVER «we terminated her». Even hostile termination doesn't surface to customer.

### 10.4 Master's external recourse
Out of scope; HR/legal handles. Platform just captures audit.

### 10.5 Hostile termination shorthand to founder
Q-MO5: if for-cause involves allegations matching review §6.5 sensitive-keyword list (sexual misconduct, harm), founder automatically notified (cannot opt out). Audit captures.

---

## 11. Re-enrollment path

### 11.1 Master can return

Per §2.11 — re-enrollment doesn't restore prior state. Master returning 6 months later:
- Re-onboarded via standard M0-M7 flow
- New `MasterCompensationProfile` set
- Customer reassignment is a new explicit step (no auto-restore)
- Reviews from previous tenure visible to admin; new aggregate starts fresh
- Old AI memory of (customer, master) — depends on data retention §7

### 11.2 Welcome-back communication

```
{{salon_owner}} пригласила вас вернуться в {{salon_name}}. Это новое
рабочее место для приложения — старый профиль сохранён в архиве, но
условия и клиентская база настраиваются заново. {{salon_owner}}
поможет.

[Принять приглашение]   [Отказаться]
```

### 11.3 Customer reaction

If customer's old master returns to salon, customer not auto-notified. Standard booking flow surfaces old master as available again. Customer can choose to book.

---

## 12. Data models

### 12.1 `MasterOffboarding`

```python
class MasterOffboarding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='offboardings')

    TYPE_CHOICES = [
        ('resignation', 'Resignation'),
        ('mutual_agreement', 'Mutual agreement'),
        ('non_renewal', 'Non-renewal'),
        ('for_cause', 'For-cause termination'),
        ('extended_leave_conversion', 'Extended leave conversion'),
    ]
    offboarding_type = models.CharField(max_length=32, choices=TYPE_CHOICES)

    notice_submitted_at = models.DateTimeField()
    notice_end_date = models.DateField()
    days_notice = models.IntegerField()

    master_message_to_admin = models.TextField(blank=True, default='', max_length=1000)
    farewell_message_to_customers = models.TextField(blank=True, default='', max_length=200)

    inheritor_master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    reassignment_pattern = models.CharField(max_length=32, default='named_substitute')
    # named_substitute | pool_rotation | customer_choice

    STATUS_CHOICES = [
        ('proposed', 'Proposed'),
        ('pending_4_eye', 'Pending 4-eye admin (for_cause only)'),
        ('admin_reviewing', 'Admin reviewing'),
        ('notice_active', 'In notice period'),
        ('recalled', 'Master recalled within 7d'),
        ('final_day_today', 'Final day (last day of work)'),
        ('read_only_window', 'Post-offboarding 30-day read-only'),
        ('access_revoked', 'Access revoked'),
        ('disputed', 'Open dispute extends access'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='proposed')

    admin_decided_at = models.DateTimeField(null=True, blank=True)
    admin_first_signer = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_second_signer = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    # for_cause requires both

    for_cause_reason_internal = models.TextField(blank=True, default='', max_length=2000)
    # NEVER customer-facing

    notify_master_via_bot = models.BooleanField(default=True)
    # Admin can set false for «I'll tell in person»

    bookings_in_notice_count = models.IntegerField(default=0)
    bookings_after_notice_count = models.IntegerField(default=0)
    customers_to_reassign_count = models.IntegerField(default=0)

    recalled_at = models.DateTimeField(null=True, blank=True)
    final_day_at = models.DateTimeField(null=True, blank=True)
    read_only_window_ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    founder_notified = models.BooleanField(default=False)
    # Per §10.5 sensitive for_cause

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-notice_submitted_at']),
            Index(fields=['master', '-notice_submitted_at']),
        ]
```

### 12.2 `OffboardingBookingReassignment`

Per-booking reassignment record for post-notice bookings.

```python
class OffboardingBookingReassignment(models.Model):
    offboarding = models.ForeignKey(MasterOffboarding, on_delete=CASCADE, related_name='booking_reassignments')
    booking = models.OneToOneField('booking.Booking', on_delete=CASCADE, related_name='offboarding_reassignment')

    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='+')
    new_master = models.ForeignKey('staff.Master', null=True, on_delete=SET_NULL, related_name='+')

    STATUS_CHOICES = [
        ('customer_pending', 'Awaiting customer decision'),
        ('customer_accepted_new_master', 'Customer accepted'),
        ('customer_chose_other_master', 'Customer chose different master'),
        ('customer_rescheduled', 'Customer rescheduled'),
        ('customer_cancelled', 'Customer cancelled'),
        ('auto_cancelled', 'Auto-cancelled (customer no response)'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='customer_pending')

    customer_notified_at = models.DateTimeField(null=True, blank=True)
    customer_responded_at = models.DateTimeField(null=True, blank=True)
```

### 12.3 `MasterAccessRevocationSchedule`

Per master, scheduled revocation events.

```python
class MasterAccessRevocationSchedule(models.Model):
    offboarding = models.OneToOneField(MasterOffboarding, on_delete=CASCADE, related_name='access_schedule')

    customer_data_access_revoked_at = models.DateTimeField()
    # = final_day_at (immediate)

    earnings_view_revoked_at = models.DateTimeField()
    # = final_day + 30 days (read-only window end)

    export_available_until = models.DateTimeField()
    # = final_day + 30 days

    audit_view_for_master_revoked_at = models.DateTimeField()
    # = final_day + 30 days
```

### 12.4 `OffboardingAuditEvent`

```python
class OffboardingAuditEvent(models.Model):
    offboarding = models.ForeignKey(MasterOffboarding, on_delete=CASCADE, related_name='audit_events')

    EVENT_CHOICES = [
        ('proposed', 'Master proposed offboarding'),
        ('admin_4_eye_approved', '4-eye admin approval'),
        ('admin_approved', 'Admin approved'),
        ('notice_started', 'Notice period started'),
        ('master_recalled', 'Master recalled within 7d'),
        ('inheritor_assigned', 'Inheritor master assigned'),
        ('inheritor_changed', 'Inheritor changed'),
        ('customer_notified', 'Customer notified of reassignment'),
        ('final_day_reached', 'Final day of work'),
        ('read_only_window_started', '30-day read-only began'),
        ('export_requested', 'Master requested data export'),
        ('access_revoked', 'Access revoked'),
        ('founder_notified', 'Founder auto-notified for sensitive'),
        ('disputes_extended_access', 'Open dispute extended access'),
        ('reenrollment_invited', 'Master invited back later'),
    ]
    event = models.CharField(max_length=64, choices=EVENT_CHOICES)
    actor = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict)
```

---

## 13. API contracts

### 13.1 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/master/offboarding` | Submit resignation §4.1 |
| GET | `/api/v1/master/offboarding/current` | View own offboarding state |
| PATCH | `/api/v1/master/offboarding/<id>` | Edit notice details (before admin approves) |
| POST | `/api/v1/master/offboarding/<id>/recall` | Recall within 7d §4.4 |
| POST | `/api/v1/master/offboarding/<id>/assign-inheritor` | Set inheritor §4.3 |
| POST | `/api/v1/master/offboarding/<id>/farewell-message` | Set farewell §4.3 |
| GET | `/api/v1/master/offboarding/<id>/export` | Request data export |
| GET | `/api/v1/master/offboarding/<id>/audit-log` | View own offboarding audit |

### 13.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/admin/offboarding/initiate-for-cause` | Admin-initiated for_cause §5.3 |
| POST | `/api/v1/admin/offboarding/<id>/4-eye-approve` | Second admin signs §5.3 |
| POST | `/api/v1/admin/offboarding/<id>/approve` | Approve non-for_cause |
| POST | `/api/v1/admin/offboarding/<id>/change-inheritor` | Override inheritor |
| POST | `/api/v1/admin/offboarding/<id>/extend-notice` | Lengthen notice |
| POST | `/api/v1/admin/offboarding/<id>/shorten-notice` | Shorten (master agreement) |
| GET | `/api/v1/admin/offboarding/active` | List active in tenant |
| GET | `/api/v1/admin/offboarding/<id>` | Detail |
| POST | `/api/v1/admin/offboarding/<id>/invite-back` | §11.2 |

### 13.3 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/affected-bookings/offboarding` | List bookings affected |
| POST | `/api/v1/customer/booking/<id>/accept-new-master` | Accept inheritor |
| POST | `/api/v1/customer/booking/<id>/choose-different-master` | Pick different |
| POST | `/api/v1/customer/booking/<id>/reschedule-offboarding` | Reschedule |
| POST | `/api/v1/customer/booking/<id>/cancel-offboarding` | Cancel |

### 13.4 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/offboardings/sensitive` | Sensitive for_cause cases §10.5 |
| GET | `/api/v1/founder/offboardings/by-tenant` | Cross-tenant analytics |

### 13.5 Internal cron

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/offboarding/<id>/activate-notice` | Cron: notice starts |
| POST | `/internal/offboarding/<id>/final-day` | Cron: final-day actions |
| POST | `/internal/offboarding/<id>/revoke-access` | Cron: 30-day complete |
| POST | `/internal/offboarding/scan-recall-window` | Cron: 7d recall expiry |

---

## 14. Events emitted

Add to [`event-taxonomy.md`](../policies/event-taxonomy.md) `3.11 master offboarding domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Offboarding proposed | NEW: `offboarding.proposed` | type, notice_days |
| 4-eye approval | NEW: `offboarding.4_eye_approved` | first_signer, second_signer |
| Admin approved | NEW: `offboarding.admin_approved` | |
| Master recalled | NEW: `offboarding.master_recalled` | days_into_notice |
| Inheritor assigned | NEW: `offboarding.inheritor_assigned` | |
| Customer notified | NEW: `offboarding.customer_notified` | booking_id |
| Customer accepted inheritor | NEW: `offboarding.customer_accepted` | |
| Customer cancelled due to offboarding | NEW: `offboarding.customer_cancelled` | |
| Final day reached | NEW: `offboarding.final_day_reached` | |
| Read-only window started | NEW: `offboarding.read_only_started` | |
| Master data export | NEW: `offboarding.data_exported` | format |
| Access revoked | NEW: `offboarding.access_revoked` | |
| Founder notified (sensitive) | NEW: `offboarding.founder_notified` | reason_class |
| Re-enrollment invited | NEW: `offboarding.reenrollment_invited` | months_since_offboarding |

14 NEW events §14.

---

## 15. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Customer message reveals reason | Privacy §2.1 | Neutral «больше не работает» |
| Auto-delete reviews on offboarding | Data integrity §2.5/§9 | Preserve, freeze aggregate |
| Master loses access to own earnings immediately | Trust violation §2.2/§7.2 | 30-day read-only |
| Customer auto-cancelled without choice | Customer autonomy | §6.1 options |
| For-cause without 4-eye | Power abuse §2.12 | Required |
| Hostile customer narrative («terminated for bad work») | Surfaces internal dispute | NEVER |
| Mass-broadcast customer message | Spam §2.10 | Per-affected-customer only |
| Inheritor auto-assigned without master input | Removes master's input | Master proposes; admin reviews |
| Customer's wellness data «inherited» by new master | Privacy boundary §2.4 | NEVER |
| Master can write «truth» about customer for next master in handover note | Boundary creep | 200-char max, customer-positive framing |
| Re-enroll restores all prior state | Trust violation §2.11 | Fresh start |
| Reviews retroactively scrubbed | Data integrity §2.6 | Frozen on date |
| Notice period 0 days for resignation | Customer chaos | 4-week default; case-by-case adjust |
| Founder always sees all offboardings | Privacy creep | Only sensitive §10.5 |
| Admin can recall master after admin approved | Master's right §4.4 | Only master can recall within 7d |
| Master ↔ customer direct chat about offboarding | Bypass mediation | NEVER; AI relays |
| Auto-poach customer to inheritor without consent | Customer autonomy §6.1 | Customer chooses |
| Earnings frozen until disputes close | Cycle disruption | Standard cycle; disputes track separately |
| Master access revoked while dispute open | Master can't participate | Extended §7.2 / §8.2 |
| Customer earlier reviews about master deleted on hostile termination | Data integrity | Never delete |

---

## 16. Acceptance criteria (engineering checklist)

- [ ] 4 models §12 + migration
- [ ] 23 endpoints across 4 roles §13
- [ ] 5 offboarding types §3 fully supported
- [ ] For-cause 4-eye flow §5.3 / §10.2
- [ ] Notice period UX master + admin §4-5
- [ ] Recall within 7d §4.4
- [ ] Inheritor assignment §4.3
- [ ] Reuses substitution customer-reassignment machinery §6.1
- [ ] Customer notification per §6 (NOT mass-broadcast §2.10)
- [ ] Master farewell message §4.3
- [ ] Master read-only 30-day window §4.6 / §7.2
- [ ] Master data export §7.5 / §13.1
- [ ] Final earnings cycle settlement §8
- [ ] Open dispute extends access §8.2
- [ ] Reviews frozen + preserved §9
- [ ] Re-enrollment flow §11
- [ ] Sensitive for-cause founder notify §10.5
- [ ] Cron workers for: notice activate, final day, revoke access, recall expiry §13.5
- [ ] 14 events §14
- [ ] Audit immutable §2.8 + §12.4
- [ ] PII: customer never sees reason §2.1
- [ ] Tests: 5 types e2e / 4-eye + founder auto-notify on sensitive / recall flow / inheritor assignment / customer choices / data export / read-only window enforcement / re-enrollment fresh-state / cross-tenant separation
- [ ] Anti-pattern review §15

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MO1** | Default notice period — 4 weeks fixed or admin-configurable? | 4 weeks for resignation MVP; salon can configure per-tenant default. | Policy + Eng | 🟢 |
| **Q-MO2** | Recall window — 7 days correct? | 7d MVP. Long enough for second thoughts, short enough that planning has begun. | Policy | 🟢 |
| **Q-MO3** | Read-only window — 30 days correct? | 30d MVP. Long enough for data export, short enough to limit risk. | Privacy | 🟢 |
| **Q-MO4** | For-cause 4-eye — 2 admins or admin + founder? | 2 admins minimum; founder can substitute. If only 1 admin in tenant, founder REQUIRED. | Policy | 🔴 PRE-DEPLOY |
| **Q-MO5** | For-cause sensitive auto-founder notify — what triggers? | Per §10.5: sensitive-keyword detection match OR explicit «misconduct» classification by admin. Founder notified, can elect to review. | Policy + Privacy | 🔴 PRE-DEPLOY |
| **Q-MO6** | Master ↔ customer farewell — direct or AI-mediated? | AI-relayed only. Master writes; AI delivers as «{{master}} попросила передать». | UX | 🟢 |
| **Q-MO7** | Inheritor refuse — what then? | Admin reassigns OR escalates to POOL_ROTATION pattern. | Policy | 🟢 |
| **Q-MO8** | Customer can refuse inheritor and stay with offboarded master post-offboarding | NO MVP — once master is offboarded, master is unavailable. Customer can wait for «invite back» Phase 4+. | Policy | 🟢 |
| **Q-MO9** | Multi-tenant master leaves only one tenant — handle independently? | YES. Per §8.6: only that tenant's offboarding occurs. Other tenants unchanged. | Eng | 🟡 |
| **Q-MO10** | Master can withdraw data export request after generating? | NO — export generated, audit row §12.4. Master controls what they do with file. | Privacy | 🟢 |
| **Q-MO11** | Customer's wellness data — what happens to master_id pointers post-offboarding? | Wellness data has NO master_id pointer (customer-only, per [`core-wellness-profile.md`](../policies/core-wellness-profile.md)). N/A. | Privacy | 🟢 |
| **Q-MO12** | Reviews about master post-offboarding visible to admin permanently? | YES — admin sees aggregate frozen, individual reviews retained. Used for hiring/coaching. | Policy | 🟢 |
| **Q-MO13** | Master in HUMAN_LOCKED conversations during offboarding — special handling? | Admin completes those conversations or hands off to other master per conversation-ownership-policy. AI silent. | Policy + Q-MTL11 alignment | 🔴 PRE-DEPLOY |
| **Q-MO14** | Wellness modules subscribed to master_id (e.g., customer's preferred master in their wellness profile) — what happens? | Updated to «no preferred master» on offboarding. Customer can set new preferred. | Privacy + UX | 🟡 |
| **Q-MO15** | Inheritor's view of «inherited customers» analytics — see them as cohort? | NO MVP — privacy. Inheritor sees each customer same as any. Phase 3+ admin analytics if useful. | Privacy | 🟡 |
| **Q-MO16** | Founder analytics on offboardings per tenant — privacy-aggregate scope? | Aggregate only: count by reason, by type, by month. NEVER specific master names cross-tenant. | Privacy | 🟡 |
| **Q-MO17** | Master who is owner-master offboards — what happens to tenant? | Tenant goes into SUSPENDED unless co-owner exists. Per [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md). | Policy | 🟡 |
| **Q-MO18** | Customer reviews edit window past offboarding (within their 7d) — does customer's edit affect frozen aggregate? | NO — aggregate frozen on offboarding date includes review-as-of-that-date. Customer's later edit visible to admin but doesn't change aggregate. | Eng + Privacy | 🟡 |
| **Q-MO19** | Master's read-only window during open dispute — extends 30d window? | YES extended to dispute resolution + 14d. Per §8.2. | Policy | 🟡 |
| **Q-MO20** | Re-enrollment same master same tenant — eligibility window? | NO restrictions. Salon owner's call. Audit captures historical context. | Policy | 🟢 |

---

## 18. Cross-document linkage

- [`master-substitution-handoff.md`](./2026-05-19-master-substitution-handoff.md) — extended-leave-conversion §3.5; reassignment patterns reused §6.1
- [`master-time-off-handoff.md`](./2026-05-19-master-time-off-handoff.md) — boundary at 180 days §3.5
- [`master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md) — final cycle §8
- [`master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md) — reviews frozen §9
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — offboarding banner + dashboard
- [`master-conversational-templates.md`](../policies/master-conversational-templates.md) — farewell touchpoints
- [`booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md) — customer reassignment machinery
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — Q-MO13 HUMAN_LOCKED handling
- [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — voice §2.1
- [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — Q-MO17 owner offboarding
- [`core-wellness-profile.md`](../policies/core-wellness-profile.md) — Q-MO11 wellness data privacy
- [`event-taxonomy.md §3.11`](../policies/event-taxonomy.md) — 14 NEW events §14
- [`../decisions-log.md`](../decisions-log.md) — Q-MO1..Q-MO20

---

## 19. What this unblocks

- **Clean master separations** — no orphaned bookings, no surprised customers
- **Salon HR confidence** — formal flow with audit
- **Founder oversight** — sensitive cases auto-surface
- **Master data rights** — 30-day export window honored
- **Customer continuity** — substitution-handoff machinery reused
- **Re-enrollment possible** — masters can come back
- **Multi-tenant master safety** — leaving one doesn't affect another

## 20. What this does NOT unblock

- ❌ Mass offboarding (whole team) — separate tenant-shutdown
- ❌ Master moving to ANOTHER tenant on platform — Phase 4+
- ❌ Non-compete enforcement
- ❌ Severance accounting
- ❌ HR/legal contracts
- ❌ Tax-final-report automation
- ❌ Customer following master to new salon
- ❌ Skip Q-MO4/Q-MO5 4-eye + founder rules (pre-deploy)
- ❌ Skip Q-MO13 HUMAN_LOCKED handoff (pre-deploy)

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Staff backend lead (offboarding model integration) | ☐ | |
| Mini App frontend (master notice dashboard + admin queue + customer reassignment) | ☐ | |
| AI prompt eng (customer + master Bot DM templates) | ☐ | |
| Substitution steward (reassignment reuse + scope alignment) | ☐ | 🔴 PRE-DEPLOY |
| Earnings steward (§8 final settlement + dispute extension) | ☐ | 🔴 PRE-DEPLOY |
| Reviews steward (§9 freezing + integrity) | ☐ | |
| Conversation ownership steward (Q-MO13) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (§2 + §7 data retention + Q-MO5 sensitive founder notify) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-MO4 + Q-MO5 + Q-MO16 analytics + Q-MO17 owner offboarding) | ☐ | 🔴 PRE-DEPLOY |
| HR-adjacent (Russia labor law alignment on Q-MO1 notice + for-cause processes) | ☐ | RECOMMENDED |
| Accessibility (WCAG 2.2 AA) | ☐ | |

## Last verified
2026-05-19 (initial draft, 5 offboarding types + 4-eye for-cause + 7d recall + 30d read-only + final settlement + re-enrollment — locked)
