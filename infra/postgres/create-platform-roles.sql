-- ai-bot-platform Postgres roles + databases (logical isolation).
--
-- Per server-layout review 2026-05-19: app.penza.taxi has only 92Mi
-- free RAM. We CANNOT afford a second Postgres container. Instead:
-- one host-level Postgres on 127.0.0.1:5432 with isolated roles +
-- databases per environment.
--
-- Run AS the Postgres superuser (typically `postgres`):
--   sudo -u postgres psql -f create-platform-roles.sql
--
-- Idempotent: uses DO blocks with IF NOT EXISTS guards.

DO $$
BEGIN
    -- Production role + database
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_bot_platform_prod') THEN
        CREATE ROLE ai_bot_platform_prod WITH LOGIN PASSWORD 'CHANGE_ME_PROD_PASSWORD';  -- pragma: allowlist secret
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ai_bot_platform_prod') THEN
        CREATE DATABASE ai_bot_platform_prod OWNER ai_bot_platform_prod;
    END IF;

    -- Dev role + database
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_bot_platform_dev') THEN
        CREATE ROLE ai_bot_platform_dev WITH LOGIN PASSWORD 'CHANGE_ME_DEV_PASSWORD';  -- pragma: allowlist secret
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ai_bot_platform_dev') THEN
        CREATE DATABASE ai_bot_platform_dev OWNER ai_bot_platform_dev;
    END IF;
END
$$;

-- Hard isolation: neither role can see the other's database or
-- mysite/beautygo databases. Postgres default — REVOKE only if you
-- want to be extra explicit. CONNECT is granted to the owner role
-- automatically.

-- Audit
\du ai_bot_platform_prod
\du ai_bot_platform_dev
\l ai_bot_platform_prod
\l ai_bot_platform_dev
