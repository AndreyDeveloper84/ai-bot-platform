"""Чтения admin Mini App называют 403 каталога, а не падают в 500 (DRF-2087).

Три читателя через ``_ayla_read_actor`` — кадр дня (``views_salon_frame``),
вытесняемые записи (``views_schedule_impact``), исключения мастера
(``views_master_exceptions``) — ловили только ``SalonNotConfigured`` и
``SalonUnavailable``. ``SalonForbidden`` (403 каталога: актор салона — не
администратор этого салона в каталоге, ``salon_client._raise_for_status``)
летел наружу и становился 500 — «сломалось», хотя это факт настройки, и у
него есть «что сделать». Действия (отмена, поиск клиентов) 403 называют
давно; чтения отстали.

Здесь три вещи:

* **403 по имени** — ``salon_forbidden`` с указанием, что сделать; ``500``
  здесь красен до правки во всех трёх;
* **страховочная сеть** — любой другой ``Salon*`` (401, 404, следующий
  подкласс, которого сегодня нет) → 503 ``schedule_unavailable`` с классом
  отказа в логе, а не 500;
* **сторож-перепись по классу** — каждый ``try`` вокруг вызова каталога в
  модулях, читающих через ``_ayla_read_actor``, обязан покрывать ВЕСЬ набор
  ``Salon*``. Набор берётся из иерархии (``SalonAPIError.__subclasses__``
  рекурсивно), а не из списка имён: новый подкласс без ловца — красный
  тест, а не 500 на стенде. Ловля базы покрывает потомков.

Красное до правки объявлено поимённо до прогона: 7 красных / 2 контроля
(плюс нижняя граница переписи — третий зелёный, не про предмет).
Коды: ``client.raise_request_exception = False`` — иначе необработанное
исключение представления прилетело бы в тест исключением, а не тем 500,
которое видит экран.
"""

from __future__ import annotations

import ast
import importlib
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

import apps.admin_api as admin_api_pkg
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla import salon_client as salon_client_module
from apps.integrations.ayla.salon_client import (
    SalonAPIError,
    SalonForbidden,
    SalonUnauthorized,
    SalonUnavailable,
)
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

READERS = ("views_salon_frame", "views_schedule_impact", "views_master_exceptions")

#: Где живёт само чтение каталога для читателя. С DRF-2118 предпросмотр
#: impact читает ``services.schedule_impact`` (одно чтение на вьюху и на
#: уведомление-решение), вьюха — обёртка; переписи и подмены — по чтению.
READ_MODULE = {"views_schedule_impact": "services.schedule_impact"}


def _read_module(reader: str) -> str:
    return READ_MODULE.get(reader, reader)


# ─── двойник каталога: один ответ на любой метод чтения ──────────────────────


class _FakeSalonClient:
    """Отвечает одним и тем же на любое чтение — предмет здесь отказ, не форма."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def _answer(self, payload):
        if self.exc is not None:
            raise self.exc
        return payload

    def get_day(self, **kwargs):
        return self._answer(
            {
                "date": "2026-09-10",
                "generated_at": "2026-09-10T06:00:00+03:00",
                "closures": [],
                "summary": {},
                "masters": [],
            }
        )

    def get_schedule_impact(self, **kwargs):
        return self._answer(
            {
                "specialist_id": "sp-1",
                "start_at": "2026-09-15T10:00:00+03:00",
                "end_at": "2026-09-15T14:00:00+03:00",
                "timezone": "Europe/Moscow",
                "bookings": [],
            }
        )

    def list_schedule_exceptions(self, **kwargs):
        return self._answer([])

    def list_time_off(self, **kwargs):
        return self._answer([])

    def list_closures(self, **kwargs):
        return self._answer([])


@pytest.fixture
def ayla(monkeypatch, settings):
    settings.BOOKING_VIA_AYLA_REST = True

    def use(exc: Exception | None = None) -> _FakeSalonClient:
        fake = _FakeSalonClient(exc)
        # Имя В КАЖДОМ модуле: они связывают ``get_salon_client`` на импорте.
        for name in READERS:
            monkeypatch.setattr(
                f"apps.admin_api.{_read_module(name)}.get_salon_client", lambda: fake
            )
        return fake

    return use


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=2087,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Ольга Синхронная",
            ayla_user_id=uuid.uuid4(),
        )
    )


def _read(client: Client, reader: str, master: CatalogMaster, *, user_id: str = "5001"):
    client.raise_request_exception = False
    auth = init_data_header(user_id)
    if reader == "views_salon_frame":
        return client.get(reverse("admin_api:salon_day_frame"), {}, HTTP_AUTHORIZATION=auth)
    if reader == "views_schedule_impact":
        return client.get(
            reverse("admin_api:master_schedule_impact", args=[str(master.id)]),
            {"date": "2026-09-15", "from": "10:00", "to": "14:00"},
            HTTP_AUTHORIZATION=auth,
        )
    return client.get(
        reverse("admin_api:master_exceptions", args=[str(master.id)]),
        {"from": "2026-09-15", "to": "2026-09-21"},
        HTTP_AUTHORIZATION=auth,
    )


# ─── 403 по имени ────────────────────────────────────────────────────────────


class TestForbiddenIsNamed:
    @pytest.mark.parametrize("reader", READERS)
    def test_forbidden_is_403_by_name(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
        reader: str,
    ) -> None:
        ayla(exc=SalonForbidden("actor is not an administrator of this salon"))

        resp = _read(client, reader, synced_master)

        assert resp.status_code == 403, (reader, resp.status_code)
        body = resp.json()
        assert body["error"] == "salon_forbidden"
        # «Что сделать» — не голый статус: отказ называет ход оператора.
        assert "provision_salon_admin" in body["detail"]

    def test_an_unnamed_salon_error_is_503_not_500(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Страховочная сеть: 401 (и любой будущий подкласс) — 503 по имени."""
        ayla(exc=SalonUnauthorized("service token rejected"))

        resp = _read(client, "views_salon_frame", synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"


class TestTheHappyAndTheOldRefusalStillHold:
    def test_a_linked_actor_reads_200(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Положительная пара к 403: связанный владелец читает день."""
        ayla()
        assert _read(client, "views_salon_frame", synced_master).status_code == 200

    def test_unavailable_is_still_503(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(exc=SalonUnavailable("upstream 502"))
        resp = _read(client, "views_salon_frame", synced_master)
        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"


# ─── сторож-перепись: каждый читатель ловит весь набор Salon* — по классу ────


def _salon_error_classes() -> dict[str, type[BaseException]]:
    """Все ``Salon*`` из иерархии ``SalonAPIError`` — по подклассам, не по именам."""
    found: dict[str, type[BaseException]] = {SalonAPIError.__name__: SalonAPIError}
    stack: list[type[BaseException]] = [SalonAPIError]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            if sub.__name__ not in found:
                found[sub.__name__] = sub
                stack.append(sub)
    return found


def _reader_modules() -> list[str]:
    """Модули admin_api, читающие каталог через ``_ayla_read_actor``."""
    root = Path(admin_api_pkg.__file__).parent
    names = []
    for path in sorted([*root.glob("views_*.py"), *root.glob("services/*.py")]):
        if "_ayla_read_actor(" in path.read_text(encoding="utf-8"):
            rel = path.relative_to(root).with_suffix("")
            names.append(".".join(rel.parts))
    return names


def _handler_names(handler: ast.ExceptHandler) -> list[str]:
    node = handler.type
    if node is None:
        return ["*"]
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    return [p.id for p in parts if isinstance(p, ast.Name)]


def _catalog_try_blocks(tree: ast.AST) -> list[ast.Try]:
    """``try`` вокруг вызова каталога — тот, где хоть один ``except`` про Salon*."""
    blocks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = [n for h in node.handlers for n in _handler_names(h)]
        if any(n.startswith("Salon") for n in names):
            blocks.append(node)
    return blocks


class TestEveryReaderCatchesTheWholeSalonFamily:
    def test_the_census_is_not_empty(self) -> None:
        """Нижняя граница переписи: пустой скан читался бы как «все ловят»."""
        assert len(_reader_modules()) >= 3
        assert len(_salon_error_classes()) >= 5

    @pytest.mark.parametrize("reader", READERS)
    def test_census_covers_every_salon_error(self, reader: str) -> None:
        assert _read_module(reader) in _reader_modules(), (
            "читатель выпал из переписи — сторож ослеп"
        )
        module = importlib.import_module(f"apps.admin_api.{_read_module(reader)}")
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        family = _salon_error_classes()
        # Присутствие раньше отсутствия (negative_assert_guard): «ничего не
        # пропущено» на пустой иерархии было бы пустой правдой.
        assert len(family) >= 5, "иерархия Salon* пуста — сканер слеп"
        blocks = _catalog_try_blocks(tree)
        assert blocks, f"{reader}: ни одного try вокруг вызова каталога — сканер слеп"

        for block in blocks:
            covered: set[str] = set()
            for handler in block.handlers:
                for name in _handler_names(handler):
                    if name == "*":
                        covered |= set(family)
                        continue
                    caught = family.get(name) or getattr(salon_client_module, name, None)
                    if caught is None:
                        continue
                    covered |= {n for n, cls in family.items() if issubclass(cls, caught)}
            assert covered, f"{reader}:{block.lineno}: ни одного Salon* в except — не тот try"
            missing = sorted(set(family) - covered)
            assert not missing, (
                f"{reader}:{block.lineno} не ловит {missing} — на стенде это 500 без имени"
            )
