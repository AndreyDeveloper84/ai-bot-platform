"""Каталожная половина роли ``admin`` — вызов до записи TenantStaff (DRF-2085, PR-2).

OWNER RULING 18.09 (вариант А). Сеть — ``pytest-httpx``: здесь каталог НЕ
заглушка (в отличие от соседних файлов с ``catalog_admin_link_stub``), а
проводная форма ручки beautygo_backend #503 — путь, четвёртый секрет, тело,
разбор ответов.

Узлы, названные в теле PR:

* **секрет не в логах бота** — ``test_s1_…``: значение
  ``AYLA_SALON_ADMIN_LINK_TOKEN`` не встречается ни в одной строке логов
  клиента, сервиса, ядра и MAX-обработчика ни на успехе, ни на отказе;
  читают его ровно два файла (перепись по AST);
* **на 403 каталога — отказ по имени, TenantStaff нет** — ``test_r1_…``:
  credential_refused → ``CatalogAdminLinkRefused`` с причиной, строки нет,
  аудит отказа записан после отката;
* **ввод admin-кода не ходит в каталог, роль в боте выдаётся** —
  ``test_g2_…`` (положительная пара к r1): ruling п.1 читается строго —
  operator-only capability; каталожную половину дозаводит оператор
  повторной «Выдать роль» (тот же ключ идемпотентности).
"""

from __future__ import annotations

import ast
import json
import logging
import uuid
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from apps.audit.models import AuditLog
from apps.events.vocabulary import (
    STAFF_ROLE_GRANTED,
    STAFF_SALON_ADMIN_LINK_REFUSED,
    STAFF_SALON_ADMIN_LINKED,
)
from apps.identity.models import BotUser
from apps.identity.services import salon_admin_link
from apps.identity.services.salon_admin_link import CatalogAdminLinkRefused, idempotency_key_for
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import (
    grant_staff_role,
    issue_staff_invite,
    redeem_staff_invite,
)
from apps.identity.services.staff_roles import change_staff_role, grant_role_by_operator
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

BASE = "https://ayla.test"
LINK_TOKEN = "salon-admin-link-secret-2085"  # noqa: S105
GENERAL_TOKEN = "general-bearer-2085"  # noqa: S105
AYLA_USER = "d0a1c2d3-0000-4000-8000-000000002085"
RELATIONSHIP = "e0a1c2d3-0000-4000-8000-000000002085"
LOGGERS = (
    "apps.catalog.services.http_client",
    "apps.identity.services.salon_admin_link",
    "apps.identity.services.staff_invites",
    "apps.identity.services.staff_roles",
    "apps.channels.max.salon_handler",
    "httpx",
)


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = GENERAL_TOKEN
    settings.AYLA_SALON_ADMIN_LINK_TOKEN = LINK_TOKEN


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="link-2085", name="Салон 2085")


@pytest.fixture
def person(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2085001")


def _actor() -> OnboardingActor:
    return OnboardingActor(
        surface="django_admin",
        audit_label="django_admin:user=7",
        cross_tenant=True,
        capability="platform_operations",
    )


def _url(tenant: Tenant) -> str:
    return f"{BASE}/api/v1/internal/tenants/{tenant.slug}/salon-admins/"


def _ok(tenant: Tenant, *, status_code: int = 201) -> dict:
    return {
        "data": {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "ayla_user_id": AYLA_USER,
            "relationship_id": RELATIONSHIP,
            "role": "admin",
            "status": "created" if status_code == 201 else "replayed",
        }
    }


def _refusal(reason: str) -> dict:
    return {
        "error": {"code": "SALON_ADMIN_LINK_REFUSED", "message": "x", "details": {"reason": reason}}
    }


def _rows(tenant: Tenant, person: BotUser) -> list[str]:
    return sorted(
        TenantStaff.all_tenants.filter(
            tenant=tenant, bot_user=person, deactivated_at__isnull=True
        ).values_list("role", flat=True)
    )


def _audit(action: str, person: BotUser) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=action, target_id=person.pk))


# ─── успех ──────────────────────────────────────────────────────────────────


class TestTheCatalogIsAskedFirst:
    def test_g1_operator_grant_of_admin_posts_the_link_then_writes_the_row(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)

        result = grant_role_by_operator(
            tenant=tenant, bot_user=person, role="admin", actor=_actor()
        )

        assert result.already_had_role is False and _rows(tenant, person) == ["admin"]
        (req,) = httpx_mock.get_requests()
        assert req.headers["Authorization"] == f"Bearer {LINK_TOKEN}"  # четвёртый секрет, не общий
        body = json.loads(req.content)
        assert body["external_user_id"] == "bot:max:2085001"
        assert body["actor"] == "django_admin:user=7"
        assert body["idempotency_key"] == idempotency_key_for(tenant.id, "bot:max:2085001")
        assert len(body["correlation_id"]) == 32
        (linked,) = _audit(STAFF_SALON_ADMIN_LINKED, person)
        assert linked.payload["ayla_user_id"] == AYLA_USER
        assert linked.payload["relationship_id"] == RELATIONSHIP
        assert linked.payload["correlation_id"] == body["correlation_id"]
        assert (
            linked.payload["actor_label"] == "django_admin:user=7"
            and linked.payload["created"] is True
        )
        assert "2085001" not in json.dumps(linked.payload)  # MAX id не в аудите
        assert len(_audit(STAFF_ROLE_GRANTED, person)) == 1

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_g2_an_admin_code_grants_the_bot_role_and_never_calls_the_catalog(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        """Узел из тела PR (положительная пара к r1): дверь кода каталог не зовёт —
        даже когда каталог ответил бы 403, роль в боте выдана и код погашен."""
        httpx_mock.add_response(method="POST", url=_url(tenant), status_code=403, json={})
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert result.role == "admin" and _rows(tenant, person) == ["admin"]
        assert httpx_mock.get_requests() == []  # в сеть ничего — ответ 403 так и не спрошен
        invite.refresh_from_db()
        assert invite.used_at is not None
        # empty-assert-ok: роль выдана (строка выше), каталожной половины нет по построению
        assert _audit(STAFF_SALON_ADMIN_LINKED, person) == []

    def test_g2b_after_the_code_the_operator_adds_the_catalog_half_with_one_click(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        """HOWTO шаг 9: после кода администратора оператор жмёт «Выдать роль» — один клик."""
        httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        result = grant_role_by_operator(
            tenant=tenant, bot_user=person, role="admin", actor=_actor()
        )

        assert result.already_had_role is True  # строка уже была — от кода
        assert len(httpx_mock.get_requests()) == 1 and _rows(tenant, person) == ["admin"]
        (linked,) = _audit(STAFF_SALON_ADMIN_LINKED, person)
        assert linked.payload["actor_label"] == "django_admin:user=7"

    def test_g3_a_repeat_grant_asks_again_with_the_same_key_and_is_replayed(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        """У администраторов до этого листа каталожной половины нет — повтор выдачи её дозаводит."""
        httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
        httpx_mock.add_response(
            method="POST", url=_url(tenant), json=_ok(tenant, status_code=200), status_code=200
        )

        first = grant_role_by_operator(tenant=tenant, bot_user=person, role="admin", actor=_actor())
        second = grant_role_by_operator(
            tenant=tenant, bot_user=person, role="admin", actor=_actor()
        )

        assert (first.already_had_role, second.already_had_role) == (False, True)
        keys = {json.loads(r.content)["idempotency_key"] for r in httpx_mock.get_requests()}
        assert len(keys) == 1  # детерминированный ключ → каталог отвечает replayed
        assert _rows(tenant, person) == ["admin"]
        assert len(_audit(STAFF_SALON_ADMIN_LINKED, person)) == 1  # replayed аудита не плодит

    def test_g4_change_to_admin_goes_through_the_same_door(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="receptionist")

        change = change_staff_role(tenant=tenant, bot_user=person, role="admin", actor=_actor())

        assert change.role == "admin" and _rows(tenant, person) == ["admin"]
        assert len(httpx_mock.get_requests()) == 1

    def test_g5_other_roles_never_touch_the_catalog(self, httpx_mock: HTTPXMock, tenant, person):
        grant_role_by_operator(tenant=tenant, bot_user=person, role="receptionist", actor=_actor())
        assert _rows(tenant, person) == ["receptionist"]
        assert httpx_mock.get_requests() == []


# ─── отказ ──────────────────────────────────────────────────────────────────


class TestRefusalByName:
    def test_r1_catalog_403_is_a_named_refusal_and_no_row_is_written(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        """Узел из тела PR: на 403 каталога — отказ по имени, TenantStaff нет."""
        httpx_mock.add_response(
            method="POST", url=_url(tenant), status_code=403, json={"detail": "no"}
        )

        with pytest.raises(CatalogAdminLinkRefused) as exc:
            grant_role_by_operator(tenant=tenant, bot_user=person, role="admin", actor=_actor())

        assert (
            exc.value.reason == "credential_refused"
            and exc.value.slug == "catalog_admin_link_refused"
        )
        assert (
            "AYLA_SALON_ADMIN_LINK_TOKEN" in exc.value.hint
            and "Роль в боте не выдана" in exc.value.operator_text()
        )
        # empty-assert-ok: отказ пойман выше (pytest.raises), аудит отказа — присутствие ниже
        assert _rows(tenant, person) == []
        (refused,) = _audit(STAFF_SALON_ADMIN_LINK_REFUSED, person)  # аудит отказа пережил откат
        assert refused.payload["reason"] == "credential_refused"
        assert refused.payload["correlation_id"] == exc.value.correlation_id
        assert refused.payload["actor_label"] == "django_admin:user=7"
        # empty-assert-ok: пара — аудит отказа найден выше; аудит выдачи пишется после ядра и не наступил
        assert _audit(STAFF_ROLE_GRANTED, person) == []
        # empty-assert-ok: пара — строка аудита отказа найдена выше
        assert _audit(STAFF_SALON_ADMIN_LINKED, person) == []

    @pytest.mark.parametrize(
        ("status_code", "body", "reason"),
        [
            (409, _refusal("identity_already_bound"), "identity_already_bound"),
            (409, _refusal("identity_not_proxy"), "identity_not_proxy"),
            (404, _refusal("tenant_not_found"), "tenant_not_found"),
            (500, _refusal("readback_failed"), "readback_failed"),
            (429, {}, "rate_limited"),
            (400, {"error": {"code": "VALIDATION_ERROR"}}, "client_error"),
            (502, {}, "transport_error"),
        ],
    )
    def test_r2_each_catalog_outcome_keeps_its_name_and_a_hint(
        self, httpx_mock: HTTPXMock, tenant, person, status_code, body, reason
    ):
        httpx_mock.add_response(method="POST", url=_url(tenant), status_code=status_code, json=body)

        with pytest.raises(CatalogAdminLinkRefused) as exc:
            grant_role_by_operator(tenant=tenant, bot_user=person, role="admin", actor=_actor())

        assert exc.value.reason == reason
        assert exc.value.hint in salon_admin_link.HINTS.values()
        # empty-assert-ok: отказ пойман выше (pytest.raises), аудит отказа — строкой ниже
        assert _rows(tenant, person) == []
        assert len(_audit(STAFF_SALON_ADMIN_LINK_REFUSED, person)) == 1

    def test_r3_empty_token_on_our_side_refuses_by_name_and_sends_nothing(
        self, httpx_mock: HTTPXMock, settings, tenant, person
    ):
        settings.AYLA_SALON_ADMIN_LINK_TOKEN = ""

        with pytest.raises(CatalogAdminLinkRefused) as exc:
            grant_role_by_operator(tenant=tenant, bot_user=person, role="admin", actor=_actor())

        assert exc.value.reason == "token_missing"
        assert httpx_mock.get_requests() == [] and _rows(tenant, person) == []

    def test_r4_change_to_admin_refused_rolls_the_deactivation_back(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        httpx_mock.add_response(method="POST", url=_url(tenant), status_code=403, json={})
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="receptionist")

        with pytest.raises(CatalogAdminLinkRefused):
            change_staff_role(tenant=tenant, bot_user=person, role="admin", actor=_actor())

        assert _rows(tenant, person) == ["receptionist"]  # прежняя роль не снята

    def test_r5_the_core_refuses_before_any_row_even_inside_a_bare_transaction(
        self, httpx_mock: HTTPXMock, tenant, person
    ):
        from django.db import transaction

        httpx_mock.add_response(method="POST", url=_url(tenant), status_code=403, json={})
        with pytest.raises(CatalogAdminLinkRefused), transaction.atomic():
            grant_staff_role(tenant_id=tenant.id, bot_user=person, role="admin", actor_label="x:1")
        # empty-assert-ok: отказ пойман выше (pytest.raises)
        assert _rows(tenant, person) == []


# ─── секрет ─────────────────────────────────────────────────────────────────


class TestTheSecretStaysOut:
    def test_s1_the_secret_is_in_no_log_line_on_success_or_refusal(
        self, httpx_mock: HTTPXMock, tenant, person, caplog
    ):
        """Узел из тела PR: секрет не в логах бота."""
        httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
        httpx_mock.add_response(method="POST", url=_url(tenant), status_code=403, json={})
        handlers = [logging.getLogger(name) for name in LOGGERS]
        for lg in handlers:
            lg.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.DEBUG):
                grant_role_by_operator(tenant=tenant, bot_user=person, role="admin", actor=_actor())
                other = BotUser.all_tenants.create(
                    tenant=tenant, channel="max", channel_user_id="2085002"
                )
                with pytest.raises(CatalogAdminLinkRefused):
                    grant_role_by_operator(
                        tenant=tenant, bot_user=other, role="admin", actor=_actor()
                    )
        finally:
            for lg in handlers:
                lg.removeHandler(caplog.handler)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert (
            "identity.salon_admin_link.created" in text
            and "identity.salon_admin_link.refused" in text
        )
        assert LINK_TOKEN not in text and GENERAL_TOKEN not in text
        assert "bot:max:2085001" not in text  # и MAX-идентификатор — тоже нет

    def test_s2_the_secret_is_read_by_exactly_two_modules(self):
        """Перепись по AST: имя секрета читают settings и HTTP-клиент, больше никто."""
        root = Path(__file__).resolve().parents[3]
        readers: set[str] = set()
        for path in list((root / "apps").rglob("*.py")) + list((root / "config").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "AYLA_SALON_ADMIN_LINK_TOKEN":
                    readers.add(rel)
        assert readers == {"config/settings/base.py", "apps/catalog/services/http_client.py"}, (
            readers
        )

    def test_s3_the_refusal_text_names_what_to_do_not_the_secret_value(self, tenant, person):
        exc = CatalogAdminLinkRefused(
            "credential_refused",
            correlation_id=uuid.uuid4().hex,
            tenant=tenant,
            bot_user=person,
            actor_label="django_admin:user=7",
        )
        text = exc.operator_text()
        assert tenant.slug in text and "credential_refused" in text and exc.correlation_id in text
        assert LINK_TOKEN not in text


# ─── стражи ─────────────────────────────────────────────────────────────────


class TestTheGuards:
    def test_c1_every_caller_of_the_core_names_its_actor_or_closes_the_door(self):
        """Все вызовы grant_staff_role передают actor_label — иначе каталожная половина
        безымянна; единственный вызов без него — дверь кода, и она закрыта явно
        (``link_catalog=False``): пропущенный аргумент не может стать «безымянной» связью."""
        root = Path(__file__).resolve().parents[3]
        calls: list[str] = []
        for path in (root / "apps").rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name != "grant_staff_role":
                    continue
                keywords = {kw.arg: kw.value for kw in node.keywords}
                door_closed = (
                    isinstance(keywords.get("link_catalog"), ast.Constant)
                    and keywords["link_catalog"].value is False
                )
                assert "actor_label" in keywords or door_closed, (
                    f"{rel}:{node.lineno} вызывает ядро без actor_label и без link_catalog=False"
                )
                calls.append(f"{rel}:{node.lineno}:{'closed' if door_closed else 'named'}")
        assert sorted(c.rsplit(":", 1)[1] for c in calls) == ["closed", "named", "named"], calls

    def test_c2_the_link_is_asked_before_the_row_is_written(self):
        """Порядок в ядре — по AST: вызов ensure_catalog_salon_admin стоит выше TenantStaff.create."""
        root = Path(__file__).resolve().parents[3]
        source = (root / "apps/identity/services/staff_invites.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "grant_staff_role"
        )
        link_line = create_line = None
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "ensure_catalog_salon_admin"
            ):
                link_line = node.lineno
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create":
                value = getattr(node.func, "value", None)
                if getattr(value, "attr", "") == "all_tenants":
                    create_line = node.lineno
        assert link_line is not None and create_line is not None
        assert link_line < create_line
