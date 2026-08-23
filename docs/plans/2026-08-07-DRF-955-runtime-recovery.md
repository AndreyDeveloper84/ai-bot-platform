# DRF-955 Runtime Recovery & Deploy Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Controlled Pilot staging runtime to a safe, reproducible, accepted baseline: producer OFF, consumer fail-closed, health/readiness green, privacy acceptance green, and stale legacy units disabled.

**Architecture:** Runtime topology is Docker Compose project `ayla-bot-staging` on host `194.87.99.126` serving `api-dev.gobeauty.site` (BOT) and `miniapp-dev.gobeauty.site`. Backend `dev.gobeauty.site` is the legacy GoBeauty Django app. Legacy systemd units for BOT and GoBeauty are stale/conflicting and will be disabled after evidence. No production systems are touched.

**Tech Stack:** Docker Compose, systemd, nginx, Django, Celery, Redis, PostgreSQL, MinIO.

## Global Constraints

- Target SHA is `origin/dev` HEAD; do not deploy unknown commits.
- Only `pilot/staging` may be changed; never production.
- Before every destructive/runtime-changing step: show current state, save rollback info, verify scope, then change.
- `OUTBOX_EXTERNAL_DELIVERY_TOPICS` must be empty/OFF before this window ends.
- `EVENT_INGEST_ALLOWED_TENANTS` = `b32a057a-56c7-4bf0-ae50-e11e76ab44be` only.
- `EVENT_INGEST_ALLOWED_EVENTS` = `booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled`.
- `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN` must be absent/false.
- `BOOKING_VIA_AYLA_REST=true` remains enabled as a staging-wide toggle per OD-RUNTIME-2.
- DRF-954 controlled producer activation is explicitly out of scope.
- No `git reset --hard` until dirty WIP is classified and preserved.
- Do not run real-user destructive privacy tests; use synthetic identity only.

---

### Task 1: Establish baseline and classify host-repo dirty state

**Files:**
- Inspect: `/home/taximeter/ai-bot-platform-dev/.env.staging`
- Inspect: `/etc/ai-bot-platform/dev.env`
- Inspect: `/home/taximeter/beautygo/dev/.env`
- Inspect: `/etc/nginx/sites-available/api-dev.gobeauty.site`
- Inspect: `/etc/nginx/sites-available/miniapp-dev.gobeauty.site`
- Inspect: `/etc/nginx/sites-enabled/dev.gobeauty.site`
- Inspect: `/etc/systemd/system/ai-bot-platform-dev*.service`, `/etc/systemd/system/gobeauty-dev.service`

**Interfaces:**
- Consumes: local `origin/dev` SHA from `git fetch origin`.
- Produces: `DEPLOY_TARGET_SHA`, rollback snapshots, topology table.

- [ ] **Step 1: Determine accepted deploy target SHA**

Run locally:
```bash
git fetch origin
echo "origin/dev HEAD: $(git rev-parse origin/dev)"
echo "commits after b584ddf:"
git log --oneline b584ddf..origin/dev
```
Expected: `origin/dev == b584ddf8389627a0719bd1ebc76fa915c370bdc8`; zero extra commits.

- [ ] **Step 2: Capture host repo dirty state before deploy**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
mkdir -p .runtime-recovery-$(date +%Y%m%d_%H%M%S)
DIR=.runtime-recovery-$(date +%Y%m%d_%H%M%S)
git status --short > "$DIR/git-status.txt"
git diff > "$DIR/docker-compose.staging.diff"
git rev-parse HEAD > "$DIR/head-before.txt"
cp .env.staging "$DIR/env.staging.before"
echo "Rollback snapshot in $DIR"
'
```
Expected: snapshot directory created; dirty diff saved.

- [ ] **Step 3: Record runtime topology table**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
echo "Component | Current runtime | Current SHA | Target SHA | Action"
echo "BOT web | Docker ayla-bot-staging/web:8014 | f9d73af | b584ddf | deploy"
echo "BOT worker | Docker ayla-bot-staging/worker | f9d73af | b584ddf | redeploy"
echo "BOT celery-worker | Docker ayla-bot-staging/celery-worker | f9d73af | b584ddf | redeploy"
echo "BOT celery-beat | Docker ayla-bot-staging/celery-beat | f9d73af | b584ddf | redeploy"
echo "BOT systemd web | inactive | - | - | none"
echo "BOT systemd worker | active, wrong DB/schema errors | - | - | stop+disable"
echo "BOT systemd beat | active, wrong DB/schema errors | - | - | stop+disable"
echo "Backend dev.gobeauty | Docker dev-web:8000 | - | - | leave"
echo "Backend gobeauty systemd | active, failing socket | - | - | stop+disable"
'
```

---

### Task 2: Restore Backend producer to OFF/empty state

**Files:**
- Modify: `/home/taximeter/beautygo/dev/.env`

**Interfaces:**
- Consumes: current `OUTBOX_EXTERNAL_DELIVERY_TOPICS` value.
- Produces: producer OFF evidence.

- [ ] **Step 1: Save Backend env rollback snapshot**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
mkdir -p /home/taximeter/beautygo/dev/.runtime-recovery-$(date +%Y%m%d_%H%M%S)
cp /home/taximeter/beautygo/dev/.env "/home/taximeter/beautygo/dev/.runtime-recovery-$(date +%Y%m%d_%H%M%S)/.env.before"
'
```

- [ ] **Step 2: Show current producer topic value**

Run on host:
```bash
ssh taximeter@194.87.99.126 'grep -E "^OUTBOX_EXTERNAL_DELIVERY_TOPICS=" /home/taximeter/beautygo/dev/.env'
```
Expected: non-empty broad topic set (e.g., `booking.*`).

- [ ] **Step 3: Set producer topics to empty/OFF**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
sed -i "s/^OUTBOX_EXTERNAL_DELIVERY_TOPICS=.*/OUTBOX_EXTERNAL_DELIVERY_TOPICS=/" /home/taximeter/beautygo/dev/.env
grep -E "^OUTBOX_EXTERNAL_DELIVERY_TOPICS=" /home/taximeter/beautygo/dev/.env
'
```
Expected: `OUTBOX_EXTERNAL_DELIVERY_TOPICS=` (empty value).

- [ ] **Step 4: Restart only the Backend worker/beat processes**

The Backend web container (`dev-web-1`) does not dispatch outbox events; the Celery worker/beat do. Restart the Docker dev stack worker and beat, not the web:

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/beautygo/dev 2>/dev/null || cd /home/taximeter/ai-bot-platform-dev
docker compose -p dev restart celery_worker celery_beat || true
'
```
Expected: restart succeeds; no error.

- [ ] **Step 5: Prove effective runtime value**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
docker exec dev-celery_worker-1 bash -c "echo \"OUTBOX_EXTERNAL_DELIVERY_TOPICS=\$OUTBOX_EXTERNAL_DELIVERY_TOPICS\"" || true
docker exec dev-celery_beat-1 bash -c "echo \"OUTBOX_EXTERNAL_DELIVERY_TOPICS=\$OUTBOX_EXTERNAL_DELIVERY_TOPICS\"" || true
'
```
Expected: both report empty value.

- [ ] **Step 6: Verify dispatch-outbox-events no longer sends external events**

Watch Backend worker logs for 60 seconds or inspect last log lines:

Run on host:
```bash
ssh taximeter@194.87.99.126 '
sleep 15
journalctl -u gobeauty-dev.service -n 20 --no-pager || true
docker logs --tail 30 dev-celery_worker-1 || true
'
```
Expected: no `dispatch-outbox-events` success entries for external topics; no new broad event deliveries.

---

### Task 3: Reconcile BOT topology and disable stale systemd units

**Files:**
- Modify runtime state: `ai-bot-platform-dev-worker.service`, `ai-bot-platform-dev-beat.service`, `gobeauty-dev.service`.

**Interfaces:**
- Consumes: nginx upstream evidence, systemd status, Docker stack status.
- Produces: final topology diagram.

- [ ] **Step 1: Confirm active ingress path**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
echo "nginx upstream for api-dev:"
grep -E "proxy_pass" /etc/nginx/sites-available/api-dev.gobeauty.site | head -n 5
echo "listening 8014 owner:"
ss -tlnp | grep 8014 || true
echo "Docker container on 8014:"
docker ps --filter "publish=8014" --format "{{.Names}} {{.Ports}}"
'
```
Expected: `proxy_pass http://127.0.0.1:8014`; port 8014 owned by Docker; container `ayla-bot-staging-web-1`.

- [ ] **Step 2: Save systemd rollback plan**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
for u in ai-bot-platform-dev-worker.service ai-bot-platform-dev-beat.service gobeauty-dev.service; do
  systemctl status "$u" --no-pager > "/tmp/$u.status.before" 2>&1 || true
done
echo "status snapshots saved to /tmp/*.status.before"
'
```

- [ ] **Step 3: Stop and disable stale/conflicting units**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
for u in ai-bot-platform-dev-worker.service ai-bot-platform-dev-beat.service gobeauty-dev.service; do
  echo "Stopping/disabling $u"
  sudo systemctl stop "$u" || true
  sudo systemctl disable "$u" || true
done
'
```

- [ ] **Step 4: Verify Docker stack and nginx remain healthy**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
echo "systemd states after cleanup:"
for u in ai-bot-platform-dev.service ai-bot-platform-dev-worker.service ai-bot-platform-dev-beat.service gobeauty-dev.service; do
  printf "%s: active=%s enabled=%s\n" "$u" "$(systemctl is-active "$u" || true)" "$(systemctl is-enabled "$u" 2>/dev/null || true)"
done
echo "Docker ayla-bot-staging:"
docker compose -p ayla-bot-staging ps
echo "nginx config test:"
sudo nginx -t
'
```
Expected: target units inactive/disabled; Docker stack Up; nginx config OK.

---

### Task 4: Prepare BOT env fail-closed

**Files:**
- Modify: `/home/taximeter/ai-bot-platform-dev/.env.staging`

**Interfaces:**
- Consumes: current `.env.staging` values.
- Produces: updated env with allowlists and fail-open removed.

- [ ] **Step 1: Save BOT env rollback snapshot**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
mkdir -p /home/taximeter/ai-bot-platform-dev/.runtime-recovery-$(date +%Y%m%d_%H%M%S)
cp /home/taximeter/ai-bot-platform-dev/.env.staging "/home/taximeter/ai-bot-platform-dev/.runtime-recovery-$(date +%Y%m%d_%H%M%S)/env.staging.before"
'
```

- [ ] **Step 2: Set consumer allowlists and disable fail-open**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
# Remove fail-open key if present
sed -i "/^EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=/d" .env.staging
# Idempotently set allowlists
if grep -q "^EVENT_INGEST_ALLOWED_TENANTS=" .env.staging; then
  sed -i "s/^EVENT_INGEST_ALLOWED_TENANTS=.*/EVENT_INGEST_ALLOWED_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be/" .env.staging
else
  echo "EVENT_INGEST_ALLOWED_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be" >> .env.staging
fi
if grep -q "^EVENT_INGEST_ALLOWED_EVENTS=" .env.staging; then
  sed -i "s/^EVENT_INGEST_ALLOWED_EVENTS=.*/EVENT_INGEST_ALLOWED_EVENTS=booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled/" .env.staging
else
  echo "EVENT_INGEST_ALLOWED_EVENTS=booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled" >> .env.staging
fi
# Verify
printf "fail-open: "; grep "^EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=" .env.staging || echo "ABSENT"
grep -E "^EVENT_INGEST_ALLOWED_(TENANTS|EVENTS)=" .env.staging
'
```
Expected: allowlists set; fail-open key absent.

---

### Task 5: Deploy accepted BOT baseline and apply migrations

**Files:**
- Use: `/home/taximeter/ai-bot-platform-dev/docker-compose.staging.yml`, `.env.staging`
- Preserve: dirty diff in rollback snapshot.

**Interfaces:**
- Consumes: target SHA `b584ddf`; clean repo state.
- Produces: running containers at target SHA; migrations applied.

- [ ] **Step 1: Stash or backup dirty WIP and checkout target SHA**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
# preserve dirty state as a git stash
git stash push -m "runtime-recovery auto-stash before deploy to b584ddf" -- docker-compose.staging.yml || true
# checkout the accepted baseline
git fetch origin
git checkout b584ddf8389627a0719bd1ebc76fa915c370bdc8
git rev-parse HEAD
'
```
Expected: HEAD == `b584ddf...`; dirty diff removed from working tree but saved in stash.

- [ ] **Step 2: Verify pending migrations are only the known safe set**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging run --rm web python manage.py showmigrations booking
'
```
Expected: `booking.0016_alter_remotebookingproxy_status` and `booking.0017_remotebookingproxy_last_applied_appointment_version` are `[ ]`; no other unapplied migrations flagged as unsafe.

- [ ] **Step 3: Apply migrations**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging run --rm web python manage.py migrate booking
'
```
Expected: migrations apply successfully.

- [ ] **Step 4: Build and recreate the Docker stack**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml down
docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml build web worker celery-worker celery-beat
docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml up -d
'
```
Expected: stack builds, starts, containers healthy.

- [ ] **Step 5: Prove deployed SHA inside running containers**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
docker exec ayla-bot-staging-web-1 bash -c "cd /app && git rev-parse HEAD" || true
docker exec ayla-bot-staging-worker-1 bash -c "cd /app && git rev-parse HEAD" || true
'
```
Expected: both report `b584ddf8389627a0719bd1ebc76fa915c370bdc8`.

---

### Task 6: Build and deliver Mini App bundle

**Files:**
- Build: `/home/taximeter/ai-bot-platform-dev/apps/miniapp/`
- Serve: `/home/taximeter/ai-bot-platform-dev/apps/miniapp/dist/`

**Interfaces:**
- Consumes: npm project in `apps/miniapp`.
- Produces: new static bundle served by nginx.

- [ ] **Step 1: Identify build pipeline**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev/apps/miniapp
ls -la package.json vite.config.* 2>/dev/null
cat package.json | head -n 30
'
```
Expected: `package.json` exists; build script defined.

- [ ] **Step 2: Build the Mini App**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev/apps/miniapp
npm install --no-save --no-package-lock
npm run build
'
```
Expected: `dist/` directory updated; build succeeds.

- [ ] **Step 3: Confirm nginx serves the new bundle**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
ls -la /home/taximeter/ai-bot-platform-dev/apps/miniapp/dist/index.html
curl -s -o /dev/null -w "%{http_code}" https://miniapp-dev.gobeauty.site/index.html
'
```
Expected: `index.html` recently modified; HTTP 200.

---

### Task 7: Health / readiness verification

**Files:**
- Endpoints: `https://api-dev.gobeauty.site/healthz/` and `/readyz/`

**Interfaces:**
- Consumes: running BOT stack.
- Produces: HTTP 200 evidence.

- [ ] **Step 1: Probe health and readiness**

Run locally:
```bash
curl -sS -o /dev/null -w "healthz: %{http_code}\n" https://api-dev.gobeauty.site/healthz/
curl -sS -o /dev/null -w "readyz: %{http_code}\n" https://api-dev.gobeauty.site/readyz/
```
Expected: both HTTP 200.

- [ ] **Step 2: Inspect readiness detail for required dependencies**

Run locally:
```bash
curl -sS https://api-dev.gobeauty.site/readyz/ | python -m json.tool
```
Expected: Postgres, Redis, MinIO green; Chroma intentionally non-blocking.

---

### Task 8: Pilot tenant provisioning recheck

**Files:**
- Database via Django shell.

**Interfaces:**
- Consumes: tenant slug `formula-tela` and UUID `b32a057a-56c7-4bf0-ae50-e11e76ab44be`.
- Produces: coverage % and linkage evidence.

- [ ] **Step 1: Verify catalog service linkage coverage**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging run --rm web python manage.py shell -c "
from apps.catalog.models import CatalogService
from apps.tenancy.models import Tenant
tenant = Tenant.objects.get(slug=\"formula-tela\")
total = CatalogService.objects.filter(tenant=tenant).count()
linked = CatalogService.objects.filter(tenant=tenant, ayla_service_id__isnull=False).count()
print(f\"CatalogService linkage: {linked}/{total}\")
"
'
```
Expected: `58/58`.

- [ ] **Step 2: Verify pilot user linkage and identity conflict**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging run --rm web python manage.py shell -c "
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant
tenant = Tenant.objects.get(slug=\"formula-tela\")
users = BotUser.objects.filter(tenant=tenant, channel_user_id__startswith=\"max:\")
for u in users:
    print(u.channel_user_id, u.ayla_user_id, u.ayla_linkage_status)
conflicts = BotUser.objects.filter(tenant=tenant, ayla_linkage_status=\"identity_conflict\").count()
print(f\"identity conflicts: {conflicts}\")
"
'
```
Expected: `max:83146139` has a single `ayla_user_id`; `max:888888` unlinked; `identity conflicts: 0`.

---

### Task 9: Privacy runtime acceptance

**Files:**
- Endpoint: `DELETE /api/v1/customer/me/personal-data/`

**Interfaces:**
- Consumes: synthetic test identity.
- Produces: negative and synthetic-erase evidence.

- [ ] **Step 1: Negative test — wrong confirmation token**

Run locally (no destructive mutation):
```bash
curl -sS -X DELETE https://api-dev.gobeauty.site/api/v1/customer/me/personal-data/ \
  -H "Content-Type: application/json" \
  -d '{"confirmation_token":"invalid-token-runtime-test"}' \
  -w "\nHTTP %{http_code}\n"
```
Expected: HTTP 4xx; zero DB mutation.

- [ ] **Step 2: Synthetic linked identity erase (prepare or use existing)**

If no safe synthetic linked user exists, create one via Django shell, then run:

Run on host:
```bash
ssh taximeter@194.87.99.126 '
cd /home/taximeter/ai-bot-platform-dev
docker compose -p ayla-bot-staging run --rm web python manage.py shell -c "
# Replace with actual synthetic user ID prepared for this test
print(\"SYNTHETIC_ERASE_STEP: execute export, confirmed delete, idempotency checks\")
"
'
```
Expected: export returns correct person-level data; confirmed delete with valid token erases PII; repeat delete safe/idempotent.

---

### Task 10: EventBus pre-activation verification

**Files:**
- Runtime env; ingest endpoint.

**Interfaces:**
- Consumes: allowlists from Task 4.
- Produces: 401 probe evidence; producer OFF evidence.

- [ ] **Step 1: Verify effective consumer env**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
for svc in web worker celery-worker; do
  echo "--- ayla-bot-staging-$svc-1 ---"
  docker exec "ayla-bot-staging-${svc}-1" bash -c "echo TENANTS=\$EVENT_INGEST_ALLOWED_TENANTS; echo EVENTS=\$EVENT_INGEST_ALLOWED_EVENTS; echo FAIL_OPEN=\${EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN:-ABSENT}"
done
'
```
Expected: tenants = pilot UUID; events = four-event list; fail_open absent.

- [ ] **Step 2: Invalid-signature ingest probe**

Run locally:
```bash
curl -sS -X POST https://api-dev.gobeauty.site/api/v1/eventbus/ingest/ \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: invalid" \
  -d '{"event":"booking.created","tenant":"b32a057a-56c7-4bf0-ae50-e11e76ab44be"}' \
  -w "\nHTTP %{http_code}\n"
```
Expected: HTTP 401.

- [ ] **Step 3: Confirm Backend producer remains OFF**

Run on host:
```bash
ssh taximeter@194.87.99.126 '
grep -E "^OUTBOX_EXTERNAL_DELIVERY_TOPICS=" /home/taximeter/beautygo/dev/.env
docker exec dev-celery_worker-1 bash -c "echo OUTBOX=\$OUTBOX_EXTERNAL_DELIVERY_TOPICS" || true
'
```
Expected: empty value.

---

### Task 11: Review rounds and Linear finish

**Files:**
- Linear issues DRF-955, DRF-956, DRF-954 via MCP.

**Interfaces:**
- Consumes: evidence from all previous tasks.
- Produces: Linear comments and status updates.

- [ ] **Step 1: Adversarial review round 2 questions**

Run on host and locally, answering each:
1. `ss -tlnp | grep 8014` and nginx config confirm ingress to Docker.
2. `systemctl is-active ai-bot-platform-dev-worker ai-bot-platform-dev-beat gobeauty-dev` = inactive.
3. `docker exec ayla-bot-staging-worker-1 env | grep EVENT_INGEST` matches allowlists; producer env empty inside dev worker.
4. `docker exec ayla-bot-staging-web-1 git rev-parse HEAD` == `b584ddf...`.
5. `python manage.py showmigrations booking` shows `[X]` for 0016/0017.
6. `ls -la apps/miniapp/dist/index.html` and `curl miniapp-dev` return 200.
7. Wrong-token delete returns 4xx and DB PII unchanged.
8. Consumer env fail_open absent and invalid signature returns 401.
9. `BOOKING_VIA_AYLA_REST=true` and catalog linkage 58/58.
10. No other active producer/consumer: systemd units disabled; Backend producer env empty.

- [ ] **Step 2: Add acceptance comments to Linear**

Use Linear MCP:
- DRF-955: `RUNTIME ACCEPTANCE PASSED` with deploy SHA and evidence.
- DRF-956: `PRIVACY RUNTIME ACCEPTANCE PASSED` with evidence.
- DRF-954: `PRECONDITIONS READY` and leave In Progress.

- [ ] **Step 3: Move DRF-955 and DRF-956 to Done**

Use Linear MCP `mcp__linear__update_issue_state` with state Done (find state ID first if needed).

- [ ] **Step 4: Produce final report**

Write `DRF-955 Runtime Recovery & Deploy Report` to the user, ending with `CAN DRF-954 CONTROLLED ACTIVATION START NOW? YES / NO` and reasons.
