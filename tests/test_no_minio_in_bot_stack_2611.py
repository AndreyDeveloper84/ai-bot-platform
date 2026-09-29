"""DRF-2611 — MinIO ушёл из стека бота; сторож держит ловушку двух одноимённых конфигураций.

Решение владельца 29.09: «убрать неиспользуемый MinIO из стека бота». Ни одна
строка бота в объектное хранилище не писала — MinIO существовал только для
того, чтобы проверка готовности (`_ping_minio`) подтверждала, что он
существует.

Ловушка. ``S3_ENDPOINT_URL`` / ``S3_BUCKET`` / ``S3_ACCESS_KEY`` живут в ДВУХ
независимых конфигурациях с одинаковыми именами:

* настройка Django из окружения контейнера — её читал только пинг, она ушла;
* переменные скриптов резервного копирования, читаемые из отдельного файла
  ``/etc/formula_tela/backup.env`` (``scripts/backup/**``). Их боевой адрес —
  Yandex Object Storage, не compose-сервис ``minio``. Резервное копирование и
  архив WAL обязаны продолжать работать.

Поэтому сторож краснеет, если ``S3_ENDPOINT_URL`` или сервис ``minio`` снова
появятся в настройках Django или в compose бота, и **намеренно не читает**
``scripts/backup/**``. Без второй половины он запретил бы резервное
копирование. Обе половины проверены подменой ниже.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Где MinIO/S3-настройке бота больше не место.
WATCHED_GLOBS = ("config/settings/**/*.py", "docker-compose*.yml")
#: Где те же имена законны — другая конфигурация, её не трогаем.
EXEMPT_PREFIXES = ("scripts/backup/",)

_S3_SETTING = re.compile(r"\bS3_ENDPOINT_URL\b")
_MINIO_SERVICE = re.compile(r"^\s{2}minio:\s*$|image:\s*['\"]?minio/")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def scan(root: Path) -> tuple[list[str], list[str]]:
    """(прочитанные файлы, ``файл:строка`` каждого возвращения MinIO/S3) под ``root``.

    Прочитанное возвращается вместе с находками: «нарушителей нет» без
    доказательства, что сканер что-то прочитал, прошло бы и на пустом дереве.
    """
    read: list[str] = []
    found: list[str] = []
    for pattern in WATCHED_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            read.append(rel)
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _is_comment(line):
                    continue
                if _S3_SETTING.search(line) or _MINIO_SERVICE.search(line):
                    found.append(f"{rel}:{i}")
    return read, found


def test_the_bot_stack_carries_no_minio_and_no_s3_setting() -> None:
    read, found = scan(REPO_ROOT)
    # Присутствие впереди: обход прочитал и настройки, и compose.
    assert "config/settings/base.py" in read
    assert "docker-compose.staging.yml" in read
    assert found == []


def test_the_backup_scripts_keep_their_own_s3_configuration() -> None:
    """Вторая половина ловушки на живом дереве: скрипты бэкапа своё сохранили."""
    backup = REPO_ROOT / "scripts" / "backup"
    readers = [p for p in backup.glob("*.sh") if "S3_ENDPOINT_URL" in p.read_text(encoding="utf-8")]
    assert readers, "скрипты резервного копирования больше не читают S3_ENDPOINT_URL"


class TestTheGuardDistinguishesTheTwoConfigurations:
    """Подмена в обе стороны на подложенном дереве."""

    def _tree(self, tmp_path: Path) -> Path:
        (tmp_path / "config" / "settings").mkdir(parents=True)
        (tmp_path / "config" / "settings" / "base.py").write_text(
            "# DRF-2611: no S3_ENDPOINT_URL setting (comment is fine)\nDEBUG = False\n",
            encoding="utf-8",
        )
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n  web:\n    image: bot\n", encoding="utf-8"
        )
        (tmp_path / "scripts" / "backup").mkdir(parents=True)
        (tmp_path / "scripts" / "backup" / "pg_base_backup.sh").write_text(
            'S3_ENDPOINT_URL="${S3_ENDPOINT_URL:?}"\n', encoding="utf-8"
        )
        return tmp_path

    def test_a_clean_tree_with_backup_s3_is_green(self, tmp_path: Path) -> None:
        read, found = scan(self._tree(tmp_path))
        assert read == ["config/settings/base.py", "docker-compose.yml"]
        assert found == []

    def test_s3_setting_back_in_settings_is_red(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        (root / "config" / "settings" / "base.py").write_text(
            'S3_ENDPOINT_URL = "http://minio:9000"\n', encoding="utf-8"
        )
        assert scan(root)[1] == ["config/settings/base.py:1"]

    def test_minio_service_back_in_compose_is_red(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        (root / "docker-compose.staging.yml").write_text(
            "services:\n  minio:\n    image: minio/minio:latest\n", encoding="utf-8"
        )
        assert scan(root)[1] == [
            "docker-compose.staging.yml:2",
            "docker-compose.staging.yml:3",
        ]

    def test_s3_in_backup_scripts_stays_green(self, tmp_path: Path) -> None:
        root = self._tree(tmp_path)
        (root / "scripts" / "backup" / "restore_pitr.sh").write_text(
            'aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls\n', encoding="utf-8"
        )
        read, found = scan(root)
        # Скрипт бэкапа не читается вовсе — и потому не может покраснеть.
        assert "config/settings/base.py" in read
        assert not any(r.startswith("scripts/backup/") for r in read)
        assert found == []
