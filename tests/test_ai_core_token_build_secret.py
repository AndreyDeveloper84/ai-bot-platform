"""Страж: токен приватного ``ayla-ai-core`` не оседает в образе и в контейнерах.

С 20.10.2026 ``ayla-ai-core`` снова приватный, и сборке нужен
``GH_DEPLOY_TOKEN``. #2434 передал его build-arg'ом, а Dockerfile записывал
его через ``git config --global`` в ``/root/.gitconfig``. Образ
одностадийный и работает от root, поэтому токен лежал в готовом образе и
читался процессом приложения. Значение build-arg к тому же печатает
``docker history`` и хранит история BuildKit (``docker buildx history``).
На стенде токен жил в ``.env.staging``, который через ``env_file:`` и
``./:/app`` доходит до каждого контейнера.

Теперь так:

* Dockerfile получает токен СЕКРЕТОМ BuildKit
  (``RUN --mount=type=secret,id=gh_deploy_token``) только на время двух
  ``uv sync`` и передаёт его git через ``GIT_CONFIG_*``, ничего не записывая
  на диск;
* compose объявляет секрет ``gh_deploy_token`` из переменной окружения
  ``GH_DEPLOY_TOKEN``, а не build-arg;
* якорь ``x-staging-app-env`` обнуляет ``GH_DEPLOY_TOKEN`` в процессах
  стенда;
* deploy-dev передаёт файл секрета сборки владельца (вне дерева выкладки)
  только команде ``build``.

Проверено вручную на Docker 29.1.3 / compose 2.40.3 с фальшивым токеном:
секрет доходит до RUN, в ``docker history`` и файлах образа 0 вхождений,
без переменной сборка идёт анонимно. Здесь закреплён текст, который это
обеспечивает. Каждая проверка отсутствия идёт после проверки наличия того,
на что она смотрит: пустой или не тот файл не должен давать «нарушений нет».
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import yaml
else:
    yaml = pytest.importorskip("yaml", reason="PyYAML is needed to read the compose files")

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
DEPLOY_DEV_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

SECRET_ID = "gh_deploy_token"
TOKEN_VAR = "GH_DEPLOY_TOKEN"
SECRET_MOUNT = f"--mount=type=secret,id={SECRET_ID}"

#: Сервисы, которые собирают образ бота. Набор закреплён: новый сервис со
#: своим `build:` без секрета упал бы на приватном пине только на стенде.
BUILD_SERVICES = {"web", "worker", "shadow-worker", "celery-worker", "celery-beat"}

#: Ровно столько RUN с `uv sync` в Dockerfile: зависимости и сам проект.
UV_SYNC_RUNS = 2

# Собирается из кусков намеренно: цельная строка, похожая на токен, — это то,
# что detect-secrets обязан ловить (тот же приём, что в
# test_ai_core_fetch_auth_optional.py).
FAKE_VALUE = "gh" + "p_" + ("7" * 36)


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader, не спотыкающийся о compose-теги `!override` / `!reset`."""


def _construct_tagged(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


_TagTolerantLoader.add_multi_constructor("!", _construct_tagged)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader) or {}


def _dockerfile_instructions() -> list[str]:
    """Инструкции Dockerfile: строки-продолжения склеены, комментарии убраны."""

    instructions: list[str] = []
    current = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if current and line.startswith("#"):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        instructions.append(current)
        current = ""
    if current:
        instructions.append(current)
    return instructions


def _uv_sync_runs() -> list[str]:
    runs = [i for i in _dockerfile_instructions() if i.startswith("RUN ") and "uv sync" in i]
    assert len(runs) == UV_SYNC_RUNS, (
        f"В Dockerfile {len(runs)} RUN с `uv sync`, ожидалось {UV_SYNC_RUNS}. Либо "
        "разбор сломан (и проверки ниже ничего не увидят), либо появился новый "
        "`uv sync` — ему тоже нужен секрет, иначе приватный пин не скачается."
    )
    return runs


def test_every_uv_sync_mounts_the_build_secret():
    for run in _uv_sync_runs():
        assert SECRET_MOUNT in run, (
            f"`uv sync` без `{SECRET_MOUNT}`: ayla-ai-core приватный, git не "
            "получит токен, сборка на стенде упадёт на пине."
        )
        assert f"-s /run/secrets/{SECRET_ID}" in run, (
            "Токен должен использоваться только при непустом секрете — иначе "
            "в git уходит https://x-access-token:@github.com/."
        )
        assert "GIT_CONFIG_KEY_0=" in run and "insteadOf" in run, (
            "Секрет смонтирован, но git его не получает: нет "
            "GIT_CONFIG_COUNT/KEY/VALUE с переписыванием URL."
        )


def test_token_is_never_a_build_arg_or_written_to_disk():
    instructions = _dockerfile_instructions()
    assert any(i.startswith("FROM ") for i in instructions), "Dockerfile не разобран"
    _uv_sync_runs()
    for instruction in instructions:
        # empty-assert-ok: наличие инструкций доказано выше — FROM найден
        assert not re.match(rf"ARG\s+{TOKEN_VAR}\b", instruction), (
            f"`ARG {TOKEN_VAR}` вернулся: значение build-arg видно в `docker "
            "history` и в истории BuildKit."
        )
        assert TOKEN_VAR not in instruction, (
            f"`{TOKEN_VAR}` упомянут в инструкции Dockerfile: {instruction[:120]!r}. "
            "Токен приходит только секретом BuildKit."
        )


@pytest.mark.parametrize(
    "forbidden",
    ["set -x", "printenv", "git config", ".git-credentials", "credential.helper"],
)
def test_secret_runs_neither_print_nor_persist_the_token(forbidden: str):
    """`set -x`/`printenv` печатают токен в лог `--progress=plain`, остальное кладёт его на диск."""

    for run in _uv_sync_runs():
        assert forbidden not in run, f"`{forbidden}` в RUN с секретом токена: {run[:120]!r}"
        # empty-assert-ok: _uv_sync_runs сам падает, если RUN с uv sync меньше двух
        assert not re.search(r"(^|[;&|]\s*)env\s*($|[;&|])", run), (
            "Голый `env` в RUN с секретом печатает GIT_CONFIG_KEY_0 вместе с токеном."
        )


def test_dockerfile_has_no_syntax_directive():
    """`# syntax=` тянет фронтенд с Docker Hub, который пилот достаёт плохо."""

    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.lstrip().startswith(("#", "ARG", "FROM")), "Dockerfile не прочитан"
    assert not re.search(r"^#\s*syntax\s*=", text, re.MULTILINE | re.IGNORECASE)


def _build_services() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in (BASE_COMPOSE, STAGING_COMPOSE):
        for name, service in (_load(path).get("services") or {}).items():
            if isinstance(service, dict) and isinstance(service.get("build"), dict):
                found[f"{path.name}:{name}"] = service["build"]
    names = {key.split(":", 1)[1] for key in found}
    assert names == BUILD_SERVICES, (
        f"Сервисы с `build:` — {sorted(names)}, ожидались {sorted(BUILD_SERVICES)}. "
        "Новый сервис со сборкой обязан получить секрет; пропавший — повод "
        "пересмотреть этот страж, а не молча пройти."
    )
    return found


def test_compose_builds_take_the_secret_not_a_build_arg():
    for key, build in _build_services().items():
        assert TOKEN_VAR not in (build.get("args") or {}), (
            f"{key}: `{TOKEN_VAR}` снова в build.args — значение уйдёт в историю BuildKit."
        )
        assert SECRET_ID in (build.get("secrets") or []), (
            f"{key}: нет `build.secrets: [{SECRET_ID}]` — сборка не получит токен."
        )


def test_the_build_secret_comes_from_the_environment():
    secrets = _load(BASE_COMPOSE).get("secrets") or {}
    assert secrets.get(SECRET_ID) == {"environment": TOKEN_VAR}, (
        f"Верхнеуровневый `secrets.{SECRET_ID}` должен браться из `{TOKEN_VAR}` "
        "окружения compose (shell или --env-file), а не из файла в дереве."
    )


def test_staging_processes_never_see_the_token():
    anchor = _load(STAGING_COMPOSE).get("x-staging-app-env") or {}
    assert "DJANGO_SETTINGS_MODULE" in anchor, "якорь x-staging-app-env не найден"
    assert anchor.get(TOKEN_VAR) == "", (
        f"Якорь x-staging-app-env должен обнулять `{TOKEN_VAR}`: `env_file: "
        ".env.staging` иначе донесёт токен в окружение каждого контейнера стенда."
    )


def test_deploy_passes_the_build_env_file_to_build_only():
    text = DEPLOY_DEV_WORKFLOW.read_text(encoding="utf-8")
    builds = re.findall(r"docker compose[^\n]*\\\n(?:[^\n]*\\\n)*[^\n]*\bbuild web\b", text)
    assert len(builds) == 1, f"ожидалась одна команда `build web …`, найдено {len(builds)}"
    assert "BUILD_ENV_ARGS[@]" in builds[0], (
        "Команда сборки в deploy-dev не получает файл секрета сборки владельца."
    )
    assert re.search(
        r"ENV_FILE=\\\"\\\$TOKEN_ENV_FILE\\\" bash scripts/resolve-base-image\.sh", text
    ), "resolve-base-image.sh должен искать токен там же, где сборка."
    assert text.count("BUILD_ENV_ARGS[@]") == 1, (
        "Файл секрета сборки нужен только `build`: `up`/`run` токен не нужен."
    )


def test_no_workflow_passes_the_token_as_a_build_arg():
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, "workflow-файлы не найдены"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert text.strip(), f"{path.name} пуст"
        assert not re.search(rf"--build-arg\s+{TOKEN_VAR}", text), (
            f"{path.name}: `--build-arg {TOKEN_VAR}` — значение уйдёт в историю BuildKit."
        )


def _resolved_with_fake_token(tmp_path: Path) -> tuple[dict, str]:
    if shutil.which("docker") is None:  # pragma: no cover - depends on the host
        pytest.skip("нет docker")
    for path in (BASE_COMPOSE, STAGING_COMPOSE):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / ".env.staging").write_text(f"{TOKEN_VAR}={FAKE_VALUE}\n", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — фиксированная команда, без ввода извне
        [
            "docker",
            "compose",
            "--env-file",
            ".env.staging",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.staging.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the host
        pytest.skip(f"`docker compose config` не выполнился: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout), result.stdout


def test_resolved_stack_carries_no_token(tmp_path: Path):
    """Худший случай: токен лежит в `.env.staging`, который и `--env-file`, и `env_file:`."""

    resolved, raw = _resolved_with_fake_token(tmp_path)
    services = resolved.get("services") or {}
    assert BUILD_SERVICES <= set(services), (
        f"в собранном стеке нет {BUILD_SERVICES - set(services)}"
    )
    assert FAKE_VALUE not in raw, "значение токена попало в итоговую конфигурацию compose"
    for name in sorted(BUILD_SERVICES):
        service = services[name]
        assert (service.get("environment") or {}).get(TOKEN_VAR) == "", (
            f"`{name}`: токен из env_file дошёл до окружения процесса."
        )
        sources = [
            s.get("source") if isinstance(s, dict) else s
            for s in (service.get("build") or {}).get("secrets") or []
        ]
        assert SECRET_ID in sources, f"`{name}`: сборка не получает секрет {SECRET_ID}."
