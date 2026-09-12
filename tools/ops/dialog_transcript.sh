#!/bin/bash
# Расшифровка диалога с пилота бота со служебным следом каждой реплики (DRF-1754).
# Обёртка над management-командой `dialog_transcript` в контейнере бота: только чтение,
# телефоны/почта маскируются в команде, id канала — восемь знаков md5.
#
#   bash tools/ops/dialog_transcript.sh --list [--since 24h]
#   bash tools/ops/dialog_transcript.sh --conv <uuid> [--since 3h] [--out docs/dialogs]
#
# --out пишет файл ЛОКАЛЬНО (docs/dialogs/ в .gitignore); в контейнер каталог не передаётся.
set -euo pipefail
HOST=${DIALOG_HOST:-taximeter@176.119.159.141}
WEB=${DIALOG_WEB_CONTAINER:-ayla-bot-staging-web-1}
OUT=""; ARGS=()
while [ $# -gt 0 ]; do case "$1" in
  --out) OUT="$2"; shift 2;;
  *) ARGS+=("$1"); shift;;
esac; done
echo "# host=$HOST container=$WEB $(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  CONV=""; for ((i=0; i<${#ARGS[@]}; i++)); do [ "${ARGS[$i]}" = "--conv" ] && CONV="${ARGS[$((i+1))]}"; done
  [ -z "$CONV" ] && { echo "--out требует --conv <uuid>" >&2; exit 2; }
  FILE="$OUT/$(printf '%s' "$CONV" | md5sum | cut -c1-8)_$(date -u +%Y-%m-%d_%H%M).txt"
  ssh -o BatchMode=yes "$HOST" "docker exec $WEB python manage.py dialog_transcript ${ARGS[*]}" | tee "$FILE"
  echo "записано: $FILE" >&2
else
  ssh -o BatchMode=yes "$HOST" "docker exec $WEB python manage.py dialog_transcript ${ARGS[*]}"
fi
