"""Стенд cross-boundary: каталог как отдельный процесс, бот как клиент.

Здесь НЕТ ``skipif`` — намеренно. Нет стенда → тесты **падают** с названной
причиной. Пропуск модуля целиком даёт зелёный job с нулём тестов, а пустой
прогон читается как зелень: ровно то, чего этот каталог не допускает
(``README.md`` рядом).

Переменные — не ``AYLA_BASE_URL``: обычный прогон нельзя случайно направить
в каталог, а этот — случайно в пилот.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

CATALOG_URL_ENV = "CROSS_BOUNDARY_CATALOG_URL"
CATALOG_TOKEN_ENV = "CROSS_BOUNDARY_CATALOG_TOKEN"
CATALOG_SHA_ENV = "CROSS_BOUNDARY_CATALOG_SHA"

#: Клиент, от имени которого идёт golden. Каталог создаёт proxy-пользователя
#: на первом же запросе (``users/services.py::resolve_external_user`` —
#: ``get_or_create``), поэтому посева клиента не нужно. Имя фиксировано,
#: чтобы стенд был воспроизводим, а не «какой-то bot:123».
GOLDEN_EXTERNAL_USER_ID = "bot:golden-p7"


@dataclass(frozen=True)
class Catalog:
    base_url: str
    token: str
    sha: str

    def headers(self, *, external_user_id: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if external_user_id:
            h["X-External-User-ID"] = external_user_id
        return h

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    """Адрес стенда — или падение с именем недостающей переменной."""
    missing = [name for name in (CATALOG_URL_ENV, CATALOG_TOKEN_ENV) if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "cross-boundary: стенд не задан — переменные не выставлены: "
            + ", ".join(missing)
            + ". Это падение, а не пропуск: пустой прогон читается как зелень."
        )
    return Catalog(
        base_url=os.environ[CATALOG_URL_ENV],
        token=os.environ[CATALOG_TOKEN_ENV],
        # SHA не обязателен для локального запуска, но в CI обязан быть:
        # пульс печатает его рядом с результатом — предмет рядом с числом.
        sha=os.environ.get(CATALOG_SHA_ENV, "<не задан>"),
    )


@pytest.fixture(scope="session")
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=15.0) as client:
        yield client


@pytest.fixture
def bot_points_at_catalog(settings, catalog: Catalog) -> Catalog:
    """Клиенты бота читают ``settings.AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN``.

    Переопределяются на время теста и только из переменных стенда — так
    golden ходит через настоящий код клиента бота, а не через ``httpx``
    напрямую, и любая правка клиента (заголовки, версия пути, идемпотентность)
    проходит через границу здесь же.
    """
    settings.AYLA_BASE_URL = catalog.base_url
    settings.AYLA_INTERNAL_API_TOKEN = catalog.token
    return catalog
