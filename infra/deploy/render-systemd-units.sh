#!/bin/bash
# Render systemd + nginx templates for a given environment.
#
# Usage:
#   ./render-systemd-units.sh dev
#   ./render-systemd-units.sh prod
#
# Outputs to /tmp/ai-bot-platform-render-${ENV}/ — review there, then
# manually copy to /etc/ paths with sudo (per runbook §2.7-§2.8).

set -euo pipefail

ENV="${1:-}"
if [[ -z "$ENV" || ! "$ENV" =~ ^(dev|prod)$ ]]; then
    echo "Usage: $0 <dev|prod>" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ "$ENV" == "dev" ]]; then
    DEPLOY_PATH="/home/taximeter/ai-bot-platform-dev"
    ENV_FILE="/etc/ai-bot-platform/dev.env"
    PORT=8014
    WORKERS=2
    # #417 (2026-05-21): dev migrated from *.gobeauty.site to *.ayla.app.
    # Production URLs remain on gobeauty.site until Phase 1 (Notion API
    # Spec v2.0 target: api.ayla.app, ready Phase 1).
    API_SUB="api-dev.ayla.app"
    MINIAPP_SUB="miniapp-dev.ayla.app"
else
    DEPLOY_PATH="/home/taximeter/ai-bot-platform"
    ENV_FILE="/etc/ai-bot-platform/prod.env"
    PORT=8013
    WORKERS=3
    # Phase 1 flip target: api.ayla.app / miniapp.ayla.app per Notion
    # API Spec v2.0. Until then prod stays on gobeauty.site.
    API_SUB="api.gobeauty.site"
    MINIAPP_SUB="miniapp.gobeauty.site"
fi

OUT="/tmp/ai-bot-platform-render-${ENV}"
mkdir -p "${OUT}/systemd" "${OUT}/nginx"

# Systemd units
for svc in web worker beat consumer; do
    SRC="${REPO_ROOT}/infra/systemd/ai-bot-platform-${svc}.service.template"
    DST_NAME="ai-bot-platform-${ENV}"
    [[ "$svc" != "web" ]] && DST_NAME="${DST_NAME}-${svc}"
    DST="${OUT}/systemd/${DST_NAME}.service"
    sed -e "s|{ENV}|${ENV}|g" \
        -e "s|{DEPLOY_PATH}|${DEPLOY_PATH}|g" \
        -e "s|{ENV_FILE}|${ENV_FILE}|g" \
        -e "s|{GUNICORN_PORT}|${PORT}|g" \
        -e "s|{WORKERS}|${WORKERS}|g" \
        "${SRC}" > "${DST}"
done

# Nginx vhost for API
sed -e "s|{SUBDOMAIN}|${API_SUB}|g" \
    -e "s|{GUNICORN_PORT}|${PORT}|g" \
    -e "s|{DEPLOY_PATH}|${DEPLOY_PATH}|g" \
    "${REPO_ROOT}/infra/nginx/ai-bot-platform-api.conf.template" \
    > "${OUT}/nginx/${API_SUB}"

# Nginx vhost for Mini App
sed -e "s|{SUBDOMAIN}|${MINIAPP_SUB}|g" \
    -e "s|{DIST_PATH}|${DEPLOY_PATH}/apps/miniapp/dist|g" \
    "${REPO_ROOT}/infra/nginx/miniapp.conf.template" \
    > "${OUT}/nginx/${MINIAPP_SUB}"

echo "Rendered to ${OUT}/"
echo ""
echo "Next steps (per docs/runbooks/server-deployment.md §2.7-§2.8):"
echo "  sudo cp ${OUT}/systemd/*.service /etc/systemd/system/"
echo "  sudo cp ${OUT}/nginx/${API_SUB} /etc/nginx/sites-available/"
echo "  sudo cp ${OUT}/nginx/${MINIAPP_SUB} /etc/nginx/sites-available/"
echo "  sudo systemctl daemon-reload"
echo "  sudo certbot --nginx -d ${API_SUB} -d ${MINIAPP_SUB}"
echo "  sudo ln -sf /etc/nginx/sites-available/${API_SUB} /etc/nginx/sites-enabled/"
echo "  sudo ln -sf /etc/nginx/sites-available/${MINIAPP_SUB} /etc/nginx/sites-enabled/"
echo "  sudo nginx -t && sudo systemctl reload nginx"
