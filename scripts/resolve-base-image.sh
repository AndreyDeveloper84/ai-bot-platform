#!/usr/bin/env bash
# Откуда взять базовый слой образа бота. DRF-1440.
#
# Печатает в stdout ОДНУ строку — ссылку на образ, годную для
# `PYTHON_BASE_IMAGE`. Всё повествование идёт в stderr, так что вызов
# безопасно подставлять:
#
#     export PYTHON_BASE_IMAGE="$(scripts/resolve-base-image.sh)"
#     docker compose -p ayla-bot-staging \
#       -f docker-compose.yml -f docker-compose.staging.yml \
#       build web worker shadow-worker celery-worker celery-beat
#
# ## Зачем
#
# Образ бота собирается НА ПИЛОТЕ, а пилот достаёт Docker Hub плохо:
# анонимный token-endpoint Hub с этого IP отвечает через раз, и 31.08 две
# подряд выкладки умерли на нём до единой скомпилированной строки.
# Измерения — в шапке Dockerfile и в .github/workflows/mirror-base-image.yml.
# Зеркало в GHCR (тот же digest, другой реестр) отвечает в 20 раз быстрее.
#
# ## Почему это скрипт, а не шаг workflow
#
# У бэкенда каскад живёт внутри ci.yml, потому что там сборку запускает
# только CI. У бота настоящий контур `ayla-bot-staging` пересобирают РУКОЙ
# по ssh, а `deploy-dev.yml` трогает только `ai-bot-platform-dev`. Каскад,
# спрятанный в workflow, не помог бы ровно в том случае, ради которого он
# нужен. Поэтому — скрипт в репозитории, который зовут и оттуда, и руками.
#
# ## Порядок предпочтений
#
# Каждая ветка строго лучше следующей, а последняя — ровно то, что
# происходило до этой правки. Хуже стать не может.
#
#   1. PYTHON_BASE_IMAGE уже задан снаружи — уважаем и не спорим
#   2. зеркало в GHCR, анонимно
#   3. зеркало в GHCR, после docker login токеном, УЖЕ лежащим на коробке
#   4. зеркало, уже лежащее в локальном хранилище образов (работает офлайн)
#   5. апстрим, уже лежащий в локальном хранилище образов
#   6. апстрим с Docker Hub — прежнее поведение, с предупреждением
#
# ## Переменные окружения
#
#   DOCKER_CMD   чем звать docker (умолчание `docker`; на пилоте
#                пригодится `sudo docker`)
#   ENV_FILE     где искать GH_DEPLOY_TOKEN для ветки 3 (умолчание — .env
#                рядом с репозиторием)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_CMD="${DOCKER_CMD:-docker}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

say() { printf '%s\n' "$*" >&2; }

# --- 1. Явный выбор оператора выигрывает у всего ---------------------------
if [ -n "${PYTHON_BASE_IMAGE:-}" ]; then
  say "PYTHON_BASE_IMAGE задан снаружи — беру как есть"
  printf '%s\n' "$PYTHON_BASE_IMAGE"
  exit 0
fi

# --- Ссылка на апстрим: единственный источник правды — Dockerfile ----------
UPSTREAM="$(grep -m1 -E '^ARG PYTHON_BASE_IMAGE=' "${REPO_ROOT}/Dockerfile" | cut -d= -f2- || true)"
UPSTREAM="${UPSTREAM:-python:3.12-slim}"

# Владелец пакета в GHCR — из origin, чтобы форк не пытался тянуть чужое.
ORIGIN="$(git -C "$REPO_ROOT" config --get remote.origin.url 2>/dev/null || true)"
OWNER="$(printf '%s' "$ORIGIN" | sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#')"
OWNER="$(printf '%s' "${OWNER:-AndreyDeveloper84}" | tr '[:upper:]' '[:lower:]')"
MIRROR="ghcr.io/${OWNER}/ayla-bot-python:${UPSTREAM##*:}"

say "upstream=${UPSTREAM}  mirror=${MIRROR}"

try_pull() {
  local ref="$1" a
  for a in 1 2 3; do
    if $DOCKER_CMD pull "$ref" >/dev/null 2>&1; then return 0; fi
    say "  pull $ref — попытка $a/3 не удалась"
    sleep $((a * 10))
  done
  return 1
}

BASE=""
SOURCE=""

# --- 2. Зеркало, анонимно --------------------------------------------------
if try_pull "$MIRROR"; then
  BASE="$MIRROR"; SOURCE="ghcr (анонимно)"
# --- 3. Зеркало, с логином токеном, который уже лежит на коробке -----------
elif [ -f "$ENV_FILE" ] && grep -q '^GH_DEPLOY_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  # Пакет приватный (рекомендуется публичный — это копия публичного образа,
  # секрета не требует). Логинимся токеном, который на этой коробке УЖЕ
  # есть; ничего нового здесь не пишется, значение никуда не печатается.
  # Токену нужна область `read:packages`.
  say "анонимный pull из GHCR не прошёл — пробую с логином"
  if grep -m1 '^GH_DEPLOY_TOKEN=' "$ENV_FILE" | cut -d= -f2- \
       | $DOCKER_CMD login ghcr.io -u "$OWNER" --password-stdin >/dev/null 2>&1 \
     && try_pull "$MIRROR"; then
    BASE="$MIRROR"; SOURCE="ghcr (с логином)"
  fi
fi

# --- 4-5. Локальное хранилище образов: BuildKit резолвит без реестра -------
if [ -z "$BASE" ] && $DOCKER_CMD image inspect "$MIRROR" >/dev/null 2>&1; then
  BASE="$MIRROR"; SOURCE="локальная копия зеркала"
fi
if [ -z "$BASE" ] && $DOCKER_CMD image inspect "$UPSTREAM" >/dev/null 2>&1; then
  BASE="$UPSTREAM"; SOURCE="локальная копия апстрима"
fi

# --- 6. Прежнее поведение --------------------------------------------------
if [ -z "$BASE" ]; then
  say "::warning::откатываюсь на Docker Hub за ${UPSTREAM} — это тот самый путь, который отказал 31.08. Проверьте, что зеркало ${MIRROR} существует и его видно (workflow: mirror-base-image)."
  BASE="$UPSTREAM"; SOURCE="docker hub (откат)"
fi

say "собираем на ${BASE}  — источник: ${SOURCE}"
# Провенанс в журнал прогона: ровно те байты, на которых собираем.
$DOCKER_CMD image inspect --format '{{index .RepoDigests 0}}' "$BASE" >&2 2>/dev/null \
  || say "(repo digest для ${BASE} не записан)"

printf '%s\n' "$BASE"
