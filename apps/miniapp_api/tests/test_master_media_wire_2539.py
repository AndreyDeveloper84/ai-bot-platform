"""Адрес фото мастера на проводе — наш путь, без хоста хранилища и без подписи (DRF-2539).

Узел, который владелец велел держать в том же PR:

* (а) хост — не сеть контейнеров (не ``minio``, не ``localhost``, не голое
  имя) ИЛИ адрес относителен к нашей ручке;
* (б) нет ни ``X-Amz-Expires``/``X-Amz-Signature`` (подпись v4), ни
  ``Signature=``/``Expires=``/``AWSAccessKeyId=`` (v2) — обе формы;

и на ВСЕХ местах, где фото мастера уходит на провод.

Как держится «на всех местах»:

* w1 — перепись: каждый ключ ``"photo_url"`` в словаре-литерале кода бота
  получает значение ТОЛЬКО из ``master_photo_path(...)``; ключ
  ``"image_url"`` — только в ``master_media.py``. Новое место сериализации,
  отдающее сырое зеркало, краснеет здесь. Положительный контроль: перепись
  находит известные места (не меньше десяти) и ловит подложенное сырое.
* w2 — живые сериализаторы (те, что вызываются без запроса) на мастере,
  чьё зеркало хранит адрес в форме стенда: на выходе (а) и (б).
* w3 — сам помощник: форма пути, версия не зависит от подписи (синк не
  сбивает кэш), зависит от объекта (новое фото — новая версия), пусто → "".

Сырые формы взяты из ``FieldFile.url`` с настройками ``prod.py`` каталога
(``querystring_auth`` по умолчанию, подпись v2) и из v4 — на случай смены
``AWS_S3_SIGNATURE_VERSION``.
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from apps.miniapp_api.master_media import (
    MEDIA_PREFIX,
    master_photo_path,
    outward_portfolio,
    outward_portfolio_item,
    portfolio_image_path,
)

APPS = Path(__file__).resolve().parents[2]

STORAGE_FORMS = {
    "v2_minio": (
        "http://minio:9000/beautygo-media/specialists/avatars/a.jpg"
        "?AWSAccessKeyId=KEY&Signature=SIG%3D&Expires=1790000000"
    ),
    "v4_minio": (
        "http://minio:9000/beautygo-media/specialists/avatars/a.jpg"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=KEY%2F20260929%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20260929T000000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Signature=abc"
    ),
    "localhost": "http://localhost:9000/beautygo-media/specialists/avatars/a.jpg?Signature=S&Expires=1",
    "bare_name": "http://storage/beautygo-media/specialists/avatars/a.jpg",
}

SIGNATURE_MARKERS = (
    "X-Amz-Expires",
    "X-Amz-Signature",
    "X-Amz-Credential",
    "Signature=",
    "Expires=",
    "AWSAccessKeyId=",
)


def assert_outward(value: str) -> None:
    """(а) и (б) для одного значения с провода."""
    for marker in SIGNATURE_MARKERS:
        assert marker not in value, f"подпись на проводе: {marker} в {value!r}"
    if not value:
        return
    parts = urlsplit(value)
    # (а): наш путь, относительный — ни схемы, ни хоста вовсе.
    assert not parts.scheme and not parts.netloc, f"хост на проводе: {value!r}"
    assert value.startswith(MEDIA_PREFIX + "/"), value


# ─── w3 — помощник ──────────────────────────────────────────────────────────


class TestW3Helper:
    @pytest.mark.parametrize("raw", list(STORAGE_FORMS.values()), ids=list(STORAGE_FORMS))
    def test_every_storage_form_becomes_our_path(self, raw) -> None:
        mid = uuid.uuid4()
        out = master_photo_path(mid, raw)
        assert_outward(out)
        assert out.startswith(f"{MEDIA_PREFIX}/{mid}/photo?v=")
        item = uuid.uuid4()
        out = portfolio_image_path(mid, item, raw)
        assert_outward(out)
        assert out.startswith(f"{MEDIA_PREFIX}/{mid}/portfolio/{item}/image?v=")

    @pytest.mark.parametrize("raw", ["", None, "   "])
    def test_no_photo_is_empty(self, raw) -> None:
        assert master_photo_path(uuid.uuid4(), raw) == ""
        assert portfolio_image_path(uuid.uuid4(), uuid.uuid4(), raw) == ""

    def test_version_ignores_signature_churn(self) -> None:
        mid = uuid.uuid4()
        a = master_photo_path(mid, STORAGE_FORMS["v2_minio"])
        b = master_photo_path(
            mid, STORAGE_FORMS["v2_minio"].replace("Expires=1790000000", "Expires=1790003600")
        )
        assert a == b

    def test_new_object_new_version(self) -> None:
        mid = uuid.uuid4()
        a = master_photo_path(mid, STORAGE_FORMS["v2_minio"])
        b = master_photo_path(mid, STORAGE_FORMS["v2_minio"].replace("a.jpg", "a_Xy12.jpg"))
        assert a != b

    def test_portfolio_body_is_rewritten_item_by_item(self) -> None:
        mid = uuid.uuid4()
        item = str(uuid.uuid4())
        body = {
            "items": [{"id": item, "image_url": STORAGE_FORMS["v2_minio"], "created_at": "x"}],
            "count": 1,
            "limit": 10,
        }
        out = outward_portfolio(mid, body)
        assert out["count"] == 1 and out["limit"] == 10
        assert_outward(out["items"][0]["image_url"])
        assert item in out["items"][0]["image_url"]
        assert_outward(outward_portfolio_item(mid, body["items"][0])["image_url"])
        # Исходный ответ каталога не тронут — переписывается копия.
        assert body["items"][0]["image_url"] == STORAGE_FORMS["v2_minio"]


# ─── w1 — перепись мест сериализации ───────────────────────────────────────


def _source_files() -> list[Path]:
    return [
        p
        for p in APPS.rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts and not p.name.startswith("test_")
    ]


def _is_helper_call(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (isinstance(fn, ast.Name) and fn.id == name) or (
        isinstance(fn, ast.Attribute) and fn.attr == name
    )


def census(tree: ast.AST, rel: str) -> tuple[int, list[str]]:
    """(сколько ключей нашлось, какие отдают сырое)."""
    found = 0
    raw: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value in ("photo_url", "image_url")):
                continue
            found += 1
            if key.value == "image_url" and not rel.endswith("miniapp_api/master_media.py"):
                raw.append(f"{rel}:{node.lineno} image_url")
            elif key.value == "photo_url" and not _is_helper_call(value, "master_photo_path"):
                raw.append(f"{rel}:{value.lineno} photo_url")
    return found, raw


class TestW1Census:
    def test_every_photo_url_key_goes_through_the_helper(self) -> None:
        total = 0
        offenders: list[str] = []
        for path in _source_files():
            rel = path.relative_to(APPS.parent).as_posix()
            found, raw = census(ast.parse(path.read_text(encoding="utf-8")), rel)
            total += found
            offenders += raw
        assert offenders == [], "сырое фото мастера на проводе:\n" + "\n".join(offenders)
        # Положительный контроль: перепись видит известные места, а не пустоту.
        # admin (3), verify, marketplace, dashboard, master (3), profile_card, miniapp.
        assert total >= 11, total

    def test_census_catches_a_raw_value(self) -> None:
        src = 'def f(m):\n    return {"id": m.id, "photo_url": m.photo_url}\n'
        found, raw = census(ast.parse(src), "apps/x/views.py")
        assert found == 1 and raw == ["apps/x/views.py:2 photo_url"]

    def test_census_catches_image_url_outside_master_media(self) -> None:
        src = 'def f(i):\n    return {"image_url": i["image_url"]}\n'
        _found, raw = census(ast.parse(src), "apps/x/views.py")
        assert raw == ["apps/x/views.py:2 image_url"]


# ─── w2 — живые сериализаторы ───────────────────────────────────────────────


@pytest.fixture
def master(db):
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="wire-2539", name="Салон", timezone="Europe/Moscow")
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        name="Анна Петрова",
        is_active=True,
        photo_url=STORAGE_FORMS["v2_minio"],
    )


@pytest.mark.django_db
class TestW2LiveSerializers:
    def test_customer_masters(self, master) -> None:
        from apps.miniapp_api.views import _master_to_dict

        assert_outward(_master_to_dict(master)["photo_url"])

    def test_marketplace_card(self, master) -> None:
        from apps.marketplace.dto import MasterCard
        from apps.marketplace.views import _card_to_dict

        card = MasterCard(
            tenant_id=master.tenant_id,
            master_id=master.id,
            name=master.name,
            specialization="",
            rating=Decimal("4.9"),
            photo_url=master.photo_url,
            city="",
        )
        assert_outward(_card_to_dict(card)["photo_url"])

    def test_admin_list_and_detail(self, master) -> None:
        from apps.admin_api.views import _detail_payload, _row_to_list_item
        from apps.tenancy.context import tenant_scope

        with tenant_scope(master.tenant):
            assert_outward(_row_to_list_item(master, 0)["photo_url"])
            assert_outward(_detail_payload(master)["photo_url"])

    def test_admin_verify_row(self, master) -> None:
        from apps.admin_api.views_master_verify import _row

        assert_outward(_row(master)["photo_url"])

    def test_master_card(self, master) -> None:
        from apps.master_api.views import _master_card
        from apps.tenancy.context import tenant_scope

        with tenant_scope(master.tenant):
            assert_outward(_master_card(master, include_services=False)["photo_url"])
