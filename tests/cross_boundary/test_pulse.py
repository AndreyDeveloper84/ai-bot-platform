"""Пульс стенда — первый тест каталога, и он про предмет, а не про порт.

Контейнер, отвечающий на TCP, — не стенд. Стенд — это каталог, у которого
**применены миграции** и **отвечает база**, и у которого известен SHA. Все
три печатаются в лог рядом с результатом: «стенд поднялся» без них — то же,
что «команда отработала» без предмета.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import GOLDEN_EXTERNAL_USER_ID, Catalog

pytestmark = pytest.mark.cross_boundary


def test_readiness_names_db_and_migrations(catalog: Catalog, http: httpx.Client) -> None:
    """``/api/v1/health/ready/`` — не ``/healthz``: healthcheck контейнера
    зелен и на коде впереди схемы, readiness каталога это различает."""
    resp = http.get(catalog.url("/api/v1/health/ready/"))
    assert resp.status_code == 200, f"readiness HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    checks = body.get("checks") or {}

    print(
        f"\n[CROSS-BOUNDARY] catalog={catalog.base_url} sha={catalog.sha} "
        f"status={body.get('status')} db={checks.get('db')} migrations={checks.get('migrations')}"
    )

    assert body.get("status") == "ok", body
    assert (checks.get("db") or {}).get("ok") is True, checks
    assert (checks.get("migrations") or {}).get("ok") is True, (
        "миграции не применены — код впереди схемы; контейнер при этом здоров"
    )


def test_the_golden_client_resolves_without_seeding(catalog: Catalog, http: httpx.Client) -> None:
    """Каталог создаёт proxy-пользователя на первом запросе — посева клиента
    не нужно. Это проверяется, а не принимается: если каталог однажды начнёт
    требовать заведённого клиента, golden P7 упадёт здесь с ясной причиной,
    а не в середине сценария."""
    resp = http.get(
        catalog.url("/api/v1/internal/me/identity/"),
        headers=catalog.headers(external_user_id=GOLDEN_EXTERNAL_USER_ID),
    )
    assert resp.status_code == 200, f"identity HTTP {resp.status_code}: {resp.text[:200]}"
    data = resp.json().get("data") or {}
    assert data.get("ayla_user_id"), data
    # Положительная стража: это ИМЕННО proxy, а не случайно совпавший
    # настоящий аккаунт — иначе golden ходил бы от чужого имени.
    assert data.get("is_proxy") is True, data


def test_a_wrong_token_is_refused(catalog: Catalog, http: httpx.Client) -> None:
    """Положительная стража к авторизации: стенд проверяет токен, а не
    пускает всех. Без неё зелёный P7 на стенде с пустым токеном ничего бы
    не доказывал про границу."""
    resp = http.get(
        catalog.url("/api/v1/internal/me/identity/"),
        headers={
            "Authorization": "Bearer not-the-token",
            "X-External-User-ID": GOLDEN_EXTERNAL_USER_ID,
        },
    )
    assert resp.status_code in (401, 403), resp.status_code
