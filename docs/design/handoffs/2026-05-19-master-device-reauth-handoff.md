# Master Device Loss / Re-Auth — Engineering Handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — edge case but high-impact (master locked out of account = no work, no earnings visibility)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/tenant-as-provider-model.md`](../policies/tenant-as-provider-model.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/master-onboarding-m0-m7.md`](../policies/master-onboarding-m0-m7.md), [`./2026-05-19-master-admin-internal-chat-handoff.md`](./2026-05-19-master-admin-internal-chat-handoff.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md), [`./2026-05-19-master-offboarding-handoff.md`](./2026-05-19-master-offboarding-handoff.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md)

> Master loses phone. Master switches phone number. Master's MAX account compromised. Master is at a customer's appointment and Mini App won't load. Without a smooth re-auth path: master is locked out, customer's booking is in limbo, admin gets emergency call. This handoff specifies the recovery flow.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: master device re-auth is **Ayla Pro** tenant-side flow per [`tenant-as-provider-model §5`](../policies/tenant-as-provider-model.md). MAX account compromise escalates to founder per [`ayla-emergency-fallback-policy §3.4`](../policies/ayla-emergency-fallback-policy.md) `legally_sensitive` tier. Customer-facing impact during master lockout uses Ayla voice (alt-master offer). Deprecated `single-assistant-identity.md` reference removed.

---

## 0. Why this exists

### 0.1 The auth-edge-case gap

Per [`master-onboarding-m0-m7.md §3-4`](../policies/master-onboarding-m0-m7.md): initial auth = magic-link via MAX deep-link. But:
- Magic-link expires after time
- Master loses phone → new device needs auth
- Master changes phone number → linking issue
- MAX account compromised / suspended
- Account locked due to security flag

No spec for any of these. Result: master messages admin via WhatsApp, admin manually re-invites, customer suffers.

### 0.2 The promise

Single source for:
- 6 recovery scenarios §3
- Master-initiated recovery via Mini App or external recovery portal §4
- Admin-assisted recovery §5
- Founder escalation for high-risk scenarios §6
- Multi-device session management §7
- Security: identity verification + audit + rate limits §8
- Account-lock semantics §9
- 2 NEW models, 9 endpoints, 8 events

---

## 1. Scope

### IN
- 6 recovery scenarios §3
- Magic-link re-issue flow §4.1
- Phone number change §4.2
- Lost device / new device login §4.3
- MAX account compromised §4.4
- Account-locked (security flag) §4.5
- Mass-incident (multiple masters at once, e.g., MAX outage) §4.6
- Master-initiated recovery via dedicated recovery URL §4 (no app needed)
- Admin-assisted recovery via Mini App §5
- Founder escalation for high-risk §6
- Multi-device sessions §7 (master can be logged in on 2 devices)
- Security: identity-verification challenges §8.2
- Audit immutable §8.4
- Rate limiting + brute-force protection §8.3
- Account-lock states §9
- 8 NEW events

### OUT
- Customer-side device loss (covered in customer-side scope, separate)
- Admin device loss (separate `admin-device-reauth-policy.md` future)
- Master's customer relationship data recovery — covered by privacy hierarchy (data is server-side, not lost with device)
- Biometric auth / passkey support — Phase 4+
- Hardware token (Yubikey) — out of scope
- SSO integration — out of scope (masters are not enterprise users)
- Master device-binding crypto (signing keys) — Phase 4+ if needed for high-value features
- WebAuthn — Phase 4+
- Anti-fraud ML on login patterns — Phase 4+
- Cross-tenant master device sharing — Phase 4+
- Recovery via email — out of scope (no master email at MVP; MAX-bound)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Master never permanently locked out
- Recovery path always exists
- May require admin or founder intervention for higher-tier
- Worst case: admin re-invites master with new MAX deep-link

### 2.2 Identity verification escalating challenge
Per Q-MD3: challenges escalate with risk:
- Low: «знаете ли вы свой следующий рабочий день?»
- Medium: same + secret check that admin sets
- High: admin video-call OR founder review

### 2.3 Security > UX in edge cases
- 5 failed re-auth attempts in 1 hour → temp lock 15 min (rate limit)
- 10 failures in 24h → account locked, admin notification, founder review
- Brute force suspected → all sessions revoked

### 2.4 Customer never sees raw recovery state
Per [`single-assistant-identity §2.2`](../policies/single-assistant-identity.md): if master is mid-recovery and customer messages, AI handles per HUMAN_SUPERVISED tier (master temporarily unavailable). NEVER «master's account is locked».

### 2.5 Earnings data not deletable via re-auth
Even if account «recovered» = new identity link, earnings history persists. Cannot inject fake re-auth to clean slate (per audit immutability §8.4).

### 2.6 No SMS-OTP MVP
Per existing platform design — MAX deep-link is primary. SMS-OTP introduces extra cost + spoofing risk. Phase 4+ if needed.

### 2.7 Multi-device allowed
Per §7 — master can be on phone + tablet. Each session tracked. Master can revoke from list.

### 2.8 Admin-assisted recovery requires identity check
Admin cannot bypass identity check by clicking «trust me». Even admin recovery has master verification §5.2.

### 2.9 Cross-tenant recovery independent
Master at tenants A + B: device loss = both need recovery, but separately authenticated. Master's MAX identity is platform-wide; tenant access is per-tenant.

### 2.10 Audit trail for every action
- Login attempt
- Failed attempt
- Session created
- Session revoked
- Account lock / unlock
- Admin/founder intervention

### 2.11 No customer impact during master recovery
- Master's upcoming bookings unchanged
- AI handles customer messages per HUMAN_SUPERVISED §2.4
- Admin can step in if recovery > 4h §5.1

### 2.12 Earnings access during recovery
Master can read earnings (read-only) via recovery portal §4.7 even before full session restored. Limits emergency stress.

---

## 3. Six recovery scenarios

### 3.1 MAGIC_LINK_EXPIRED
- Master clicks old link; expired
- Standard low-risk
- Self-service via Mini App «не приходит ссылка» or recovery portal
- Re-issue magic-link
- Standard onboarding identity check (knew their name? worked yesterday?)

### 3.2 PHONE_NUMBER_CHANGED
- Master kept same MAX account but phone number underneath changed
- Medium risk (MAX should still bind)
- If MAX session intact: just re-login via existing MAX
- If MAX requires re-verify: admin-assisted §5

### 3.3 LOST_DEVICE_NEW_DEVICE
- New phone, same MAX account on new device
- Medium risk
- MAX deep-link to new device, magic-link issued, identity challenge
- All existing sessions revoked (security best practice §8.3)

### 3.4 MAX_ACCOUNT_COMPROMISED
- Master suspects MAX account hacked (someone else logged in)
- High risk
- All sessions revoked immediately
- Admin intervention required §5
- Founder optional escalation if cross-tenant attack

### 3.5 ACCOUNT_LOCKED_SECURITY
- Platform-side anti-fraud or abuse signal locked account
- High risk
- Admin can request unlock with master verification
- Founder review per Q-MD5

### 3.6 MASS_INCIDENT
- MAX outage, platform incident, etc.
- Auto-detected by platform monitoring
- Mass-notification to admins/masters
- Recovery batch-processed
- Founder leads communication

### 3.7 Scenario summary matrix

| Scenario | Risk | Master-self | Admin-assisted | Founder | SLA |
|---|---|---|---|---|---|
| MAGIC_LINK_EXPIRED | Low | ✓ | optional | N/A | 5 min |
| PHONE_NUMBER_CHANGED | Medium | partial | ✓ | N/A | 30 min |
| LOST_DEVICE_NEW_DEVICE | Medium | ✓ | optional | N/A | 15 min |
| MAX_ACCOUNT_COMPROMISED | High | N/A | ✓ | optional | 4h |
| ACCOUNT_LOCKED_SECURITY | High | N/A | partial | ✓ | 24h |
| MASS_INCIDENT | Variable | N/A | N/A | ✓ | per-incident |

---

## 4. Master-initiated recovery

### 4.1 Recovery entry points

| Entry | Where | Scenario |
|---|---|---|
| «Не приходит ссылка» on magic-link page | Onboarding | 3.1 |
| «Войти на новом устройстве» on login | Login screen | 3.3 |
| «Что-то не так с аккаунтом» in Mini App settings (if still logged in) | Settings | 3.4 |
| Recovery portal URL `recovery.{{platform}}` | External | Any |
| Bot DM to platform support | MAX | Any (especially when locked out) |

### 4.2 Recovery portal §4.4

Standalone URL (no Mini App required, accessible via any browser):

```
recovery.ai-bot-platform.com
```

```
┌────────────────────────────────────────┐
│ 🔐 Восстановление доступа               │
├────────────────────────────────────────┤
│ Что произошло?                          │
│                                        │
│ ⦿ Не приходит ссылка для входа          │
│ ◯ Не могу войти на новом телефоне       │
│ ◯ Сменил(а) номер                       │
│ ◯ Кажется, кто-то залез в мой аккаунт  │
│ ◯ Аккаунт заблокирован                  │
│                                        │
│ [Дальше]                                │
└────────────────────────────────────────┘
```

### 4.3 Identity challenge per risk

**Low (LINK_EXPIRED):**
- Кем работаете в студии (мастер маникюра, мастер стрижек, etc.)
- Имя salon owner
- Магия-линк отправляется в MAX, если данные совпадают

**Medium (LOST_DEVICE / PHONE_CHANGE):**
- Above + 2 of:
  - Дата последнего рабочего дня
  - Имя последнего клиента (first name only — verification, NOT discloser)
  - Какая выплата была в последнем цикле (rough sum, ±10%)

**High (COMPROMISED / LOCKED):**
- Above + admin attestation OR video call to founder OR ID document (out-of-scope MVP; admin handles via internal-admin-chat) §5

### 4.4 Magic-link re-issue

After identity challenge passes:

```
┌────────────────────────────────────────┐
│ Идентификация подтверждена ✓             │
├────────────────────────────────────────┤
│ Отправили новую ссылку в MAX. Откройте   │
│ MAX и нажмите на сообщение.              │
│                                        │
│ Если ссылка не пришла за 5 минут —      │
│ напишите {{salon_owner}}: {{contact}}    │
│                                        │
│ [Понятно]                                │
└────────────────────────────────────────┘
```

### 4.5 Session creation + audit

New session created with:
- Device fingerprint (browser/UA hash, NOT PII)
- IP address (audit only, not displayed to admin)
- Timestamp
- Recovery scenario classification

### 4.6 Master sees own session list

In Settings → «Безопасность»:

```
┌────────────────────────────────────────┐
│ ← Безопасность                          │
├────────────────────────────────────────┤
│ ── Активные сессии ──                   │
│                                        │
│ 📱 Этот телефон (Android)               │
│ Активна с 19 мая, 10:00                  │
│ Используется сейчас                      │
│                                        │
│ 💻 Браузер на ноутбуке                  │
│ Активна с 12 мая, 14:30                  │
│ Последняя активность: 3 дня назад        │
│ [Выйти на этом устройстве]               │
│                                        │
│ ── Действия ──                           │
│ [Выйти со всех устройств]               │
│ [История входов]                         │
│ [Связаться с поддержкой]                 │
└────────────────────────────────────────┘
```

### 4.7 Read-only earnings during recovery

If master is mid-recovery and identity not fully verified, but recovery portal session exists (limited tier), master can:
- View own earnings cycle preview
- View list of own past payouts
- See own upcoming bookings (read-only)

Cannot:
- Mark booking COMPLETED
- Open earnings dispute (would need full identity)
- Message admin
- Take any action

Reduces panic during recovery («can I see if next payout is coming?»).

### 4.8 Bot DM during locked-out

If master is locked out + has MAX still working:

```
{{master_first_name}}, обнаружил подозрительный вход с другого устройства,
кратко поставил защиту. Если это были вы — пройдите по ссылке, можно
снять.

[Восстановить доступ]

Если это не вы — спокойно, никто не сделал ничего с вашими данными.
Поможем дальше.
```

---

## 5. Admin-assisted recovery

### 5.1 Trigger

- Master messages admin via internal-admin-chat (if logged in) topic `general` + tag «доступ»
- Master messages admin via WhatsApp / phone call (outside platform) → admin uses admin Mini App to assist
- Master in recovery portal §4 needs Medium+ challenge → admin attests
- Admin proactively notices master inactive >24h with bookings due → reaches out

### 5.2 Admin recovery panel

In admin Mini App «Мастера» tab — per master row, «Помочь с доступом» button:

```
┌────────────────────────────────────────┐
│ ← Помочь Анне с доступом                │
├────────────────────────────────────────┤
│ Текущий статус Анны:                     │
│ • Сессии активные: 0                     │
│ • Последний вход: 12 мая (7 дней назад) │
│ • Бронирования сегодня: 4                │
│                                        │
│ Какая ситуация?                          │
│ ⦿ Магия-линк не пришла                   │
│ ◯ Новый телефон                          │
│ ◯ Сменила номер                          │
│ ◯ Кажется кто-то взломал MAX             │
│ ◯ Аккаунт заблокирован системой          │
│                                        │
│ ── Я подтверждаю, что Анна — это Анна ──│
│ Проверила по фото / голосу / лично:     │
│ ☐ Да, это точно она                     │
│                                        │
│ Если высокий риск (взлом, блокировка),  │
│ потребуется founder.                     │
│                                        │
│ [Отправить новую ссылку]                │
│ [Эскалировать к founder]                 │
└────────────────────────────────────────┘
```

Admin's attestation is logged. Master receives Bot DM on action.

### 5.3 Master ↔ admin coordination for MAX_COMPROMISED

If master suspects compromise:
- All master's sessions revoked
- Admin alerted via Mini App + Bot DM
- Admin contacts master via off-platform channel (phone) to verify
- After verification, admin clicks «снять компрометацию» + master goes through high-challenge recovery
- Founder optionally notified §6.3

### 5.4 Account-lock unlock

If platform locked master's account (anti-fraud, abuse signal):
- Admin sees lock reason in Mini App
- Admin can request unlock with attestation
- Founder reviews + approves §6.1
- Master notified

---

## 6. Founder escalation

### 6.1 ACCOUNT_LOCKED_SECURITY

Anti-fraud / abuse triggers lock. Founder must approve unlock per Q-MD5:

```
┌────────────────────────────────────────┐
│ 🔒 Account locked — review              │
├────────────────────────────────────────┤
│ Master: Анна (Salon Натали)             │
│ Locked: 18 May, 14:23                    │
│ Reason: Suspicious login pattern         │
│        (5 IPs in 1h from different      │
│         countries)                       │
│                                        │
│ Admin's attestation:                     │
│ «I verified Anna by phone, she          │
│ confirmed her account was hacked, MAX   │
│ was secured»                             │
│                                        │
│ [Approve unlock]                         │
│ [Require master video-call first]        │
│ [Reject — keep locked]                   │
└────────────────────────────────────────┘
```

### 6.2 MASS_INCIDENT

Platform-wide outage detected. Founder coordinates:
- Notification to all admins via Mini App + Bot DM
- Cross-tenant communication
- Restore plan documented
- Affected masters bulk-recovered after incident resolves

### 6.3 MAX_COMPROMISED with cross-tenant signal

If master's compromise indicates platform-level attack (not just master), founder:
- Reviews logs cross-tenant
- Decides if other masters need preemptive notification
- Audit captures decisions

### 6.4 Audit retention

All founder interventions: 7 years (per attribution + safety policy).

---

## 7. Multi-device sessions

### 7.1 Max 3 active sessions per master
Per Q-MD6: phone + tablet + browser = 3 max. Adding 4th revokes oldest.

### 7.2 Session tracking
- `Session` row per device
- Device fingerprint (UA hash)
- IP (audit only)
- Created/last_used timestamps
- Manual revoke per session

### 7.3 Sessions across tenants
Master at A + B has separate session per tenant (per token scoping). Revoke at tenant A doesn't kill tenant B session.

### 7.4 «Sign out everywhere» action
Master can revoke ALL sessions including current. Forces fresh login.

### 7.5 Idle timeout
- Active session: no idle timeout (Mini App tab open all day OK)
- Background session > 30d no activity: auto-revoked, master must re-login

### 7.6 Suspicious activity auto-revoke
- 5 sessions across 5 different country/IP in 1h → all revoked + lock signal
- Master notified via Bot DM if MAX still accessible

---

## 8. Security details

### 8.1 Magic-link properties
- 24-hour expiration
- Single-use
- 64-byte random token, base64url
- Bound to email/MAX-ID at creation
- Replay-resistant (used token → 410 Gone)

### 8.2 Identity challenge questions
Stored at onboarding, M2-M3 (when master signs up):
- «Кем работаете?» (master's stated role)
- Admin sets 1 secret question + answer at M0 (optional)
- Last booking customer first name (auto-pulled if booking history exists)
- Last payout amount (auto-pulled if cycle history exists)

NEVER asks for sensitive data (password, full name, SSN, etc.).

### 8.3 Rate limiting
- 5 magic-link requests per email per hour (after 5, lockout 1h)
- 10 failed identity challenges per master per 24h (after 10, account locked, admin notified)
- 100 magic-link requests per IP per day (anti-spam)

### 8.4 Audit content
- All login attempts (success + fail)
- Device fingerprint per session
- IP (audit only, never shown to admin/master per Q-MD7 privacy)
- Session revocations + reason
- Account lock/unlock + actor
- Identity challenge attempts + outcome

### 8.5 PII rules
- Per master's data only (cross-master 403)
- Customer data NEVER accessible via re-auth (re-auth doesn't leak customer info; recovery portal §4.7 only shows master's OWN bookings)
- IP addresses NEVER shown to anyone except security review (Q-MD7)

### 8.6 Defense in depth
- Magic-link token rotation
- Session token short-lived (refresh-token pattern)
- MAX OAuth flow per existing onboarding
- HTTPS only
- HSTS

---

## 9. Account-lock states

### 9.1 States

```
[ACTIVE] (normal)
   ↓ suspicious activity
[TEMPORARILY_LIMITED] (rate-limit triggered, 15 min cooldown)
   ↓ continues
[LOCKED_FOR_REVIEW] (admin/founder review required)
   ↓ unlock approved
[ACTIVE]

Or:
[LOCKED_FOR_REVIEW] → [DELETED] (founder + master agree on deletion)
```

### 9.2 LOCKED_FOR_REVIEW UX (master side)

If master tries to log in during LOCKED:

```
┌────────────────────────────────────────┐
│ ⚠ Аккаунт временно заблокирован         │
├────────────────────────────────────────┤
│ Из соображений безопасности доступ      │
│ временно ограничен. {{salon_owner}}     │
│ уведомлена и работает с поддержкой.     │
│                                        │
│ Что вы можете сейчас:                    │
│ ✓ Видеть свой график (только чтение)    │
│ ✓ Видеть последние выплаты              │
│ ✗ Подтверждать новые записи             │
│ ✗ Отвечать клиентам                     │
│                                        │
│ Связь со студией: {{contact}}            │
│                                        │
│ [Понятно]                                │
└────────────────────────────────────────┘
```

### 9.3 During LOCKED admin can still see master's earnings, bookings (for continuity)
Customer continues to be served — admin handles bookings.

### 9.4 Customer experience during LOCKED master
Per [`master-time-off-handoff §7`](./2026-05-19-master-time-off-handoff.md): customer rebooking flow if needed. AI handles per HUMAN_SUPERVISED tier.

---

## 10. Data models

### 10.1 `MasterSession`

```python
class MasterSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='sessions')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    session_token = models.CharField(max_length=128, unique=True)
    # JWT or opaque token; encrypted at rest

    device_fingerprint = models.CharField(max_length=64)
    # SHA256 of UA + screen + locale + tz
    device_label_user_friendly = models.CharField(max_length=100, blank=True, default='')
    # e.g., «Android, Mini App, Москва» — derived, no PII

    ip_audit_only = models.GenericIPAddressField()
    # NEVER shown to user/admin

    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    REVOCATION_CHOICES = [
        ('active', 'Active'),
        ('user_logout', 'User logged out'),
        ('admin_revoked', 'Admin revoked'),
        ('founder_revoked', 'Founder revoked'),
        ('suspicious_activity', 'Suspicious activity'),
        ('idle_timeout', 'Idle 30d+'),
        ('replaced_oldest', 'Replaced — oldest of 3'),
    ]
    status = models.CharField(max_length=32, choices=REVOCATION_CHOICES, default='active')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', 'status']),
            Index(fields=['expires_at']),
        ]
```

### 10.2 `MasterRecoveryAttempt`

```python
class MasterRecoveryAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL, related_name='recovery_attempts')
    # null if identity not yet confirmed during attempt

    SCENARIO_CHOICES = [
        ('magic_link_expired', 'Magic link expired'),
        ('phone_number_changed', 'Phone number changed'),
        ('lost_device_new_device', 'Lost device, new device'),
        ('max_account_compromised', 'MAX account compromised'),
        ('account_locked_security', 'Account locked by security'),
        ('mass_incident', 'Mass incident participant'),
    ]
    scenario = models.CharField(max_length=64, choices=SCENARIO_CHOICES)

    RISK_LEVEL_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    risk_level = models.CharField(max_length=16, choices=RISK_LEVEL_CHOICES)

    challenge_questions_passed = models.IntegerField(default=0)
    challenge_questions_total = models.IntegerField(default=0)

    requires_admin_assist = models.BooleanField(default=False)
    admin_attestation_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_attestation_at = models.DateTimeField(null=True, blank=True)
    admin_attestation_comment = models.TextField(blank=True, default='', max_length=500)

    requires_founder = models.BooleanField(default=False)
    founder_decision = models.CharField(max_length=32, blank=True, default='')
    founder_at = models.DateTimeField(null=True, blank=True)

    STATUS_CHOICES = [
        ('initiated', 'Initiated'),
        ('challenge_pending', 'Challenge in progress'),
        ('admin_assist_required', 'Awaiting admin attestation'),
        ('founder_required', 'Awaiting founder approval'),
        ('completed', 'Completed; new session issued'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired with no action'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='initiated')

    ip_audit_only = models.GenericIPAddressField()
    user_agent_hash = models.CharField(max_length=64)
    # SHA256 of UA — audit only

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['master', '-created_at']),
            Index(fields=['scenario', 'status']),
        ]
```

---

## 11. API contracts

### 11.1 Recovery endpoints (master-initiated)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/recovery/initiate` | none | Start recovery; returns recovery_attempt_id |
| POST | `/api/v1/recovery/<id>/challenge` | recovery_token | Submit challenge answers |
| GET | `/api/v1/recovery/<id>/status` | recovery_token | Check status |
| POST | `/api/v1/recovery/<id>/request-admin-assist` | recovery_token | Escalate to admin |

### 11.2 Recovery endpoints (post-login)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/master/sessions` | master | List own sessions |
| POST | `/api/v1/master/sessions/<id>/revoke` | master | Revoke specific |
| POST | `/api/v1/master/sessions/revoke-all` | master | Revoke all incl current |

### 11.3 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/master-recoveries/queue` | List recovery attempts needing admin |
| POST | `/api/v1/admin/master-recoveries/<id>/attest` | Admin attestation |
| POST | `/api/v1/admin/master-recoveries/<id>/escalate-founder` | Force founder review |
| POST | `/api/v1/admin/masters/<master_id>/lock` | Manually lock account |
| POST | `/api/v1/admin/masters/<master_id>/unlock` | Manually unlock (with master verification) |
| POST | `/api/v1/admin/masters/<master_id>/revoke-all-sessions` | Force logout master |

### 11.4 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/master-recoveries/founder-review` | High-risk queue |
| POST | `/api/v1/founder/master-recoveries/<id>/approve-unlock` | Approve unlock |
| POST | `/api/v1/founder/master-recoveries/<id>/reject` | Reject |
| GET | `/api/v1/founder/mass-incidents/current` | View ongoing |
| POST | `/api/v1/founder/mass-incidents` | Declare incident |
| POST | `/api/v1/founder/mass-incidents/<id>/resolve` | Mark resolved |

### 11.5 Recovery initiate request

```json
{
  "scenario": "lost_device_new_device",
  "self_identified_phone_e164": "+79111234567",
  // OR
  "self_identified_max_username": "@anna_nails"
}
```

System looks up potential matches; doesn't reveal whether match exists (prevents enumeration attack).

### 11.6 Challenge submission

```json
{
  "answers": {
    "salon_owner_name": "Натали",
    "role_at_salon": "мастер маникюра",
    "last_payout_amount_approx": 38000
  }
}
```

Server scores: ≥ 2/3 pass for medium; ≥ 3/3 for high.

---

## 12. Events emitted

Add to [`event-taxonomy.md`](../policies/event-taxonomy.md) `3.13 auth & device domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Recovery initiated | NEW: `master_recovery.initiated` | scenario, risk_level |
| Challenge attempt | NEW: `master_recovery.challenge_attempted` | passed_count, total_count |
| Admin attestation | NEW: `master_recovery.admin_attested` | |
| Founder approval | NEW: `master_recovery.founder_approved` | |
| Recovery completed | NEW: `master_recovery.completed` | scenario, duration_minutes |
| Recovery rejected | NEW: `master_recovery.rejected` | reason |
| Account locked | NEW: `master_account.locked` | reason_class |
| Account unlocked | NEW: `master_account.unlocked` | actor (admin/founder) |

8 NEW events §12.

---

## 13. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Customer sees «master account locked» | §2.4 voice violation | HUMAN_SUPERVISED tier handles |
| Admin can unlock without master verification §5.2 | Bypass | Verification required even for admin |
| Magic-link in plain Bot DM (e.g., chat history persists) | Token leak risk | Time-bound, one-use |
| IP shown to admin in dashboard | Privacy creep §8.5 | Audit-only, founder-only on review |
| Mass unlock (admin clicks «unlock all») | Bypass safety | Per-master |
| Recovery completion DOES NOT revoke prior sessions | Risk of compromise persisting | Per §3.3 revoke all |
| Recovery via SMS-OTP | Spoofing risk §2.6 | MAX deep-link only MVP |
| Master can self-unlock LOCKED_FOR_REVIEW | Defeats lock purpose | Admin/founder only |
| Identity challenge uses customer PII | Privacy leak | NEVER customer data; master's own only |
| Forever-lock (no founder appeal) | Trust violation §2.1 | Always reviewable |
| Founder bypasses audit | Accountability | All actions logged §8.4 |
| Multi-tenant recovery affects all tenants | Per-tenant boundary §2.9 | Each tenant separately authenticated |
| Recovery portal exposes other-master data via enumeration | Attack vector | Don't reveal whether identifier matched §11.5 |
| Auto-approve admin attestation without master independent confirmation | Trust gap §2.8 | Master independently messaged in Bot DM |
| Read-only earnings disabled during recovery | Stress + opacity | §4.7 read-only allowed |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] 2 models §10 + migration
- [ ] 19 endpoints across 4 roles §11
- [ ] 6 recovery scenarios §3 with risk-tiered challenge §4.3
- [ ] Recovery portal accessible via standalone URL §4.2
- [ ] Master-initiated flow §4 with magic-link re-issue
- [ ] Identity challenge stored at M0-M3 onboarding §8.2
- [ ] Admin attestation flow §5.2
- [ ] Founder approval flow §6.1
- [ ] Mass-incident declaration §6.2
- [ ] Multi-device sessions §7 (max 3 + revoke per/all)
- [ ] Rate limiting §8.3 (5 magic-links/h/email, 10 challenges/24h)
- [ ] Brute force auto-lock §7.6
- [ ] Read-only earnings during partial recovery §4.7
- [ ] Account-lock states + UX §9
- [ ] Customer impact mitigation per HUMAN_SUPERVISED §9.4
- [ ] 8 events §12
- [ ] PII rules §8.5 (IP audit-only)
- [ ] Audit immutability §8.4 / §2.5
- [ ] Cross-tenant independence §2.9
- [ ] Tests: 6 scenarios e2e / rate limit triggers / brute force lock / challenge scoring / admin attest / founder approve+reject / mass-incident / multi-device cap / cross-tenant 403 / customer-data-leak attempt blocked / IP not displayed
- [ ] Anti-pattern review §13

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MD1** | Magic-link TTL — 24h or shorter? | 24h MVP. Beauty industry masters check messages frequently. Shorter = annoying. | Security + UX | 🟢 |
| **Q-MD2** | Challenge questions — what counts as «pass» 2/3 medium? | 2/3 for medium, 3/3 for high MVP. Tune based on false-positive/negative data. | Security | 🟡 |
| **Q-MD3** | Risk-tier challenge escalation — automatic or admin opts up? | Automatic by scenario §3.7. Admin can manually escalate (security gut feeling). | Security | 🟢 |
| **Q-MD4** | MASS_INCIDENT — how detected (manual founder OR auto)? | Manual founder declaration MVP. Auto-detection (e.g., > 10 simultaneous recovery requests from MAX-side) Phase 3+. | SRE | 🟡 |
| **Q-MD5** | ACCOUNT_LOCKED_SECURITY founder approval ALWAYS required? | YES for high-risk locks. Admin can recommend unlock; founder approves. Per Q12-δ cohort review for billing-attribution sensitivity. | Founder | 🔴 PRE-DEPLOY |
| **Q-MD6** | Max sessions per master — 3 or different? | 3 MVP (phone + tablet + browser). Adjust if user data shows 4th common. | UX + Security | 🟢 |
| **Q-MD7** | IP shown to ANYONE (security review, master, admin)? | NEVER displayed; founder can see in audit on demand. Master sees only «session from {{location_country}}» (geo-derived, not raw IP). | Privacy | 🟡 |
| **Q-MD8** | Read-only earnings during recovery — full or limited? | Limited to current cycle + last 3 cycles MVP §4.7. Phase 3+ may extend. | Policy | 🟢 |
| **Q-MD9** | Multi-tenant recovery — what if master at A is locked but recovery shouldn't affect B? | Lock is per-tenant. Master uses recovery flow per tenant. MAX identity is shared (platform-wide). Per-tenant session and access. | Eng | 🟡 |
| **Q-MD10** | Magic-link sent via MAX — what if MAX bot blocked / muted by master? | Recovery portal §4.2 still works. Master types phone/MAX-username, system pings via MAX. If MAX unreachable, admin attestation required §5. | UX + Eng | 🟡 |
| **Q-MD11** | Suspicious activity threshold — 5 IPs in 1h vs other? | 5 IPs / 5 countries / 1h MVP §7.6. Tune. | Security | 🟢 |
| **Q-MD12** | Identity question «last payout amount» — what if cycle just started? | Pull from PRIOR cycle. If no prior cycle, this question not asked. Alternative: «last customer first name» which works once master has any work history. | UX + Eng | 🟢 |
| **Q-MD13** | Bot DM «suspicious login detected» §4.8 — sent how soon? | Immediately on detection (anti-fraud signal). Master either confirms or initiates recovery. | UX | 🟢 |
| **Q-MD14** | Account in LOCKED state — earnings cycle continues? | YES — cycle continues per-bookings-completed (admin handles bookings during lock). Master sees read-only post-unlock. | Eng | 🟡 |
| **Q-MD15** | Q-MO13 HUMAN_LOCKED during recovery — same protocol? | YES — admin handles customer-facing during lock. Per-conversation-ownership-policy. | Policy | 🟡 |
| **Q-MD16** | Recovery portal i18n — Russian only or multi-lang? | Russian MVP. EN/UK Phase 3+ for international tenants. | UX | 🟢 |
| **Q-MD17** | Recovery completed — auto-Bot-DM to master AND admin? | Master Bot DM yes; admin only if admin participated. Reduces admin notification fatigue. | UX | 🟢 |
| **Q-MD18** | Multi-tenant master sees session list across tenants OR per tenant? | Per tenant MVP (one tenant context selected). Cross-tenant overview Phase 4+. | UX | 🟢 |
| **Q-MD19** | Wellness data — confirm NEVER accessible via recovery | Confirmed §8.5; tests must enforce. | Privacy | 🔴 PRE-DEPLOY |
| **Q-MD20** | Recovery audit retention — 7 years? | YES — consistent with attribution-policy. | Privacy + Compliance | 🟢 |

---

## 16. Cross-document linkage

- [`master-onboarding-m0-m7.md M0-M2`](../policies/master-onboarding-m0-m7.md) — identity questions stored here at onboarding §8.2
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — Settings → Безопасность section added §4.6
- [`master-admin-internal-chat-handoff.md`](./2026-05-19-master-admin-internal-chat-handoff.md) — admin attestation surfaces via internal chat thread §5.1
- [`master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md) — read-only earnings during recovery §4.7
- [`master-offboarding-handoff.md`](./2026-05-19-master-offboarding-handoff.md) — locked-state interactions
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_SUPERVISED during master lockout §2.4 / §9.4 / Q-MD15
- [`single-assistant-identity.md §2.2`](../policies/single-assistant-identity.md) — customer never sees lock state §2.4
- [`event-taxonomy.md §3.13`](../policies/event-taxonomy.md) — 8 NEW events §12
- [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — tenant lifecycle interactions
- [`../decisions-log.md`](../decisions-log.md) — Q-MD1..Q-MD20

---

## 17. What this unblocks

- **Master psychological safety** — locked out doesn't mean panicked or lost work
- **Admin operational support** — formal flow vs «I'll call you to verify»
- **Founder governance on security** — lock review path
- **Multi-device productivity** — masters can use Mini App + browser
- **Edge case completion** — full master UX coverage with no «but what if» gaps
- **Compliance posture** — audit immutable, retention defined, PII rules clear

## 18. What this does NOT unblock

- ❌ Biometric / passkey login (Phase 4+)
- ❌ SSO
- ❌ Cross-tenant master device sharing
- ❌ ML-based fraud detection
- ❌ Hardware tokens
- ❌ Skip Q-MD5 founder-required-always (pre-deploy)
- ❌ Skip Q-MD19 wellness-block confirmation (pre-deploy)

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Auth backend lead | ☐ | |
| Mini App frontend (Безопасность settings + recovery portal + lock-state screens) | ☐ | |
| Recovery portal frontend (standalone Russian) | ☐ | |
| AI prompt eng (HUMAN_SUPERVISED handling during master lockout) | ☐ | |
| Security review (rate limits, challenge scoring, brute force detection) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (§8.5 IP not displayed + Q-MD19 wellness block + Q-MD7 geo only) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-MD5 founder approval + Q-MD7 IP audit access + Q12-δ cohort interaction) | ☐ | 🔴 PRE-DEPLOY |
| Conversation ownership steward (HUMAN_SUPERVISED during master lockout) | ☐ | |
| SRE (Q-MD4 incident detection + rate limit infra) | ☐ | |
| Accessibility (recovery portal WCAG 2.2 AA, MAX-free fallback) | ☐ | |

## Last verified
2026-05-19 (initial draft, 6 scenarios + risk-tiered challenge + admin attestation + founder review + multi-device + brute force + immutable audit — locked)
