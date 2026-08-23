#!/usr/bin/env python3
"""DRF-916 Booking E2E acceptance harness.

Drives the REAL deployed product paths on the Controlled Pilot baseline:
  CREATE  -> BOT Mini App API (MaxInitData) -> Ayla internal create (no-prepay)
  LOOKUP  -> BOT Mini App API (proxy mirror read)
  RESCHEDULE -> Ayla client app API (OTP -> JWT -> /api/v1/appointments/{id}/reschedule/)
  CANCEL  -> BOT Mini App API -> Ayla internal cancel

Usage:
  python drf916_e2e.py                 # full run (create+lookup+reschedule+cancel)
  python drf916_e2e.py <appt_uuid>     # resume: skip create/lookup, run reschedule+cancel

Secrets are read from env files and never printed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode

import requests

BOT = "https://api-dev.gobeauty.site"
AYLA = "https://dev.gobeauty.site"
TENANT = "b32a057a-56c7-4bf0-ae50-e11e76ab44be"
TENANT_SLUG = "formula-tela"
CH_UID = "drf954-test-001"
AYLA_USER = "63ae8906-634a-4977-9a93-bda65c028034"
PHONE = "+79990001003"

RESULTS: list[tuple[str, str, str]] = []


def report(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    print(f"[{status:4s}] {step}: {detail}", flush=True)


def fail(step: str, detail: str) -> None:
    report(step, "FAIL", detail)
    summary()
    sys.exit(1)


def summary() -> None:
    print("\n=== SUMMARY ===")
    for s, st, d in RESULTS:
        print(f"{st:4s}  {s}  {d[:160]}")


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


BOT_ENV = load_env("/home/taximeter/ai-bot-platform-dev/.env.staging")
BE_ENV = load_env("/home/taximeter/beautygo/dev/.env")
MAX_BOT_TOKEN = BOT_ENV["MAX_BOT_TOKEN"]
INTERNAL_TOKEN = BE_ENV.get("AYLA_INTERNAL_API_TOKEN") or BOT_ENV.get("AYLA_INTERNAL_API_TOKEN", "")


def mint_init_data(uid: str) -> str:
    pairs = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": uid}, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", MAX_BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def bot_get(path: str) -> requests.Response:
    return requests.get(
        BOT + path, headers={"Authorization": "MaxInitData " + mint_init_data(CH_UID)}, timeout=20
    )


def bot_post(path: str, body: dict | None = None, extra: dict | None = None) -> requests.Response:
    hdrs = {"Authorization": "MaxInitData " + mint_init_data(CH_UID)}
    if extra:
        hdrs.update(extra)
    return requests.post(BOT + path, json=body or {}, headers=hdrs, timeout=30)


def psql(container: str, db: str, user: str, sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-d", db, "-At", "-c", sql],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def bot_sql(sql: str) -> str:
    return psql("ayla-bot-staging-postgres-1", "ai_bot_platform", "platform", sql)


def be_sql(sql: str) -> str:
    return psql("dev-db-1", "beautygo", "beautygo", sql)


def be_appointment(appt: str) -> dict:
    out = be_sql(
        "SELECT status, version, start_datetime, end_datetime, service_id, specialist_id, client_id, tenant_id "
        f"FROM appointments_appointment WHERE id='{appt}'"
    )
    if not out:
        return {}
    f = out.split("|")
    return {
        "status": f[0],
        "version": int(f[1]),
        "start": f[2],
        "end": f[3],
        "service_id": f[4],
        "specialist_id": f[5],
        "client_id": f[6],
        "tenant_id": f[7],
    }


def bot_proxy(appt: str) -> dict:
    out = bot_sql(
        "SELECT status, COALESCE(last_applied_appointment_version,-1), start_at, end_at, service_id, "
        "specialist_id, bot_user_id, tenant_id, last_synced_event_id "
        f"FROM booking_remotebookingproxy WHERE appointment_id='{appt}'"
    )
    if not out:
        return {}
    f = out.split("|")
    return {
        "status": f[0],
        "version": int(f[1]),
        "start": f[2],
        "end": f[3],
        "service_id": f[4],
        "specialist_id": f[5],
        "bot_user_id": f[6],
        "tenant_id": f[7],
        "last_event": f[8],
    }


def be_outbox(appt: str) -> list[str]:
    out = be_sql(
        "SELECT topic || '|' || bot_delivery_status || '|' || id FROM appointments_outboxevent "
        f"WHERE payload->'data'->>'appointment_id'='{appt}' ORDER BY created_at"
    )
    return [ln for ln in out.splitlines() if ln]


def wait_proxy(appt: str, want_status: str | None = None, timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = bot_proxy(appt)
        if p and (want_status is None or p["status"] == want_status):
            return p
        time.sleep(5)
    return bot_proxy(appt)


def pick_fixture() -> tuple[str, str, str, str, str]:
    row = bot_sql(
        "SELECT s.id, s.ayla_service_id, s.name, m.id, m.name "
        "FROM catalog_catalogservice s "
        "JOIN catalog_masterservice ms ON ms.service_id = s.id "
        "JOIN catalog_catalogmaster m ON m.id = ms.master_id "
        f"WHERE s.tenant_id='{TENANT}' AND s.is_active AND s.ayla_service_id IS NOT NULL "
        "AND s.duration_min > 0 AND m.ayla_user_id IS NOT NULL AND m.is_active "
        "AND m.invite_status='accepted' ORDER BY s.name LIMIT 1"
    )
    if not row:
        fail("fixture", "no grounded master/service pair found")
    svc_id, ayla_svc_id, svc_name, mst_id, mst_name = row.split("|")[:5]
    report("fixture", "PASS", f"service={svc_name}({svc_id}) master={mst_name}({mst_id})")
    return svc_id, ayla_svc_id, svc_name, mst_id, mst_name


def pick_slots(mst_id: str, ayla_svc_id: str, count: int = 3) -> list[str]:
    r = bot_get(
        f"/api/v1/customer/slots?master_id={mst_id}&service_id="
        f"{bot_sql(f"SELECT id FROM catalog_catalogservice WHERE ayla_service_id='{ayla_svc_id}' LIMIT 1")}"
        "&date_from=2026-08-09&date_to=2026-08-16"
    )
    if r.status_code != 200:
        fail("slots", f"HTTP {r.status_code} {r.text[:200]}")
    bot_slots = [s["start"] for s in r.json().get("slots", [])]
    be_all: set[str] = set()
    for day in sorted({s[:10] for s in bot_slots}):
        ri = requests.get(
            f"{AYLA}/api/v1/internal/specialists/{mst_id}/slots/?service_id={ayla_svc_id}&date={day}",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}", "X-App-Type": "client"},
            timeout=20,
        )
        if ri.status_code == 200:
            be_all |= {s.replace(" ", "T")[:19] for s in (ri.json().get("slots") or [])}
    slots = [s for s in bot_slots if s[:19] in be_all]
    if len(slots) < count:
        fail("slots", f"need >={count} mutually-confirmed free slots, got {len(slots)}")
    return slots


def phase_create_lookup(
    slot1: str, svc_id: str, svc_name: str, mst_id: str, mst_name: str, corr: str
) -> str:
    # ---- CREATE via Mini App -------------------------------------------------
    body = {"service_id": svc_id, "master_id": mst_id, "visit_at": slot1, "payment_required": False}
    r = bot_post("/api/v1/customer/bookings", body, extra={"X-Correlation-Id": corr})
    if r.status_code != 201:
        fail("create", f"HTTP {r.status_code} {r.text[:300]}")
    bk = r.json()["booking"]
    appt = bk["id"]
    report("create", "PASS", f"appointment={appt} status={bk['status']} visit_at={bk['visit_at']}")
    if bk["status"] != "confirmed":
        report("create-status", "WARN", f"expected confirmed, got {bk['status']}")

    n_before = be_sql(
        f"SELECT count(*) FROM appointments_appointment WHERE client_id='{AYLA_USER}'"
    )

    # ---- duplicate create (double submit, same idempotency key) --------------
    r2 = bot_post("/api/v1/customer/bookings", body, extra={"X-Correlation-Id": corr})
    dup_id = ""
    try:
        dup_id = r2.json().get("booking", {}).get("id", "")
    except Exception:
        pass
    time.sleep(2)
    n_after = be_sql(f"SELECT count(*) FROM appointments_appointment WHERE client_id='{AYLA_USER}'")
    if n_after == n_before and (not dup_id or dup_id == appt):
        report(
            "dup-create",
            "PASS",
            f"replay HTTP {r2.status_code} same_id={dup_id == appt} count={n_after}",
        )
    else:
        fail(
            "dup-create",
            f"second appointment risk: HTTP {r2.status_code} id={dup_id} count {n_before}->{n_after}",
        )

    # ---- Backend state + EventBus round trip ---------------------------------
    be1 = be_appointment(appt)
    report(
        "create-backend",
        "PASS" if be1.get("status") == "confirmed" else "FAIL",
        f"backend status={be1.get('status')} version={be1.get('version')} start={be1.get('start')}",
    )
    proxy = wait_proxy(appt, timeout=120)
    if not proxy:
        fail("create-eventbus", "no RemoteBookingProxy row appeared within 120s")
    report(
        "create-eventbus",
        "PASS",
        f"proxy status={proxy['status']} start={proxy['start']} event={proxy['last_event'][:8]}",
    )
    if proxy["status"] != "confirmed":
        fail("create-proxy-status", f"proxy status={proxy['status']} != confirmed")

    # ---- LOOKUP via Mini App --------------------------------------------------
    r = bot_get("/api/v1/customer/bookings/list")
    items = r.json().get("items", []) if r.status_code == 200 else []
    mine = [i for i in items if i["id"] == appt]
    if not mine:
        fail("lookup-list", f"appointment not in list (HTTP {r.status_code}, {len(items)} items)")
    it = mine[0]
    from datetime import datetime

    ok = (
        it["service_name"] == svc_name
        and it["master_name"] == mst_name
        and it["status"] == "confirmed"
        and datetime.fromisoformat(it["visit_at"]) == datetime.fromisoformat(slot1)
    )
    report(
        "lookup-list",
        "PASS" if ok else "FAIL",
        f"item service={it['service_name']} master={it['master_name']} status={it['status']} at={it['visit_at']}",
    )

    r = bot_get(f"/api/v1/customer/bookings/{appt}")
    det = r.json().get("booking", {}) if r.status_code == 200 else {}
    ok = det.get("id") == appt and det.get("status") == "confirmed"
    report(
        "lookup-detail",
        "PASS" if ok else "FAIL",
        f"HTTP {r.status_code} status={det.get('status')}",
    )

    # tenant/isolation negative probe: foreign appointment id must 404
    foreign = be_sql(
        f"SELECT id FROM appointments_appointment WHERE tenant_id<>'{TENANT}' OR client_id<>'{AYLA_USER}' LIMIT 1"
    )
    if foreign:
        r = bot_get(f"/api/v1/customer/bookings/{foreign}")
        report(
            "lookup-isolation",
            "PASS" if r.status_code == 404 else "FAIL",
            f"foreign appointment detail -> HTTP {r.status_code}",
        )
    if items:
        ids = ",".join(f"'{i['id']}'" for i in items)
        own = bot_sql(
            f"SELECT count(*) FROM booking_remotebookingproxy WHERE appointment_id IN ({ids}) "
            f"AND bot_user_id=(SELECT id FROM identity_botuser WHERE channel_user_id='{CH_UID}' AND tenant_id='{TENANT}')"
        )
        report(
            "lookup-ownership",
            "PASS" if own == str(len(items)) else "FAIL",
            f"{own}/{len(items)} listed rows owned by synthetic user",
        )
    return appt


def ayla_client_jwt() -> dict:
    """Real OTP flow for the synthetic client; code read from DB (SMS unobservable in test)."""
    requests.post(
        f"{AYLA}/api/v1/auth/send-otp/",
        json={"phone": PHONE},
        headers={"X-App-Type": "client"},
        timeout=20,
    )
    time.sleep(1)
    otp = be_sql(
        f"SELECT code FROM users_otpcode WHERE phone='{PHONE}' AND is_used=false "
        "AND expires_at > now() ORDER BY created_at DESC LIMIT 1"
    )
    if not otp:
        fail("reschedule-auth", "no OTP row minted by send-otp")
    rv = requests.post(
        f"{AYLA}/api/v1/auth/verify-otp/",
        json={"phone": PHONE, "code": otp},
        headers={"X-App-Type": "client"},
        timeout=20,
    )
    tok = rv.json()
    data = tok.get("data") or tok
    access = (
        data.get("access") or data.get("access_token") or (data.get("tokens") or {}).get("access")
    )
    if not access:
        fail("reschedule-auth", f"verify-otp HTTP {rv.status_code} {rv.text[:200]}")
    report("reschedule-auth", "PASS", "JWT obtained via real OTP flow")
    return {"Authorization": f"Bearer {access}", "X-App-Type": "client", "X-Tenant": TENANT_SLUG}


def phase_reschedule_cancel(appt: str, slot2: str, slot3: str) -> None:
    jwt_hdrs = ayla_client_jwt()

    # ---- RESCHEDULE via Ayla client app API (real mobile path) ---------------
    be2 = be_appointment(appt)
    ver = be2["version"]
    rr = requests.post(
        f"{AYLA}/api/v1/appointments/{appt}/reschedule/",
        json={"new_start_datetime": slot2, "expected_version": ver},
        headers=jwt_hdrs,
        timeout=30,
    )
    if rr.status_code not in (200, 201):
        fail("reschedule", f"HTTP {rr.status_code} {rr.text[:300]}")
    be3 = be_appointment(appt)
    ok = (
        be3["version"] == ver + 1
        and be3["start"][:16] != be2["start"][:16]
        and be3["status"] == "confirmed"
    )
    report(
        "reschedule",
        "PASS" if ok else "FAIL",
        f"same id, version {ver}->{be3['version']}, start {be2['start']} -> {be3['start']}",
    )

    proxy2 = {}
    deadline = time.time() + 120
    while time.time() < deadline:
        proxy2 = bot_proxy(appt)
        if (
            proxy2
            and proxy2["version"] >= be3["version"]
            and proxy2["start"][:16] == be3["start"][:16]
        ):
            break
        time.sleep(5)
    ok = (
        proxy2 and proxy2["version"] == be3["version"] and proxy2["start"][:16] == be3["start"][:16]
    )
    report(
        "reschedule-eventbus",
        "PASS" if ok else "FAIL",
        f"proxy version={proxy2.get('version')} start={proxy2.get('start')}",
    )

    # ---- STALE expected_version conflict --------------------------------------
    stale = be3["version"] - 1
    rs = requests.post(
        f"{AYLA}/api/v1/appointments/{appt}/reschedule/",
        json={"new_start_datetime": slot3, "expected_version": stale},
        headers={**jwt_hdrs, "X-Idempotency-Key": uuid.uuid4().hex},
        timeout=30,
    )
    be4 = be_appointment(appt)
    unchanged = be4 == be3
    n_resched_events = len([e for e in be_outbox(appt) if e.startswith("appointment.rescheduled")])
    stale_reason = "STALE" in rs.text.upper()
    if rs.status_code == 409 and stale_reason and unchanged and n_resched_events == 1:
        report(
            "stale-conflict",
            "PASS",
            f"HTTP 409 {rs.text[:90]} appointment untouched (v{be4['version']}), reschedule events={n_resched_events}",
        )
    else:
        fail(
            "stale-conflict",
            f"HTTP {rs.status_code} stale_reason={stale_reason} unchanged={unchanged} events={n_resched_events} {rs.text[:150]}",
        )

    # ---- CANCEL via Mini App ---------------------------------------------------
    rc = bot_post(f"/api/v1/customer/bookings/{appt}/cancel", {"reason_class": "plans_changed"})
    if rc.status_code != 200:
        fail("cancel", f"HTTP {rc.status_code} {rc.text[:300]}")
    be5 = be_appointment(appt)
    report(
        "cancel-backend",
        "PASS" if be5["status"] == "cancelled" else "FAIL",
        f"backend status={be5['status']}",
    )
    proxy3 = wait_proxy(appt, want_status="cancelled", timeout=120)
    report(
        "cancel-eventbus",
        "PASS" if proxy3.get("status") == "cancelled" else "FAIL",
        f"proxy status={proxy3.get('status')}",
    )

    # ---- duplicate cancel -------------------------------------------------------
    rc2 = bot_post(f"/api/v1/customer/bookings/{appt}/cancel", {"reason_class": "plans_changed"})
    time.sleep(15)  # let any (erroneous) second event propagate
    cancel_events = [e for e in be_outbox(appt) if e.startswith("booking.cancelled")]
    be6 = be_appointment(appt)
    ok = rc2.status_code in (200, 409) and be6["status"] == "cancelled" and len(cancel_events) == 1
    report(
        "dup-cancel",
        "PASS" if ok else "FAIL",
        f"repeat HTTP {rc2.status_code} {rc2.text[:80]} cancelled_events={len(cancel_events)}",
    )

    # ---- final reconciliation ----------------------------------------------------
    proxyF = bot_proxy(appt)
    beF = be_appointment(appt)
    ok = (
        proxyF["status"] == beF["status"] == "cancelled"
        and proxyF["tenant_id"] == beF["tenant_id"] == TENANT
        and proxyF["service_id"] == beF["service_id"]
        and proxyF["specialist_id"] == beF["specialist_id"]
        and proxyF["start"][:16] == beF["start"][:16]
        and proxyF["version"] == beF["version"]
    )
    report(
        "reconciliation",
        "PASS" if ok else "FAIL",
        f"backend(v{beF['version']},{beF['status']},{beF['start'][:16]}) == "
        f"proxy(v{proxyF['version']},{proxyF['status']},{proxyF['start'][:16]})",
    )

    # ---- EventBus / DLQ ------------------------------------------------------------
    ev = be_outbox(appt)
    report("events", "INFO", "; ".join(e.rsplit("|", 1)[0] for e in ev))
    dlq_bot = bot_sql("SELECT count(*) FROM eventbus_ingestdlq")
    dead_be = be_sql(
        "SELECT count(*) FROM appointments_outboxevent WHERE bot_delivery_status='dead'"
    )
    report(
        "dlq",
        "PASS" if dlq_bot == "2" and dead_be == "1" else "FAIL",
        f"bot_ingest_dlq={dlq_bot} (T0=2), backend_dead={dead_be} (T0=1)",
    )
    dedup = bot_sql("SELECT count(*) - count(DISTINCT event_id) FROM eventbus_ingestdedupe")
    report("dedupe", "PASS" if dedup == "0" else "FAIL", f"duplicate dedupe rows={dedup}")
    rem = bot_sql(
        f"SELECT COALESCE(string_agg(kind || ':' || status, ','), 'NONE') FROM booking_bookingreminder "
        f"WHERE ayla_appointment_id='{appt}'"
    )
    report("reminders", "INFO", rem)


def main() -> None:
    corr = str(uuid.uuid4())
    print(f"correlation_id={corr}", flush=True)
    resume = sys.argv[1] if len(sys.argv) > 1 else ""

    svc_id, ayla_svc_id, svc_name, mst_id, mst_name = pick_fixture()
    slots = pick_slots(mst_id, ayla_svc_id)
    report("slots", "PASS", f"candidates={len(slots)} first={slots[0]}")

    if resume:
        appt = resume
        be = be_appointment(appt)
        from datetime import datetime, timezone

        taken_dt = datetime.fromisoformat(be["start"]).astimezone(timezone.utc)
        free = [s for s in slots if datetime.fromisoformat(s).astimezone(timezone.utc) != taken_dt]
        if len(free) < 2:
            fail("slots", "not enough free slots distinct from current appointment time")
        slot2, slot3 = free[0], free[1]
        report(
            "resume",
            "PASS",
            f"appt={appt} current_start={be['start']} v{be['version']} slot2={slot2} slot3={slot3}",
        )
    else:
        appt = phase_create_lookup(slots[0], svc_id, svc_name, mst_id, mst_name, corr)
        slot2, slot3 = slots[1], slots[2]

    phase_reschedule_cancel(appt, slot2, slot3)
    summary()


if __name__ == "__main__":
    main()
