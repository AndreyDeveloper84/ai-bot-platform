#!/usr/bin/env bash
# Что на самом деле лежит внутри мастера в салонном дне Ayla — DRF-1638, DRF-1237.
#
# Вопрос, ради которого это написано. `SalonClient.get_day` в своём docstring
# обещает на каждого мастера `working_intervals`, `breaks`, `absences` и
# `bookings`. Если это правда, то:
#
#   * режим «Все» (DRF-1237, срез A2) строится ОДНИМ удалённым вызовом, а не
#     тремя на каждого мастера, как обходится `build_schedule`;
#   * у DRF-1638 (перерыв теряется при разборе недельного шаблона) есть
#     готовый источник перерывов на том же сервере.
#
# Но в репозитории этого обещания не подтверждает НИЧТО: ключей
# `working_intervals`, `breaks`, `absences` нет ни в одном тесте, фикстуре или
# записанном ответе, а единственный живой потребитель (`booking/
# mirror_reconcile.py`) читает только `masters[].bookings[]`. То есть сегодня
# это пересказ контракта, а не провод. Один живой вызов закрывает вопрос.
#
# ### Почему три исхода, а не два
#
# «Ключа нет» и «ключ есть, список пуст» — РАЗНЫЕ ответы, и сводный счётчик их
# путает. Отсутствие ключа значит «контракт другой». Пустой список значит
# «контракт тот, у этого мастера сегодня пусто» — и тогда пробовать надо на
# дне, где перерыв заведомо есть. Скрипт печатает их раздельно и никогда не
# сворачивает в ноль.
#
# ### Что печатается наружу
#
# Только ИМЕНА ключей и количества. Тело дня несёт `client_id` и
# `client_name` (DRF-1039), поэтому значения не печатаются ни из одной ветки,
# а токен не эхоится вовсе.
#
# Usage:
#   AYLA_BASE_URL=https://api-dev.gobeauty.site \
#   AYLA_INTERNAL_API_TOKEN=… \
#   SALON_ACTOR=bot:max:<max-user-id-of-a-tenant-admin> \
#   SALON_TENANT=<tenant-slug> \
#   [PROBE_DATE=YYYY-MM-DD] \
#   bash scripts/salon_day_shape_probe.sh

set -u

: "${AYLA_BASE_URL:?set AYLA_BASE_URL (host only, no /api/v1)}"
: "${AYLA_INTERNAL_API_TOKEN:?set AYLA_INTERNAL_API_TOKEN}"
: "${SALON_ACTOR:?set SALON_ACTOR — X-External-User-ID of a human who administers the tenant}"
: "${SALON_TENANT:?set SALON_TENANT — the tenant slug for X-Tenant}"

BASE="${AYLA_BASE_URL%/}"
DAY="$BASE/api/v1/tenants/me/day/"
if [ -n "${PROBE_DATE:-}" ]; then
  DAY="${DAY}?date=${PROBE_DATE}"
fi

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

status="$(curl -sS -o "$BODY" -w '%{http_code}' -X GET "$DAY" \
  -H "Authorization: Bearer ${AYLA_INTERNAL_API_TOKEN}" \
  -H "X-External-User-ID: ${SALON_ACTOR}" \
  -H "X-Tenant: ${SALON_TENANT}" \
  -H "X-App-Type: pro")" || { echo "curl failed"; exit 1; }

echo "GET tenants/me/day/${PROBE_DATE:+?date=$PROBE_DATE} -> $status"
if [ "$status" != "200" ]; then
  sed -n 's/.*"code"[[:space:]]*:[[:space:]]*"\([A-Z_]*\)".*/  error code: \1/p' "$BODY" | head -1
  echo "  форма не проверялась: ответ не 200"
  exit 1
fi

python3 - "$BODY" <<'PY'
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    try:
        payload = json.load(fh)
    except Exception as exc:
        sys.exit(f"  тело не разобралось как JSON: {type(exc).__name__}")

data = (payload or {}).get("data")
if not isinstance(data, dict):
    sys.exit("  под 'data' не объект — конверт другой, дальше смотреть нечего")

print(f"  date={data.get('date')!r}  верхние ключи: {sorted(data)}")

masters = data.get("masters")
if not isinstance(masters, list):
    sys.exit("  'masters' не список — контракт разошёлся, останавливаюсь")
print(f"  мастеров: {len(masters)}")
if not masters:
    print("  ПУСТО: у салона нет мастеров в этом дне — форма не проверена,")
    print("  повторить с PROBE_DATE рабочего дня")
    raise SystemExit(0)

WANTED = ("working_intervals", "breaks", "absences", "bookings")

# Три исхода на ключ, и они не сворачиваются друг в друга.
verdict = {k: {"нет ключа": 0, "пусто": 0, "есть": 0} for k in WANTED}
break_fields: set[str] = set()
interval_fields: set[str] = set()

for m in masters:
    if not isinstance(m, dict):
        continue
    for key in WANTED:
        if key not in m:
            verdict[key]["нет ключа"] += 1
        elif not (m.get(key) or []):
            verdict[key]["пусто"] += 1
        else:
            verdict[key]["есть"] += 1
    for row in (m.get("breaks") or []):
        if isinstance(row, dict):
            break_fields |= set(row)
    for row in (m.get("working_intervals") or []):
        if isinstance(row, dict):
            interval_fields |= set(row)

print(f"  ключи первого мастера: {sorted(masters[0]) if isinstance(masters[0], dict) else '—'}")
for key in WANTED:
    v = verdict[key]
    print(f"    {key:<19} нет ключа: {v['нет ключа']:<3} пусто: {v['пусто']:<3} есть: {v['есть']}")

# Имена полей — не значения: времена перерыва здесь не нужны, нужен факт,
# что перерыв на проводе назван и назван чем.
print(f"  поля строки breaks:            {sorted(break_fields) or '— (ни одной непустой строки)'}")
print(f"  поля строки working_intervals: {sorted(interval_fields) or '— (ни одной непустой строки)'}")

print()
if verdict["breaks"]["нет ключа"] == len(masters):
    print("  ЧИТАЕТСЯ: ключа breaks на проводе НЕТ. docstring get_day описывает")
    print("  контракт, которого нет; A2 дешёвым одним вызовом не строится, и у")
    print("  DRF-1638 готового источника перерывов здесь тоже нет.")
elif verdict["breaks"]["есть"]:
    print("  ЧИТАЕТСЯ: перерыв на проводе ЕСТЬ и назван. Тогда A2 строится одним")
    print("  вызовом вместо 3N, а DRF-1638 чинится источником, а не досочинением.")
else:
    print("  ЧИТАЕТСЯ: ключ breaks есть, но сегодня пуст у всех. Это НЕ ответ —")
    print("  повторить с PROBE_DATE дня, где перерыв заведомо стоит.")
PY
