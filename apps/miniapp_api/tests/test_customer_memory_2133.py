"""«Что Ayla помнит» — Mini App API памяти (DRF-2133, Память-2).

    GET    customer/memory/                 → {green: [...], health: [...], status}
    DELETE customer/memory/<entry_id>/      → своя запись забыта; чужая — 404
    POST   customer/memory/forget-all/      → то же, что «забудь всё» в чате

Читатели — те же, что у чата (``read_green_entries`` под
``memory_key_policy.select_current_facts`` для зелёных, ``RedZoneReader`` для
раздела «Здоровье»), удалитель —
``memory_deleter``. Экран не должен вырастить второго понятия «что помню».

Сторожа из листа: чужая запись → 404; после forget-all GET пуст и статус
``deletion_pending`` виден строкой; red-строка никогда не попадает в ``green``
и в ``health`` приходит только через RedZoneReader (лог доступа пишется);
салонная/мастерская/админская поверхности маршрут не видят.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.identity.models import BotUser, MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-memory-2133"  # noqa: S105 — test fixture  # pragma: allowlist secret

pytestmark = pytest.mark.django_db


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(channel_user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(channel_user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _person_gate_open():
    """§2.4 gate is the chat's concern and has its own tests; open it here."""
    with patch(
        "apps.identity.services.person_context_gate.person_context_access",
        return_value=None,
    ):
        yield


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="memory-2133", name="Memory 2133", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "memory-2133"
    return t


@pytest.fixture
def ayla_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bot_user(tenant: Tenant, ayla_user_id: uuid.UUID) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="21330001",
        display_name="Анна",
        ayla_user_id=ayla_user_id,
    )


@pytest.fixture
def upc(ayla_user_id: uuid.UUID) -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=ayla_user_id)


def _green(upc: UserPersonalContext, **overrides) -> MemoryEntry:
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


def _red(upc: UserPersonalContext, **overrides) -> MemoryEntry:
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="health",
        content={"key": "allergy", "value": "nuts", "display": "аллергия на орехи"},
        consent_at=timezone.now(),
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


def _get(client: Client, bot_user: BotUser):
    return client.get(
        reverse("miniapp_api:customer_memory"), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id)
    )


def _delete(client: Client, bot_user: BotUser, entry_id):
    return client.delete(
        reverse("miniapp_api:customer_memory_entry", kwargs={"entry_id": entry_id}),
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


def _forget_all(client: Client, bot_user: BotUser):
    return client.post(
        reverse("miniapp_api:customer_memory_forget_all"),
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


class TestGet:
    def test_green_fact_with_provenance_and_label(self, client, bot_user, upc):
        entry = _green(upc)

        r = _get(client, bot_user)

        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"green", "health", "status"}
        assert body["status"] == "active"
        assert body["health"] == []
        assert len(body["green"]) == 1
        fact = body["green"][0]
        assert fact["id"] == str(entry.id)
        assert fact["key"] == "diet"
        assert fact["value"] == "vegan"
        # Тот же текст, что в чате: «помню, что ты…» читается одинаково везде.
        assert fact["label"] == "придерживается веганского питания"
        assert fact["provenance"] == "said"
        assert fact["said_at"].startswith(entry.created_at.date().isoformat())

    def test_inferred_fact_is_shown_with_its_mark_not_hidden(self, client, bot_user, upc):
        _green(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            provenance=None,
            # CHECK 1 (0007): inferred rows carry last_inferred_at — Postgres
            # enforces it, SQLite does not; the fixture must satisfy both.
            last_inferred_at=timezone.now(),
            content={"key": "preferred_time_slots", "value": "evening"},
        )

        body = _get(client, bot_user).json()

        assert len(body["green"]) == 1
        assert body["green"][0]["provenance"] == "inferred"

    def test_current_view_not_history(self, client, bot_user, upc):
        """Ключ diet — single: показывается победитель, не оба значения (DRF-1262)."""
        old = _green(upc, content={"key": "diet", "value": "vegan"})
        _green(upc, content={"key": "diet", "value": "keto"})
        # auto_now_add gives both rows the same instant on a fast machine;
        # the policy's tie-break is then the id, not the age. Make the age real.
        MemoryEntry.objects.filter(id=old.id).update(
            created_at=old.created_at - timezone.timedelta(days=1)
        )

        body = _get(client, bot_user).json()

        assert [f["value"] for f in body["green"]] == ["keto"]

    def test_empty_when_nothing_remembered(self, client, bot_user, upc):
        body = _get(client, bot_user).json()
        assert body == {"green": [], "health": [], "status": "active"}

    def test_empty_when_no_memory_identity(self, client, tenant):
        """BotUser без ayla_user_id — памяти нет; ответ пустой, не 500."""
        stranger = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="21330002", display_name="Б"
        )
        r = _get(client, stranger)
        assert r.status_code == 200
        assert r.json() == {"green": [], "health": [], "status": "active"}

    def test_red_row_never_leaks_into_green(self, client, bot_user, upc):
        """Ложный вход: red-строка подсажена — в ``green`` её нет."""
        _green(upc)
        _red(upc)

        body = _get(client, bot_user).json()

        assert [f["key"] for f in body["green"]] == ["diet"]
        assert all(f["key"] != "allergy" for f in body["green"])

    def test_health_comes_only_through_red_zone_reader_with_access_log(
        self, client, bot_user, upc, ayla_user_id
    ):
        red = _red(upc)
        assert RedZoneAccessLog.objects.filter(user_id=ayla_user_id).count() == 0

        body = _get(client, bot_user).json()

        assert [h["id"] for h in body["health"]] == [str(red.id)]
        assert body["health"][0]["kind"] == "health"
        assert body["health"][0]["value"] == "аллергия на орехи"
        log = RedZoneAccessLog.objects.filter(user_id=ayla_user_id, memory_entry_id=red.id)
        assert log.count() == 1
        row = log.get()
        assert row.accessor_role == RedZoneAccessLog.ACCESSOR_DATA_SUBJECT
        assert row.accessor_principal == f"bot_user:{bot_user.pk}"
        assert row.purpose == "miniapp_memory_screen"
        assert row.access_type == RedZoneAccessLog.ACCESS_READ

    def test_health_section_is_read_through_the_reader_not_the_orm(self, client, bot_user, upc):
        """Подмена читателя — раздел пуст: у вью нет второго пути к red."""
        _red(upc)
        with patch(
            "apps.identity.services.red_zone_reader.RedZoneReader.list_live_for_subject",
            return_value=[],
        ) as reader:
            body = _get(client, bot_user).json()
        assert body["health"] == []
        assert reader.call_count == 1


class TestDelete:
    def test_own_green_entry_is_forgotten(self, client, bot_user, upc):
        entry = _green(upc)

        r = _delete(client, bot_user, entry.id)

        assert r.status_code == 200
        assert r.json() == {"id": str(entry.id), "deleted": True}
        entry.refresh_from_db()
        assert entry.soft_deleted_at is not None
        assert entry.status == MemoryEntry.STATUS_DELETED
        assert entry.deletion_reason == "user_request_miniapp"
        assert _get(client, bot_user).json()["green"] == []

    def test_forgetting_the_current_value_does_not_resurrect_the_previous_one(
        self, client, bot_user, upc
    ):
        """Ключ diet — single: экран показывает победителя, старая строка живёт.
        Забыть победителя = забыть ключ целиком, иначе «забыла кето» воскрешает
        «веган» (RESURRECT — тот же риск назван у доменного «забудь» в чате)."""
        old = _green(upc, content={"key": "diet", "value": "vegan"})
        new = _green(upc, content={"key": "diet", "value": "keto"})
        MemoryEntry.objects.filter(id=old.id).update(
            created_at=old.created_at - timezone.timedelta(days=1)
        )
        assert [f["value"] for f in _get(client, bot_user).json()["green"]] == ["keto"]

        assert _delete(client, bot_user, new.id).status_code == 200

        assert _get(client, bot_user).json()["green"] == []
        old.refresh_from_db()
        assert old.soft_deleted_at is not None

    def test_forgetting_one_value_of_a_multi_key_keeps_the_others(self, client, bot_user, upc):
        a = _green(upc, content={"key": "preferred_districts", "value": "центр"})
        b = _green(upc, content={"key": "preferred_districts", "value": "арбеково"})

        assert _delete(client, bot_user, a.id).status_code == 200

        assert [f["id"] for f in _get(client, bot_user).json()["green"]] == [str(b.id)]

    def test_foreign_red_entry_is_404_with_no_access_log(self, client, bot_user, upc):
        other = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        foreign = _red(other)

        r = _delete(client, bot_user, foreign.id)

        assert r.status_code == 404
        foreign.refresh_from_db()
        assert foreign.soft_deleted_at is None
        assert RedZoneAccessLog.objects.count() == 0

    def test_own_health_delete_logs_delete_with_principal(
        self, client, bot_user, upc, ayla_user_id
    ):
        red = _red(upc)
        assert _delete(client, bot_user, red.id).status_code == 200
        row = RedZoneAccessLog.objects.get(memory_entry_id=red.id, user_id=ayla_user_id)
        assert row.access_type == RedZoneAccessLog.ACCESS_DELETE
        assert row.accessor_principal == f"bot_user:{bot_user.pk}"
        assert row.purpose == "miniapp_memory_forget"

    def test_foreign_entry_is_404_and_untouched(self, client, bot_user, upc):
        other = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        foreign = _green(other)

        r = _delete(client, bot_user, foreign.id)

        assert r.status_code == 404
        foreign.refresh_from_db()
        assert foreign.soft_deleted_at is None

    def test_unknown_id_is_404(self, client, bot_user, upc):
        assert _delete(client, bot_user, uuid.uuid4()).status_code == 404

    def test_own_health_entry_is_forgotten_by_the_same_path(self, client, bot_user, upc):
        red = _red(upc)

        r = _delete(client, bot_user, red.id)

        assert r.status_code == 200
        red.refresh_from_db()
        assert red.soft_deleted_at is not None
        assert red.deletion_reason == "user_request_miniapp"
        assert _get(client, bot_user).json()["health"] == []


class TestForgetAll:
    def test_forget_all_then_get_is_empty_with_pending_status(self, client, bot_user, upc):
        _green(upc)

        r = _forget_all(client, bot_user)

        assert r.status_code == 202
        assert r.json()["status"] == "deletion_pending"
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is not None

        body = _get(client, bot_user).json()
        assert body["green"] == []
        assert body["health"] == []
        assert body["status"] == "deletion_pending"

    def test_forget_all_is_idempotent(self, client, bot_user, upc):
        assert _forget_all(client, bot_user).status_code == 202
        assert _forget_all(client, bot_user).status_code == 202

    def test_forget_all_does_what_the_chat_does(self, client, bot_user, upc):
        """Не только флаг: переписка обезличивается и профиль в каталоге стирается —
        те же три обязательства, что у «забудь всё» в чате (OD_MEMORY.md §4)."""
        order: list[str] = []

        def _anon(bot_user_arg):
            # The intent must already be on the UPC when the dialogue goes: the
            # read gate is dark before anything else happens («сейчас»).
            upc.refresh_from_db()
            assert upc.forget_all_requested_at is not None
            order.append("anonymize")

        def _erase(bot_user_arg):
            order.append("erase")
            return "erased"

        with (
            patch("apps.persona.memory_commands._anonymize_dialogue", side_effect=_anon) as anon,
            patch("apps.persona.memory_commands._bridge_erase", side_effect=_erase) as erase,
        ):
            r = _forget_all(client, bot_user)
        assert r.status_code == 202
        assert anon.call_count == 1
        assert erase.call_count == 1
        assert anon.call_args.args[0].pk == bot_user.pk
        assert order == ["anonymize", "erase"]

    def test_forget_all_respects_the_person_context_gate(self, client, bot_user, upc):
        """§2.4 как у GET/DELETE и у чата: закрытая оболочка не командует памятью."""
        _green(upc)
        from apps.identity.services.person_context_gate import Refusal

        with (
            patch(
                "apps.identity.services.person_context_gate.person_context_access",
                return_value=Refusal(reason="unresolved", bot_user_id="x"),
            ),
            patch("apps.persona.memory_commands._anonymize_dialogue") as anon,
            patch("apps.persona.memory_commands._bridge_erase") as erase,
        ):
            r = _forget_all(client, bot_user)
        assert r.status_code == 403
        assert r.json()["error"] == "person_context_closed"
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is None
        assert anon.call_count == 0
        assert erase.call_count == 0


class TestSurface:
    def test_memory_routes_exist_only_on_the_customer_surface(self):
        assert reverse("miniapp_api:customer_memory").startswith("/api/v1/customer/")
        for ns in ("master_api", "admin_api"):
            with pytest.raises(NoReverseMatch):
                reverse(f"{ns}:customer_memory")

    def test_memory_views_carry_the_customer_init_data_guard(self):
        from django.urls import get_resolver
        from django.urls.resolvers import URLPattern, URLResolver

        from apps.miniapp_api.transport_refusal import GUARD_ATTR

        def _walk(patterns, prefix=""):
            for entry in patterns:
                route = prefix + str(entry.pattern)
                if isinstance(entry, URLResolver):
                    yield from _walk(entry.url_patterns, route)
                elif isinstance(entry, URLPattern):
                    yield route, entry.callback

        memory_routes = [
            (route, cb)
            for route, cb in _walk(get_resolver().url_patterns)
            if "customer/memory" in route
        ]
        assert len(memory_routes) == 3
        assert all(getattr(cb, GUARD_ATTR, None) == "customer" for _, cb in memory_routes)
