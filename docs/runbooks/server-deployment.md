# ai-bot-platform server deployment runbook

**Target:** `app.penza.taxi` (1.8GB RAM, 50GB disk, taximeter user, sudo via masterkey).
**Strategy:** logical isolation in host Postgres+Redis (RAM-constrained, no new containers).
**Owner:** Phase 0 infra deployment.

---

## 1. Layout decision

```
/home/taximeter/
├── ai-bot-platform/                  ← PROD (deployed from main branch)
│   ├── .venv/                        ← Python 3.12 venv
│   ├── apps/, config/, manage.py
│   ├── apps/miniapp/dist/            ← Vite build artifact
│   └── staticfiles/                  ← collectstatic output
├── ai-bot-platform-dev/              ← DEV (deployed from dev branch)
│   ├── .venv/
│   └── ...
└── (mysite/, beautygo/ untouched until cutover)

/etc/ai-bot-platform/
├── prod.env                          ← chmod 640 root:taximeter
└── dev.env

/etc/systemd/system/
├── ai-bot-platform-prod.service       (= web on :8013)
├── ai-bot-platform-prod-worker.service
├── ai-bot-platform-prod-beat.service
├── ai-bot-platform-dev.service        (= web on :8014)
├── ai-bot-platform-dev-worker.service
└── ai-bot-platform-dev-beat.service

/etc/nginx/sites-enabled/
├── api.gobeauty.site                 → 127.0.0.1:8013
├── api-dev.gobeauty.site             → 127.0.0.1:8014
├── miniapp.gobeauty.site             → static dist/
└── miniapp-dev.gobeauty.site         → static dist/
```

---

## 2. Pre-flight (do once, BEFORE first deploy)

### 2.1 DNS

Create A records pointing at `app.penza.taxi` IP:
- `api.gobeauty.site`
- `api-dev.gobeauty.site`
- `miniapp.gobeauty.site`
- `miniapp-dev.gobeauty.site`

Wait for propagation (`dig +short api-dev.gobeauty.site`).

### 2.2 Postgres roles + databases

```bash
ssh taximeter@app.penza.taxi
# Edit infra/postgres/create-platform-roles.sql first — replace
# CHANGE_ME_*_PASSWORD with strong unique values. Save passwords
# into 1Password before pasting.

sudo -u postgres psql -f /tmp/create-platform-roles.sql
```

Verify:
```bash
sudo -u postgres psql -c "\l ai_bot_platform_prod"
sudo -u postgres psql -c "\du ai_bot_platform_dev"
```

### 2.3 Env files

```bash
sudo mkdir -p /etc/ai-bot-platform
sudo cp infra/env/dev.env.example /etc/ai-bot-platform/dev.env
sudo cp infra/env/prod.env.example /etc/ai-bot-platform/prod.env
sudo chmod 640 /etc/ai-bot-platform/*.env
sudo chown root:taximeter /etc/ai-bot-platform/*.env

# Edit each — fill CHANGE_ME_* values from 1Password.
sudo vi /etc/ai-bot-platform/dev.env
sudo vi /etc/ai-bot-platform/prod.env
```

**Critical fills:**
- `DJANGO_SECRET_KEY` → `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `DB_PASSWORD` → from §2.2 step
- `MAX_BOT_TOKEN` → copy from existing `/home/taximeter/mysite/formula_tela{,_dev}/.env`
- `MAX_WEBHOOK_SECRET` → keep current (cutover reuses)
- `OPENAI_API_KEY` → 1Password

### 2.4 Directories + clone

```bash
cd /home/taximeter
git clone -b dev https://github.com/AndreyDeveloper84/ai-bot-platform.git ai-bot-platform-dev
git clone -b main https://github.com/AndreyDeveloper84/ai-bot-platform.git ai-bot-platform
# Initial PROD clone can wait — provision DEV first, validate, then PROD.

# Python venvs
cd /home/taximeter/ai-bot-platform-dev
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .  # or pip install -r requirements.txt
```

### 2.5 Apply migrations + collect static

```bash
cd /home/taximeter/ai-bot-platform-dev
set -a; source /etc/ai-bot-platform/dev.env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy
```

### 2.6 Frontend build

```bash
cd /home/taximeter/ai-bot-platform-dev/apps/miniapp
npm ci
npm run build
# Produces dist/. nginx serves directly.
```

### 2.7 Systemd units

```bash
# Render templates (replace placeholders)
ENV=dev
DEPLOY_PATH=/home/taximeter/ai-bot-platform-dev
ENV_FILE=/etc/ai-bot-platform/dev.env
PORT=8014
WORKERS=2

for svc in web worker beat; do
    sudo sed -e "s|{ENV}|${ENV}|g" \
             -e "s|{DEPLOY_PATH}|${DEPLOY_PATH}|g" \
             -e "s|{ENV_FILE}|${ENV_FILE}|g" \
             -e "s|{GUNICORN_PORT}|${PORT}|g" \
             -e "s|{WORKERS}|${WORKERS}|g" \
             infra/systemd/ai-bot-platform-${svc}.service.template \
             | sudo tee /etc/systemd/system/ai-bot-platform-${ENV}-${svc}.service > /dev/null
done

# Rename ai-bot-platform-dev-web.service → ai-bot-platform-dev.service (web is the default name)
sudo mv /etc/systemd/system/ai-bot-platform-dev-web.service /etc/systemd/system/ai-bot-platform-dev.service

sudo systemctl daemon-reload
sudo systemctl enable ai-bot-platform-dev{,-worker,-beat}
sudo systemctl start ai-bot-platform-dev{,-worker,-beat}

# Verify
sudo systemctl status ai-bot-platform-dev
curl -fsS http://127.0.0.1:8014/readyz/  # → 200
```

### 2.8 Nginx vhosts

```bash
SUB=api-dev.gobeauty.site
DEPLOY_PATH=/home/taximeter/ai-bot-platform-dev
sudo sed -e "s|{SUBDOMAIN}|${SUB}|g" \
         -e "s|{GUNICORN_PORT}|8014|g" \
         -e "s|{DEPLOY_PATH}|${DEPLOY_PATH}|g" \
         infra/nginx/ai-bot-platform-api.conf.template \
         | sudo tee /etc/nginx/sites-available/${SUB} > /dev/null
sudo ln -sf /etc/nginx/sites-available/${SUB} /etc/nginx/sites-enabled/

# Get SSL cert. Certbot reads the nginx config and adjusts.
sudo certbot --nginx -d ${SUB} --non-interactive --agree-tos -m admin@gobeauty.site

# Same for Mini App
SUB=miniapp-dev.gobeauty.site
DIST=/home/taximeter/ai-bot-platform-dev/apps/miniapp/dist
sudo sed -e "s|{SUBDOMAIN}|${SUB}|g" -e "s|{DIST_PATH}|${DIST}|g" \
         infra/nginx/miniapp.conf.template \
         | sudo tee /etc/nginx/sites-available/${SUB} > /dev/null
sudo ln -sf /etc/nginx/sites-available/${SUB} /etc/nginx/sites-enabled/
sudo certbot --nginx -d ${SUB} --non-interactive --agree-tos -m admin@gobeauty.site

sudo nginx -t && sudo systemctl reload nginx

# Final smoke
curl -fsS https://api-dev.gobeauty.site/readyz/  # → 200
curl -fsS https://miniapp-dev.gobeauty.site/     # → HTML page
```

---

## 3. PROD deployment

Identical to §2 but:
- branch=`main` clone
- env file=`/etc/ai-bot-platform/prod.env`
- port=`8013`, workers=`3`
- subdomains: `api.gobeauty.site` + `miniapp.gobeauty.site`
- systemd: `ai-bot-platform-prod{,-worker,-beat}`

**Do AFTER dev is validated for ≥7 days under load.**

---

## 4. Routine deploys (after initial provisioning)

```bash
# Pull + restart
ssh taximeter@app.penza.taxi
cd /home/taximeter/ai-bot-platform-dev
git fetch origin
git checkout dev && git pull origin dev

# Backend
.venv/bin/pip install -e .  # in case deps changed
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart ai-bot-platform-dev{,-worker,-beat}

# Frontend
cd apps/miniapp
npm ci  # if package-lock changed
npm run build
# dist/ is served live by nginx — no nginx restart needed
```

---

## 5. Rollback (if deploy broke things)

```bash
cd /home/taximeter/ai-bot-platform-dev
git log --oneline -5  # find last-good sha
git checkout <last-good-sha>
.venv/bin/python manage.py migrate  # rolls back if migrations down-applicable
sudo systemctl restart ai-bot-platform-dev{,-worker,-beat}
```

For migration rollback when down-not-supported: restore Postgres
backup. Backup procedure: `docs/runbooks/disaster-recovery.md`.

---

## 6. Smoke checklist post-deploy

- [ ] `curl https://api-dev.gobeauty.site/readyz/` → 200
- [ ] `curl https://api-dev.gobeauty.site/api/v1/customer/auth/verify` (with valid initData header) → 200 or 404 user_not_registered
- [ ] `https://miniapp-dev.gobeauty.site/` returns HTML with `<title>` matching app
- [ ] `journalctl -u ai-bot-platform-dev -n 50` — no traceback in last 5 min
- [ ] `sudo systemctl is-active ai-bot-platform-dev{,-worker,-beat}` — all `active`
- [ ] PostgreSQL: `sudo -u postgres psql ai_bot_platform_dev -c '\dt'` shows migrated tables
- [ ] Redis: `redis-cli -n 3 ping` → PONG (dev), `redis-cli -n 4 ping` → PONG (prod)
- [ ] RAM headroom: `free -h` shows ≥100Mi free after services up

---

## 7. Resource budget

Per process (steady state) on app.penza.taxi:

| Service | RAM | Notes |
|---|---|---|
| ai-bot-platform-dev (gunicorn 2 workers) | ~250MB | MemoryMax=512M |
| ai-bot-platform-dev-worker (celery 2 concur) | ~250MB | MemoryMax=512M |
| ai-bot-platform-dev-beat | ~80MB | MemoryMax=200M |
| **dev total** | **~580MB** | |
| ai-bot-platform-prod (gunicorn 3 workers) | ~350MB | |
| ai-bot-platform-prod-worker | ~250MB | |
| ai-bot-platform-prod-beat | ~80MB | |
| **prod total** | **~680MB** | |

Box currently uses ~1.7GB / 1.8GB total. Adding ~1.2GB for both
envs exceeds physical RAM → swap usage. **MITIGATION REQUIRED:**

1. Stop mysite-stage / mysite-staging gunicorn (`formula_tela_staging.service`) if not actively used
2. Reduce GoBeauty docker compose workers (`dev-celery_worker`, `dev-celery_beat` — heavy?)
3. Defer prod deploy until after mysite cutover (when its services stop)
4. Add swap (current 2GB swap nearly full; bump to 4GB)
5. Long-term: bigger VPS or move Ayla/GoBeauty to its own box

**Decision required from PM before §3 (PROD) provisioning.**

---

## 8. Open infra TODOs (post-Phase 0)

- [ ] Activate `deploy-dev.yml` GitHub Actions workflow (currently placeholder per DRF-891 — see workflow comments)
- [ ] `tmpfiles.d/ai-bot-platform.conf` for any /run/ socket paths (none currently — direct TCP ports)
- [ ] Backup: `pg_dump ai_bot_platform_prod` to `/home/taximeter/backups/` daily cron
- [ ] Log rotation for `/var/log/nginx/ai-bot-platform-*` (default logrotate.d/nginx covers)
- [ ] Sentry / OTEL exporters: fill envs in §2.3 once endpoints exist
- [ ] Monitoring: extend `munin-node` (running on box) with platform-specific plugins, or stand up Prometheus (separate epic)
