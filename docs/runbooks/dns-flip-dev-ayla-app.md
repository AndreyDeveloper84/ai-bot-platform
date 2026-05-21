# DNS flip — `dev.gobeauty.site` → `dev.ayla.app`

Operator + Sync 1 driver runbook for issue **#417** (Phase 0 / Sprint 1 Foundation, Bucket 2 — Technical rebrand).

Touches **three repos** and **external DNS / certs / Nginx config**. This runbook is the canonical sequence; Sync 1 driver coordinates, code-stream agents (ai-bot-platform / Ayla djangoproject / frontAyla) execute their slice.

---

## 0. Decision point — subdomain structure

Issue #417 says "dev environment URLs migrate from `dev.gobeauty.site` to `dev.ayla.app`" but the current structure has THREE subdomains, all of which need a home in the new namespace.

| Current | Serves | New (Option A — flat) | New (Option B — subdomained) |
|---|---|---|---|
| `dev.gobeauty.site` | Ayla djangoproject (port 8013) | `dev.ayla.app` | `dev.ayla.app` |
| `api-dev.gobeauty.site` | bot-platform API + ingress (port 8014) | `dev.ayla.app/api/v1/...` (path-based) | `api-dev.ayla.app` |
| `miniapp-dev.gobeauty.site` | Mini App static (Nginx → `apps/miniapp/dist`) | `dev.ayla.app/miniapp` | `miniapp-dev.ayla.app` |

**Option A (flat, one cert):** simpler ops, single Let's Encrypt cert, but path-based routing forces Nginx to discriminate `/api/v1/`, `/miniapp`, and the django default by location blocks. Tighter coupling between Ayla djangoproject and bot-platform reverse-proxy config.

**Option B (subdomained, three certs OR one wildcard):** mirrors the current shape 1:1 — flip is a near-pure `sed` on Nginx + a re-subscribe call. Three Let's Encrypt issuances, or one `*.ayla.app` wildcard (cheaper to manage long-term).

**Recommendation: Option B with a wildcard cert.** Reasoning:
- Production target per Notion API Spec v2.0 is `api.ayla.app` — already subdomained.
- Wildcard `*.ayla.app` saves issuance cycles for every future subdomain (master-admin internal-chat infra in Phase 1, etc.).
- 1:1 mapping = the 301 redirect rule is just `s/gobeauty.site/ayla.app/` — no path rewrites.

**Sync 1 driver must lock the choice before any code change ships.** This runbook assumes Option B below; flag if you choose A and the templates need restructuring.

---

## 1. ai-bot-platform inventory (audited 2026-05-21)

### Code / config files that reference old domains

| File | Line(s) | Type | Change |
|---|---|---|---|
| `apps/channels/management/commands/max_subscribe_webhook.py` | 18 | docstring usage example | text-only |
| `infra/deploy/render-systemd-units.sh` | 26-27 | `API_SUB` + `MINIAPP_SUB` shell vars | code |
| `infra/nginx/ai-bot-platform-api.conf.template` | 4, 10 | comments + sed example | doc-only |
| `infra/nginx/miniapp.conf.template` | 4, 7 | comments + sed example | doc-only |
| `tests/e2e/test_ayla_integration.py` | 5, 11 | docstring | text-only |

### Operator-facing docs

- `docs/runbooks/miniapp-acceptance.md` — pre-check curls
- `docs/runbooks/server-deployment.md` — deployment diagram
- `docs/qa/ayla-e2e-setup.md` — `AYLA_BASE_URL` example
- `docs/plans/sprint-9-internal-smoke.md` — internal-smoke target URLs

### Runtime config (live on dev host)

- `/etc/ai-bot-platform/dev.env`:
  - `MAX_MINIAPP_URL=https://miniapp-dev.gobeauty.site` → `https://miniapp-dev.ayla.app`
- MAX webhook subscription (managed via `python manage.py max_subscribe_webhook`):
  - Current: `https://api-dev.gobeauty.site/api/v1/ingress/max/`
  - New: `https://api-dev.ayla.app/api/v1/ingress/max/`

---

## 2. Cross-repo inventory (sync coordination)

| Repo | Files to update | Owner agent |
|---|---|---|
| `ai-bot-platform` | files above | W2 (code-stream) |
| `Ayla djangoproject` | `ALLOWED_HOSTS`, `AYLA_BASE_URL`, `WEBHOOK_BASE_URL` (if applicable) | NEW Alpha |
| `frontAyla` (mobile) | `EXPO_PUBLIC_API_BASE_URL` build-time env, all hard-coded URLs in plist/manifest | (TBD — frontAyla agent) |

Sync 1 driver gathers PRs from all three before T-zero flip.

---

## 3. T-zero sequence (Sync 1 driver executes)

### 3.1 Pre-flip (24h ahead)

1. **DNS — add new records (parallel to old, no break)**:
   - `dev.ayla.app A <dev-VPS-ip>`
   - `api-dev.ayla.app CNAME dev.ayla.app` (or `A` to same IP)
   - `miniapp-dev.ayla.app CNAME dev.ayla.app`

2. **Let's Encrypt — issue wildcard**:
   ```bash
   sudo certbot certonly --dns-cloudflare \
       -d 'ayla.app' -d '*.ayla.app'
   ```
   (DNS-01 challenge required for wildcard; alternative: per-subdomain HTTP-01 if no Cloudflare API access. Operator picks based on DNS provider.)

3. **Nginx — add new vhosts ALONGSIDE old ones**:
   ```bash
   # New vhost files (operator copies from infra/nginx/ templates with
   # post-#417 placeholders filled).
   sudo cp infra/nginx/ai-bot-platform-api.conf.template \
       /etc/nginx/sites-available/api-dev.ayla.app
   sudo sed -i 's/{SUBDOMAIN}/api-dev.ayla.app/g; s/{GUNICORN_PORT}/8014/g' \
       /etc/nginx/sites-available/api-dev.ayla.app
   sudo ln -s /etc/nginx/sites-available/api-dev.ayla.app /etc/nginx/sites-enabled/

   # Same pattern for miniapp-dev.ayla.app — `infra/nginx/miniapp.conf.template`.
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. **Verify new URLs work** before old ones are killed:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://api-dev.ayla.app/readyz/
   curl -s -o /dev/null -w "%{http_code}\n" https://miniapp-dev.ayla.app/
   ```

### 3.2 T-zero (the actual flip)

5. **Update bot-platform `/etc/ai-bot-platform/dev.env`**:
   ```bash
   sudo sed -i 's|https://miniapp-dev.gobeauty.site|https://miniapp-dev.ayla.app|g' \
       /etc/ai-bot-platform/dev.env
   ```

6. **Re-subscribe MAX webhook to new URL**:
   ```bash
   cd /home/taximeter/ai-bot-platform-dev
   set -a && source /etc/ai-bot-platform/dev.env && set +a
   DJANGO_SETTINGS_MODULE=config.settings.staging \
       .venv/bin/python manage.py max_subscribe_webhook \
       --url https://api-dev.ayla.app/api/v1/ingress/max/
   ```

7. **Restart bot-platform services** (env reload):
   ```bash
   sudo systemctl restart ai-bot-platform-dev ai-bot-platform-dev-consumer ai-bot-platform-dev-beat
   ```

8. **Coordinate Ayla djangoproject + frontAyla cuts** (NEW Alpha + frontAyla agents do the equivalent on their stacks). Sync 1 driver confirms each is live.

### 3.3 Post-flip — 301 redirect on old domains (30-day window)

9. **Nginx — replace `gobeauty.site` server blocks with 301 redirects**:
   ```nginx
   # /etc/nginx/sites-available/dev.gobeauty.site (replaces existing).
   server {
       listen 443 ssl;
       server_name dev.gobeauty.site api-dev.gobeauty.site miniapp-dev.gobeauty.site;

       ssl_certificate /etc/letsencrypt/live/gobeauty.site/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/gobeauty.site/privkey.pem;

       # 301 → matching subdomain on ayla.app. The replacement uses
       # $host so api-dev.gobeauty.site → api-dev.ayla.app, etc.
       return 301 https://$1.ayla.app$request_uri;
   }
   ```
   (The `$1` capture pattern needs a regex `server_name` — actual config likely uses three explicit server blocks. Operator: use whatever pattern is least error-prone for them.)

10. **Mark calendar**: kill the old vhost + Let's Encrypt entry **30 days after T-zero**. Mobile builds older than 30 days fail — that's the announced contract.

---

## 4. ai-bot-platform code changes that ship in this PR

Code change is **scoped to inventory + this runbook** until Sync 1 picks Option A/B. After lock, a follow-up PR updates the template / shell-var / docstring references — pure text find-and-replace, no behaviour change.

### Why not also update templates now

- Templates use `{SUBDOMAIN}` placeholders — they're already domain-agnostic. The "change" is the comment-example value, not the template logic.
- Pre-committing to one domain shape means a second PR if Sync 1 picks Option A. Two cheap PRs > one expensive one.

---

## 5. Acceptance checklist (mirrors #417)

- [ ] DNS records for `dev.ayla.app` + `api-dev.ayla.app` + `miniapp-dev.ayla.app` point to dev VPS
- [ ] Wildcard Let's Encrypt cert issued (or three per-subdomain certs if no DNS-API access)
- [ ] Nginx vhosts for new URLs live, both `curl ... 200`
- [ ] bot-platform `dev.env` updated; services restarted; MAX webhook re-subscribed to `api-dev.ayla.app`
- [ ] Ayla djangoproject + frontAyla cutover confirmed by their stream owners
- [ ] 30-day 301 redirect from old domains active
- [ ] Sprint 9 internal smoke (`docs/plans/sprint-9-internal-smoke.md`) re-run + green against new URLs

---

## 6. Rollback

If anything fails between steps 5 and 8:

1. Restore old `/etc/ai-bot-platform/dev.env` from backup (Sync 1 driver: take backup before §3.2).
2. Re-subscribe MAX webhook back to `api-dev.gobeauty.site`:
   ```bash
   .venv/bin/python manage.py max_subscribe_webhook \
       --url https://api-dev.gobeauty.site/api/v1/ingress/max/
   ```
3. Restart services. Old vhosts on Nginx still alive (we kept them in §3.1), so traffic just routes back.

No code rollback needed — the prep PR doesn't change behaviour.
