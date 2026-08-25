#!/usr/bin/env bash
# Live proof that the bot can reach Ayla's salon surface — DRF-1346.
#
# Four steps, and the first two are the point. `TenantContextMiddleware`
# answers 400 TENANT_REQUIRED for ANY /api/v1/ path when X-Tenant is missing,
# and it runs BEFORE routing — so a made-up path answers exactly like a real
# one. Probing a route without calibrating first proves nothing about whether
# the route exists.
#
#   1. non-existent path, no X-Tenant   -> expect 400 TENANT_REQUIRED  (the trap)
#   2. non-existent path, all headers   -> expect 404                  (calibration)
#   3. GET tenants/me/day/, all headers -> expect 200                  (the claim)
#   4. GET tenants/me/day/, no app-type -> expect 403 APP_TYPE_MISSING (the header
#                                                                      that was missing)
#
# Step 2 is what makes step 3 mean something: a 404 on a path that does not
# exist proves the request reached the URL resolver, so a 200 on day/ is the
# route answering and not middleware waving us through.
#
# Prints status codes and Ayla's machine-readable error codes only. The token
# is never echoed, and the day payload is summarised rather than dumped — it
# carries client_id and client_name per booking.
#
# Usage:
#   AYLA_BASE_URL=https://api-dev.gobeauty.site \
#   AYLA_INTERNAL_API_TOKEN=… \
#   SALON_ACTOR=bot:max:<max-user-id-of-a-tenant-admin> \
#   SALON_TENANT=<tenant-slug> \
#   bash scripts/salon_surface_probe.sh

set -u

: "${AYLA_BASE_URL:?set AYLA_BASE_URL (host only, no /api/v1)}"
: "${AYLA_INTERNAL_API_TOKEN:?set AYLA_INTERNAL_API_TOKEN}"
: "${SALON_ACTOR:?set SALON_ACTOR — X-External-User-ID of a human who administers the tenant}"
: "${SALON_TENANT:?set SALON_TENANT — the tenant slug for X-Tenant}"

BASE="${AYLA_BASE_URL%/}"
GHOST="$BASE/api/v1/tenants/me/__drf1346_no_such_route__/"
DAY="$BASE/api/v1/tenants/me/day/"

AUTH="Authorization: Bearer ${AYLA_INTERNAL_API_TOKEN}"
ACTOR_H="X-External-User-ID: ${SALON_ACTOR}"
TENANT_H="X-Tenant: ${SALON_TENANT}"
APP_H="X-App-Type: pro"

# Emit "<status> <error.code or ->" and nothing that came out of a body except
# that code. Ayla's envelope is {"error": {"code": ..., "message": ...}}.
probe() {
  local label="$1"; shift
  local out status code
  out="$(curl -sS -o /tmp/drf1346_body -w '%{http_code}' "$@")" || {
    echo "  $label -> curl failed"; return 1;
  }
  status="$out"
  code="$(sed -n 's/.*"code"[[:space:]]*:[[:space:]]*"\([A-Z_]*\)".*/\1/p' /tmp/drf1346_body | head -1)"
  printf '  %-46s -> %s %s\n' "$label" "$status" "${code:--}"
}

echo "probing ${BASE} as tenant '${SALON_TENANT}'"
echo

echo "1. the trap — non-existent path, X-Tenant omitted (expect 400 TENANT_REQUIRED)"
probe "GET __drf1346_no_such_route__ (no tenant)" -X GET "$GHOST" -H "$AUTH" -H "$ACTOR_H" -H "$APP_H"

echo "2. calibration — non-existent path, every header (expect 404)"
probe "GET __drf1346_no_such_route__" -X GET "$GHOST" -H "$AUTH" -H "$ACTOR_H" -H "$TENANT_H" -H "$APP_H"

echo "3. the claim — the salon day (expect 200)"
probe "GET tenants/me/day/" -X GET "$DAY" -H "$AUTH" -H "$ACTOR_H" -H "$TENANT_H" -H "$APP_H"

if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' </tmp/drf1346_body
import json, sys
try:
    data = (json.load(sys.stdin) or {}).get("data") or {}
except Exception:
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
masters = data.get("masters") or []
bookings = sum(len(m.get("bookings") or []) for m in masters if isinstance(m, dict))
# Counts and key names only — the payload carries client_id and client_name.
print(f"     date={data.get('date')} masters={len(masters)} bookings={bookings} "
      f"closures={len(data.get('closures') or [])} keys={sorted(data)}")
PY
fi

echo "4. the negative — same call without X-App-Type (expect 403 APP_TYPE_MISSING)"
probe "GET tenants/me/day/ (no app-type)" -X GET "$DAY" -H "$AUTH" -H "$ACTOR_H" -H "$TENANT_H"

rm -f /tmp/drf1346_body
echo
echo "reading: 1=400 TENANT_REQUIRED, 2=404, 3=200, 4=403 APP_TYPE_MISSING means the"
echo "client's header set is exactly what this surface needs. A 403 on step 3 with"
echo "PERMISSION_DENIED means SALON_ACTOR does not administer SALON_TENANT — that is"
echo "Ayla refusing the human, which is the other thing worth proving."
