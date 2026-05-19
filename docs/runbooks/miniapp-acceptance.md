# Runbook: Customer Mini App — acceptance smoke

> Status: **complete**
> Last exercised: _2026-05-19 (Phase 5 ship)_
> Target completion sprint: _Phase 5_
> Owner: _Customer Mini App (PI track)_

## Purpose

Manual end-to-end walkthrough an operator runs against any Mini App
deploy (dev or prod) to confirm the customer-facing path is intact
before declaring a release green. Catches issues automated tests miss:
real MAX webview render, real haptics/back-button wiring, real
HUMAN_LOCKED handoff visibility in the admin UI.

## Trigger / when to run

- After any merge to `feat/customer-miniapp-*` → `dev` (deploy smoke)
- Before promoting `dev` → `staging`/`prod`
- After server provisioning changes (nginx vhost, gunicorn restart, env update)

## Prerequisites

- Access: a MAX account that has DM'd the dev bot at least once OR is
  willing to trigger lazy-create on first Mini App tap.
- Access: admin console (or `manage.py shell`) on the target env to
  inspect AdminTask + BookingRequest rows.
- Pre-check: `curl https://miniapp-dev.gobeauty.site/ -I` returns 200.
- Pre-check: `curl https://api-dev.gobeauty.site/healthz/` returns
  `{"status":"ok"}`.
- Communication: announce in #ops before running the **rating=2** step
  — it creates a real `TaskType.COMPLAINT` AdminTask the operators will see.

## Step-by-step procedure

### 1. Cold open → Hello → Catalog

1. Force-close MAX or wait > 60 min so initData is fresh.
2. Open the dev bot (Формула тела | тестовый бот), tap the «Старт»
   Mini App button under the chat input.
3. Expect: `Здравствуйте, <имя>!` screen, "Записаться" CTA, nav row
   to "Мои записи" / "Профиль".
4. Tap "Записаться" → catalog loads with 3 services (LPG-массаж,
   Обёртывание, Прессотерапия).

**Server-side check (optional):**
- `nginx access log:` `POST /api/v1/customer/auth/verify HTTP/2.0 200`
- Gunicorn JSON log: `miniapp_api.auth.lazy_register` if first-time user

### 2. Full booking happy path

1. From catalog, tap a service → detail screen with description + price.
2. Tap CTA → master picker.
3. Pick a master → date strip + slot grid.
4. Pick a slot in the next 7 days → confirm screen with summary.
5. Tap "Подтвердить запись" → success screen with booking id snippet.

**Server-side check:**
```
manage.py shell -c "
from apps.booking.models import BookingRequest
b = BookingRequest.all_tenants.order_by('-created_at').first()
print(b.id, b.status, b.booking_source, b.billable, b.billing_reason)
"
# expect: <uuid> confirmed ai_direct True "ai_direct + confirmed: customer-initiated via execute_confirm"
```

### 3. Reschedule from MyVisits

1. Navigate to "Мои записи" → upcoming tab.
2. Tap "Перенести" on the booking just made.
3. Pick a different slot → confirm.
4. Old booking's status flips to `rescheduled`, new row is created
   with `billable=False` (Q12-α).

**Server-side check:**
```
manage.py shell -c "
from apps.booking.models import BookingRequest
for b in BookingRequest.all_tenants.order_by('-created_at')[:2]:
    print(b.status, b.billable, b.billing_reason)
"
# expect: old=rescheduled billable=True; new=confirmed billable=False with "Q12-α reschedule"
```

### 4. Feedback rating=2 → HUMAN_LOCKED

> Coordinate with #ops first — this creates an operator task.

1. To get a "past" booking quickly, hand-edit `visit_at` to 1 hour ago:
   ```
   manage.py shell -c "
   from apps.booking.models import BookingRequest; from django.utils import timezone; from datetime import timedelta
   b = BookingRequest.all_tenants.filter(rating__isnull=True).order_by('-created_at').first()
   b.visit_at = timezone.now() - timedelta(hours=1); b.save(update_fields=['visit_at'])
   print(b.id)"
   ```
2. In Mini App: "Мои записи" → "Прошедшие" → "Оценить визит".
3. Pick **2 stars**, write a short comment, tap "Отправить".
4. Expect: panel "Спасибо за честность — мы передали ваш отзыв… свяжемся".

**Server-side check:**
```
manage.py shell -c "
from apps.booking.models import BookingRequest
from apps.handoff.models import AdminTask
b = BookingRequest.all_tenants.order_by('-feedback_at').first()
print('rating', b.rating, 'feedback_at', b.feedback_at)
t = AdminTask.all_tenants.order_by('-created_at').first()
print('task', t.task_type, t.priority, t.reason[:80])
"
# expect: rating=2; task=complaint priority=high reason contains "[post-visit rating] 2/5"
```

### 5. Profile save + soft-delete (USE A TEST ACCOUNT)

> Only run on a throwaway BotUser — the delete is real, the row is
> scrubbed, the user has to be reactivated by hand to come back.

1. Mini App → "Профиль".
2. Toggle "Промо-предложения" off, type a name in the input.
3. Tap "Сохранить" → CTA disappears, settings persist.
4. (Optional, throwaway only) Scroll to "Приватность" → tap "Удалить
   все мои данные" → modal asks for "УДАЛИТЬ" → type it → tap "Удалить".
5. Reopen Mini App from the bot → Hello screen shows
   "Аккаунт удалён" copy (slug: user_deleted, status 403).

**Server-side check:**
```
manage.py shell -c "
from apps.identity.models import BotUser
u = BotUser.all_tenants.filter(deleted_at__isnull=False).order_by('-deleted_at').first()
print(u.channel_user_id, u.deleted_at, repr(u.client_name), repr(u.phone))
"
# expect: deleted_at set, client_name='' phone=''
```

### 6. BackButton parity

1. Hello (root) → no MAX back button shown in webview header.
2. Catalog → back arrow shown → tap → returns to Hello.
3. Service detail → back → catalog.
4. Master picker → back → service detail.
5. BookingWhen → back → master picker.
6. BookingConfirm → back → BookingWhen.
7. BookingSuccess → back → Hello (route is reset, not history).
8. MyVisits → back → Hello.
9. Profile → back → Hello.
10. Feedback → back → MyVisits (or wherever it was opened from).

If any screen above is **missing** the back button or sends the user
to the wrong place, file a bug — `useBackButton({ onBack: () => navigate(-1) })`
should be present on every non-root screen.

### 7. Force-stale path

1. Open Mini App, then leave it idle in the foreground for 60+ minutes
   (or change device time forward 90 min then reopen).
2. Trigger any API call (tap "Мои записи").
3. Expect: Mini App stays usable while initData is fresh; when MAX
   re-signs (after close/reopen) the new value is accepted.

**Known MAX quirk to verify on first acceptance run:** does the SDK
re-sign `WebApp.initData` automatically when the Mini App regains focus,
or only on full close + reopen? Capture the answer here once
confirmed live. Phase 5 ships with a manual retry button on Hello
that covers either case.

## Communication post-run

- **Pass:** post `✅ acceptance smoke pass on <env>` in #ops with the
  commit SHA + browser + MAX version (from `User-Agent` in nginx log).
- **Fail at step N:** quote the step, the actual vs expected, and the
  nginx access log line. File a Linear issue tagged `miniapp-bug`.

## Rollback

The Mini App is static SPA + Django API; deploys are reversible:

1. SPA: redeploy the previous `dist/` tarball (kept in
   `/home/taximeter/ai-bot-platform-dev/.dist-archive/<sha>/` from the
   previous deploy script run).
2. API: `git checkout <previous-sha> && systemctl restart
   ai-bot-platform-dev.service`.

See [rollback-procedure.md](rollback-procedure.md) for the full flow.
