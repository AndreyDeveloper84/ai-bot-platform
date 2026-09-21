"""DRF-2065 — внешний LLM-прокси как документированная зависимость.

17.09 адрес прокси знал только владелец: он жил лишь в ``.env.staging`` на
хосте, ``.env.staging.template`` его не нёс, а пересоздание контейнеров
требовало трёх compose-файлов, которые нигде не были записаны одной строкой.
Замена адреса в 05:55 применилась в 06:24 — полчаса бот ходил по старому.

Сторожа здесь держат две вещи от дрейфа:

* шаблон окружения стенда перечисляет переменные пути к LLM;
* раннбук пересоздания зовёт compose с ТЕМ ЖЕ набором ``-f``, что и деплой,
  и называет каждый сервис стенда, который читает env при создании.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / ".env.staging.template"
RUNBOOK = REPO / "docs" / "runbooks" / "llm-proxy.md"
DEPLOY = REPO / ".github" / "workflows" / "deploy-dev.yml"
STAGING_COMPOSE = REPO / "docker-compose.staging.yml"

#: Сервисы стенда, которые читают LLM-переменные при создании (инцидент 17.09:
#: web/worker/celery-worker/celery-beat/shadow-worker были пересозданы все).
APP_SERVICES = ("web", "worker", "shadow-worker", "celery-worker", "celery-beat")


@pytest.mark.parametrize("var", ["ANTHROPIC_PROXY", "OPENAI_PROXY", "LLM_FALLBACK_ORDER"])
def test_staging_template_names_the_llm_path_variables(var: str) -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(rf"^{var}=", text, re.M), f"{var} нет в .env.staging.template"


def test_runbook_exists() -> None:
    assert RUNBOOK.is_file(), RUNBOOK


def _compose_file_set(text: str) -> set[str]:
    return set(re.findall(r"-f\s+(\S+\.yml)", text))


def _recreate_command(runbook: str) -> str:
    """Команда пересоздания целиком: строки с `\` в конце склеиваются."""
    joined = re.sub(r"\\\n\s*", " ", runbook)
    lines = [line for line in joined.splitlines() if "--force-recreate" in line]
    assert lines, "в раннбуке нет строки пересоздания"
    return " ".join(lines)


def test_runbook_recreates_with_the_same_compose_files_as_deploy() -> None:
    deploy = _compose_file_set(DEPLOY.read_text(encoding="utf-8"))
    # Положительная пара: деплой правда зовёт три файла — иначе сравнение пустое.
    assert len(deploy) == 3, deploy
    recreate = _recreate_command(RUNBOOK.read_text(encoding="utf-8"))
    assert _compose_file_set(recreate) == deploy, recreate


def test_runbook_recreates_every_app_service() -> None:
    compose = STAGING_COMPOSE.read_text(encoding="utf-8")
    for service in APP_SERVICES:
        assert re.search(rf"^  {re.escape(service)}:$", compose, re.M), service
    joined = _recreate_command(RUNBOOK.read_text(encoding="utf-8"))
    missing = [
        s for s in APP_SERVICES if not re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", joined)
    ]
    assert missing == [], missing
