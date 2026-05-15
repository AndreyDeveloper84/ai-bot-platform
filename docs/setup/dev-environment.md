# Dev environment setup — `@ai_bot_platform_dev` bot + dev instance

> Sprint 10 / DRF-891 — operator setup procedure for the dev MAX-bot
> + dev platform instance.

## Why

Before Sprint 10 X-5pct (first real MAX traffic on the canary), we
need a place where the platform can run against a non-public MAX-bot
so the Lead can poke things manually for 1-2 days before they reach
production users.

Without this, the canary itself is the test — but the canary is
supposed to be the rollback safety net, not the QA gate.

## Decision: where to host the dev instance

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| **A. Same VPS, separate compose project** | No extra cost; shares Postgres + Redis containers via separate DBs/databases | Shared blast radius — kernel panic on prod = dev down too | ✅ **default for Phase 0** |
| **B. Separate VPS** (~₽500-1000/мес) | Full isolation | Extra cost + setup + monitoring | Use if A starves on resources or if security iso required |
| **C. Local + Cloudflare Tunnel** | Zero-cost iteration | MAX webhook unstable via tunnel; can't `git pull` from CI | Use for sub-feature poke-tests only, not for the 24h soak gate |

**Recommended:** A for Phase 0; revisit at Phase 1 kickoff.

## One-time setup (operator, ~4-6 hours)

### Step 1 — Create `@ai_bot_platform_dev` MAX-bot (10 min)

1. Open https://botapi.max.ru (or the MAX bot creator UI)
2. Sign in as the platform team member
3. Create a new bot: `@ai_bot_platform_dev`
4. Copy the token; store in 1Password under `ops vault → MAX_BOT_TOKEN_DEV`
5. **Do NOT** configure a webhook yet — we set it after the dev instance
   is reachable

### Step 2 — Provision dev instance directory + env (30 min)

On `app.penza.taxi`:

```bash
ssh ops@app.penza.taxi

sudo mkdir -p /home/ops/ai-bot-platform-dev
sudo chown ops:ops /home/ops/ai-bot-platform-dev
cd /home/ops/ai-bot-platform-dev
git clone https://github.com/AndreyDeveloper84/ai-bot-platform.git .
git checkout dev

sudo mkdir -p /etc/ai-bot-platform-dev
sudo nano /etc/ai-bot-platform-dev/.env
```

Populate `.env` with dev-scoped values:

```env
# === Django ===
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<generate fresh: python -c 'import secrets; print(secrets.token_urlsafe(50))'>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=dev.app.penza.taxi,127.0.0.1

# === DB === (separate database on same Postgres container)
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=ai_bot_platform_dev   # NOTE _dev suffix
POSTGRES_USER=platform_dev        # NOTE _dev suffix
POSTGRES_PASSWORD=<generate fresh>

# === Redis === (separate DB index on same Redis container)
REDIS_URL=redis://redis:6379/1    # /1 vs prod's /0

# === MAX bot === (the new dev bot)
MAX_BOT_TOKEN=<MAX_BOT_TOKEN_DEV from 1Password>
ADMIN_MAX_CHAT_ID=<your personal MAX chat for dev pings>

# === Alerts ===
TELEGRAM_BOT_TOKEN=<can share with prod>
ALERTS_TELEGRAM_CHAT_ID=<-100... ID of "🚨 ai-bot-platform DEV alerts" channel
                          OR same as prod if you don't mind mixed signal>
TELEGRAM_PROXY=<same proxy as prod — api.telegram.org is RU-blocked>

# === Sentry === (separate project recommended)
SENTRY_DSN=<dev project DSN; empty if you don't want dev events in Sentry>
SENTRY_ENVIRONMENT=dev

# === ChromaDB === (separate auth token = separate collection namespace)
CHROMA_HTTP_HOST=chromadb
CHROMA_HTTP_PORT=8001
CHROMA_AUTH_TOKEN=<generate fresh — different from prod>

# === Mysite catalog ===
MYSITE_CATALOG_BASE_URL=https://formulatela58.ru
MYSITE_CATALOG_SERVICE_TOKEN=<same as prod — read-only access>
MYSITE_WEBHOOK_HMAC_SECRET=<generate fresh OR same as prod;
                            dev webhook receives same deliveries if same>

# === STRICT scope === (dev runs strict from day 1; prod still on audit)
STRICT_TENANT_SCOPE=strict
# STRICT_SCOPE_FLIP_AT — leave unset; dev was never on audit mode
```

### Step 3 — Bring up the dev compose project (30 min)

Use a separate compose project name (`-p ai-bot-platform-dev`) so
containers don't collide with prod:

```bash
cd /home/ops/ai-bot-platform-dev
sudo docker compose --env-file /etc/ai-bot-platform-dev/.env \
  -p ai-bot-platform-dev up -d --force-recreate web worker
```

**Port mapping:** dev runs on `:8013` (prod is `:8003`). Update
`docker-compose.yml` or use an override file:

```yaml
# docker-compose.dev-override.yml
services:
  web:
    ports:
      - "8013:8000"
```

Run the compose with the override:

```bash
sudo docker compose --env-file /etc/ai-bot-platform-dev/.env \
  -p ai-bot-platform-dev \
  -f docker-compose.yml -f docker-compose.dev-override.yml \
  up -d --force-recreate web worker
```

Verify:

```bash
curl -fsS http://127.0.0.1:8013/readyz/
# Expected: {"status":"healthy",...}
```

### Step 4 — Provision dev database (15 min)

If the Postgres container is shared with prod (Option A):

```bash
sudo docker compose -p ai-bot-platform exec postgres psql -U postgres
```

```sql
CREATE USER platform_dev WITH PASSWORD '<from .env>';
CREATE DATABASE ai_bot_platform_dev OWNER platform_dev;
\q
```

Run migrations:

```bash
sudo docker compose --env-file /etc/ai-bot-platform-dev/.env \
  -p ai-bot-platform-dev exec web \
  python manage.py migrate

sudo docker compose --env-file /etc/ai-bot-platform-dev/.env \
  -p ai-bot-platform-dev exec web \
  python manage.py create_tenant --slug formula-tela --name "Формула тела (dev)"
```

### Step 5 — Configure nginx upstream for dev (30 min)

Add `dev.app.penza.taxi` as a new server block routing to `:8013`:

```nginx
# /etc/nginx/sites-available/ai-bot-platform-dev
server {
    listen 443 ssl;
    server_name dev.app.penza.taxi;

    ssl_certificate     /etc/letsencrypt/live/dev.app.penza.taxi/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dev.app.penza.taxi/privkey.pem;

    location /api/maxbot/webhook/ {
        proxy_pass http://127.0.0.1:8013/api/maxbot/webhook/;
        proxy_set_header X-Max-Bot-Api-Secret $http_x_max_bot_api_secret;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /readyz/ {
        proxy_pass http://127.0.0.1:8013/readyz/;
    }

    # Other routes 502 — dev is webhook-only, no public surface.
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ai-bot-platform-dev \
   /etc/nginx/sites-enabled/ai-bot-platform-dev
sudo certbot --nginx -d dev.app.penza.taxi  # or DNS-01 if HTTP-01 blocked
sudo nginx -t && sudo systemctl reload nginx
```

### Step 6 — Wire MAX webhook to dev (5 min)

```bash
# Via curl (replace token):
curl "https://botapi.max.ru/setWebhook?access_token=<MAX_BOT_TOKEN_DEV>" \
  -d "url=https://dev.app.penza.taxi/api/maxbot/webhook/" \
  -d "secret=<X-Max-Bot-Api-Secret value matching dev .env>"
```

Smoke: open `@ai_bot_platform_dev` in MAX, send `/start`. Bot should
respond as the prod bot does but using dev data.

### Step 7 — Configure GitHub secrets (10 min)

In repo Settings → Secrets and variables → Actions:

```
DEV_HOST          = app.penza.taxi
DEV_USER          = ops
DEV_SSH_KEY       = <contents of ops's id_rsa private key>
DEV_DEPLOY_PATH   = /home/ops/ai-bot-platform-dev
```

After setup, **uncomment the deploy steps** in
`.github/workflows/deploy-dev.yml` and push to `dev` — the workflow
should SSH + git pull + restart + smoke + Telegram alert.

### Step 8 — Verify dev-flow end-to-end (20 min)

```bash
# Local
git checkout -b feat/dev-flow-smoke
echo "# Dev flow smoke $(date)" >> README.md
git add README.md
git commit -m "test: dev-flow smoke"
git push -u origin feat/dev-flow-smoke

# Open PR feat/dev-flow-smoke → dev (NOT main!)
# Merge after CI green.
# Wait ~2 min for deploy-dev.yml to fire.

# Then send a message to @ai_bot_platform_dev — confirms code is live on dev.
```

If all good: DRF-891 acceptance criteria satisfied; this runbook
graduates to `complete`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose up` fails: "port already allocated" | Both compose projects trying to bind same port | Use the `-p ai-bot-platform-dev` flag consistently; check `docker ps` for stale containers |
| MAX webhook returns 502 | nginx upstream is dev port (8013) but service down | `sudo docker compose -p ai-bot-platform-dev ps`; check worker logs |
| Bot doesn't respond on dev but webhook succeeds | `MAX_BOT_TOKEN` mismatch — bot getting webhook for the wrong token | Verify `/etc/ai-bot-platform-dev/.env` has `MAX_BOT_TOKEN_DEV` value, not prod's |
| Alerts fire to prod channel from dev | `ALERTS_TELEGRAM_CHAT_ID` shared between envs | Create separate dev alert channel + new chat_id in dev .env |
| Dev DB queries return prod data | Wrong `POSTGRES_DB` in .env — pointing at `ai_bot_platform` not `_dev` | Fix .env, restart worker |

## Related

- [`branch-protection.md`](branch-protection.md) — git-level enforcement
- CLAUDE.md § Git workflow — when to push where
- `.github/workflows/deploy-dev.yml` — what fires on `dev` push
