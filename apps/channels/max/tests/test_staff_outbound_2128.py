"""Уведомления персоналу уходят от салонного бота, а не от клиентского (DRF-2128).

Замер 19.09 (бот ``dev 7a21f884``): ``outbound.send_message`` берёт токен из
``bot_scope``, иначе — ``settings.MAX_BOT_TOKEN`` (legacy). ``bot_scope(salon)``
ставили только ``booking/master_notify.py`` и ``internal_chat/notify.py``.
Девять мест, пишущих персоналу, шли без scope — а на стенде legacy-токен
**равен клиентскому** (fp ``6e0b4184``; салонный — ``5d7307f0``). Персонал
получал рабочие уведомления от клиентского бота — либо не получал вовсе, если
клиентскому боту не писал.

Один вход — :func:`apps.channels.max.staff_outbound.send_to_staff`: сам
входит в ``bot_scope(resolve_by_stream("max_salon"))``, адресаты — активные
``TenantStaff`` владелец/админ (люди, ``channel_user_id``) плюс
``manager_address(tenant)``, либо конкретный мастер по ``user_id``.

* p1 — **узел**: под ``MAX_BOTS=client,salon`` (реестр из двух записей,
  ``MAX_BOT_TOKEN`` = клиентский) токен исходящего персоналу на каждом
  из путей — салонный, не клиентский. Красный до правки: ``token-client``;
* p2 — **перепись прямых вызовов по роли**: каждый ``send_message(`` в
  ``apps/`` вне ``channels/max/{outbound,staff_outbound}.py`` записан с ролью
  ``client`` / ``operator`` / ``transport`` / ``in_scope``; вызов персоналу (функция читает
  ``manager_address`` или живёт в модуле персонала) — только через
  ``send_to_staff``; незаписанный → красный, устаревший → красный;
* p3 — **ложный вход**: подсадка прямого ``send_message`` персоналу в
  синтетический модуль → сканер красный; тот же модуль через
  ``send_to_staff`` → зелёный;
* p4 — нет записи ``max_salon`` в реестре → legacy как сейчас, но с
  ``ERROR`` в логе (не тихо);
* p5 — сторож DRF-2129 на общем входе: текст, дошедший до ``send_to_staff``
  на каждом пути p1, без телефона клиента (переезд с переписи строителей);
* p6 — адресаты менеджерского уведомления: активные ``TenantStaff``
  владелец/админ по ``channel_user_id`` + ``manager_address``; уволенный,
  ресепшн и дубль по значению — не адресаты.

Два места того же класса — ``llm/cost_tracker.py`` (бюджет токенов) и
``orchestrator/pipeline.py`` (retry exhausted) — забирает DRF-2130 в
операторский канал (``alerting.page``), а не в персонал; они названы в
``HANDED_TO_DRF_2130`` и не переводятся здесь. Когда DRF-2130 снимет
отправку менеджеру, запись станет устаревшей и p2 попросит её удалить.
"""

from __future__ import annotations

import ast
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from django.utils import timezone

from apps.bookings.tests.test_staff_texts_no_phone_2129 import violates_phone_rule
from apps.channels.bot_registry import BotEntry

CLIENT_TOKEN = "token-client"  # pragma: allowlist secret
SALON_TOKEN = "token-salon"  # pragma: allowlist secret
PHONE = "79991234567"

APPS_ROOT = Path(__file__).resolve().parents[3]
assert APPS_ROOT.name == "apps", APPS_ROOT


def _registry_two_bots() -> tuple[BotEntry, ...]:
    """``MAX_BOTS=client,salon`` — как на стенде (замер 19.09)."""
    return (
        BotEntry(
            slug="client",
            webhook_secret="wh-client",  # pragma: allowlist secret
            api_token=CLIENT_TOKEN,
            stream="max_global",
        ),
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token=SALON_TOKEN,
            stream="max_salon",
            miniapp_url="https://app.example",
        ),
    )


@pytest.fixture
def two_bots(settings):
    settings.MAX_BOT_REGISTRY = _registry_two_bots()
    # Legacy-токен на стенде РАВЕН клиентскому — в этом весь дефект.
    settings.MAX_BOT_TOKEN = CLIENT_TOKEN
    return settings


@pytest.fixture
def capture(monkeypatch):
    """Что outbound отправил бы: токен на момент вызова, адрес, текст.

    Подменяется ``apps.channels.max.outbound.send_message`` — единственный
    провод к MAX; все пути персоналу обязаны в него упираться.
    """
    seen: list[dict] = []

    def _fake_send(*, text, chat_id=None, user_id=None, attachments=None, **_):
        from apps.channels.max.outbound import _token

        seen.append({"token": _token(), "chat_id": chat_id, "user_id": user_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _fake_send)
    # До правки эскалация и каскад мастера держат ``send_message`` именем
    # модуля — иначе красное там было бы «0 отправок» (или отправка в
    # список прошлого теста), а не «клиентский токен».
    for legacy_name in (
        "apps.bookings.escalation.send_message",
        "apps.admin_api.services.master_deactivation.send_message",
    ):
        monkeypatch.setattr(legacy_name, _fake_send, raising=False)
    return seen


def _tenant_double(**over) -> SimpleNamespace:
    base = dict(
        pk=None,
        id=uuid.uuid4(),
        slug="formula-tela",
        name="Формула тела",
        manager_user_id="mgr-user-1",
        manager_chat_id="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _master_double() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name="Лера")


def _pending_row(tenant) -> SimpleNamespace:
    return SimpleNamespace(
        pk=uuid.uuid4(),
        tenant=tenant,
        payload={
            "record_id": 555,
            "new_datetime": "2026-06-01T14:00:00",
            "service_name": "Массаж",
            "master_name": "Ольга",
            "client_name": "Anna",
            "client_phone": PHONE,
        },
    )


# ── Пути персоналу — каждый вызывается как есть, без обхода ──────────


def _path_schedule_request_view() -> None:
    from apps.master_api.views import _maybe_send_manager_dm

    _maybe_send_manager_dm(
        tenant=_tenant_double(), master=_master_double(), request_id=uuid.uuid4()
    )


def _path_schedule_request_service() -> None:
    from apps.master_api.services.schedule import notify_manager_of_availability_request

    notify_manager_of_availability_request(
        tenant=_tenant_double(), master=_master_double(), request_id=uuid.uuid4()
    )


def _path_conversation_promoted() -> None:
    from apps.master_api.services.conversation_detail import _maybe_send_manager_dm

    _maybe_send_manager_dm(
        tenant=_tenant_double(),
        master=_master_double(),
        client_label="Anna",
        reason_class="complaint",
    )


def _path_reschedule_partial() -> None:
    from apps.bookings.callbacks import _notify_manager_partial_reschedule

    _notify_manager_partial_reschedule(_pending_row(_tenant_double()))


def _path_reschedule_success() -> None:
    from apps.bookings.callbacks import _notify_manager_reschedule_success

    _notify_manager_reschedule_success(
        _pending_row(_tenant_double()),
        SimpleNamespace(confirmation=SimpleNamespace(record_id="777")),
    )


def _path_master_decision_dm() -> None:
    from apps.admin_api.tasks import dispatch_master_decision_dm

    dispatch_master_decision_dm.apply(
        kwargs={
            "user_id": "9001",
            "decision": "approved",
            "date_range_human": "12–15 июня",
            "request_id": str(uuid.uuid4()),
            "master_id": str(uuid.uuid4()),
            "rejection_reason": "",
        }
    )


def _path_escalation() -> None:
    """Эскалация напоминания — реальные строки, как в ``test_escalation``."""
    from apps.booking.models import BookingReminder
    from apps.bookings.escalation import escalate_stale_reminders
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="esc-2128", name="Эскалация", manager_user_id="mgr-user-1")
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2128",
        chat_id="chat-client-2128",
        phone=PHONE,
        client_name="Anna",
    )
    now = timezone.now()
    visit_at = now + timedelta(hours=6)
    BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id="yc-2128",
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT_NO_REPLY,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Lera",
        service_name="Массаж",
    )
    escalate_stale_reminders()


def _path_master_reactivated() -> None:
    from datetime import datetime, timezone as dt_timezone

    from django.test import TestCase

    from apps.admin_api.services.master_deactivation import reactivate_master
    from apps.catalog.models import CatalogMaster
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="react-2128", name="Реактивация")
    master_bu = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="8001",
        display_name="Master DM",
        chat_id="chat-8001",
        phone="+79990000001",
    )
    actor = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="5001",
        display_name="Owner",
        chat_id="chat-5001",
        phone="+79990000002",
    )
    row_id = uuid.uuid4()
    master = CatalogMaster.all_tenants.create(
        id=row_id,
        catalog_specialist_id=row_id,
        tenant=tenant,
        external_id=60,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Reactivate Me",
        is_active=False,
        archived_at=datetime.now(tz=dt_timezone.utc),
        linked_bot_user=master_bu,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        reactivate_master(master, notify_master=True, actor=actor, actor_role="owner")


STAFF_PATHS: dict[str, Callable[[], None]] = {
    "schedule_request_view": _path_schedule_request_view,
    "schedule_request_service": _path_schedule_request_service,
    "conversation_promoted": _path_conversation_promoted,
    "reschedule_partial": _path_reschedule_partial,
    "reschedule_success": _path_reschedule_success,
    "master_decision_dm": _path_master_decision_dm,
    "escalation": _path_escalation,
    "master_reactivated": _path_master_reactivated,
}


@pytest.mark.django_db
class TestP1StaffOutboundGoesAsTheSalonBot:
    @pytest.mark.parametrize("path", sorted(STAFF_PATHS))
    def test_token_is_the_salon_one(self, path: str, two_bots, capture) -> None:
        STAFF_PATHS[path]()
        assert len(capture) == 1, capture  # положительно: ровно одна отправка
        assert capture[0]["token"] == SALON_TOKEN, (path, capture[0]["token"])

    def test_the_fixture_itself_sees_the_client_token_outside_any_scope(self, two_bots) -> None:
        """Стража стражи: без scope ``_token()`` действительно клиентский."""
        from apps.channels.max.outbound import _token

        assert _token() == CLIENT_TOKEN


# ── p2 — перепись прямых вызовов send_message по роли ────────────────

#: Каждый прямой ``send_message(`` в ``apps/`` (вне тестов и вне
#: ``channels/max/{outbound,staff_outbound}.py``) — с ролью адресата.
#: Персонал здесь недопустим по построению: он идёт через ``send_to_staff``.
DIRECT_SEND_SITES: dict[str, str] = {
    # клиент — человек, которому бот пишет как консьерж
    "apps/bookings/followups.py": "client",
    "apps/bookings/tasks.py": "client",
    "apps/nutrition_proactive/tasks.py": "client",
    "apps/orchestrator/health_return.py": "client",
    "apps/skills/payment_failed/skill.py": "client",
    "apps/handoff/silence.py": "client",
    "apps/admin_api/services/master_deactivation.py:_dispatch_customer_notifications": "client",
    # транспорт — фан-аут, отправитель задаётся вызывающим (bot_scope)
    "apps/handoff/notify.py": "transport",
    "apps/channels/apps.py": "transport",
    "apps/channels/telegram/handler.py": "transport",
    # ответ внутри хода — bot_scope уже поставлен границей вебхука
    "apps/channels/max/handler.py": "in_scope",
    "apps/channels/max/salon_handler.py": "in_scope",
}

#: Инженерные алерты менеджеру — уходят в операторский канал по DRF-2130,
#: не в персонал. Устаревшая запись (отправки больше нет) → красный: снять.
HANDED_TO_DRF_2130: frozenset[str] = frozenset(
    {
        "apps/llm/cost_tracker.py",
        "apps/orchestrator/pipeline.py",
    }
)

#: Модули, чьи отправки — персоналу по определению модуля.
STAFF_MODULES: tuple[str, ...] = (
    "apps/master_api/",
    "apps/admin_api/tasks.py",
)

#: Функции персоналу в модулях, где рядом живут и клиентские отправки
#: (каскад деактивации мастера пишет и клиентам, и мастерам).
STAFF_FUNCTIONS: frozenset[str] = frozenset(
    {
        "apps/admin_api/services/master_deactivation.py:_dispatch_master_notifications",
        "apps/admin_api/services/master_deactivation.py:_dispatch",
    }
)

_STAFF_ADDRESS_NAMES = frozenset({"manager_address", "manager_recipients", "operator_addresses"})


def _is_send_message_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "send_message"
    if isinstance(func, ast.Attribute):
        # Только ``outbound.send_message``: у чата и консьержа свои
        # одноимённые методы, к проводу MAX они отношения не имеют.
        return (
            func.attr == "send_message"
            and isinstance(func.value, ast.Name)
            and func.value.id == "outbound"
        )
    return False


def _own_nodes(fn: ast.AST):
    """Узлы тела функции без вложенных функций: вызов принадлежит ближайшей."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _reads_staff_address(fn: ast.AST) -> bool:
    """Функция адресуется персоналу: читает адрес менеджера/операторов
    или разворачивает ``MaxAddress.send_kwargs()`` — форму адреса «из
    настройки салона», которой клиентов не адресуют (DRF-1559)."""
    for sub in _own_nodes(fn):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in _STAFF_ADDRESS_NAMES or name == "send_kwargs":
                return True
    return False


def scan_direct_sends(root: Path, rel_to: Path) -> dict[str, list[tuple[str, bool]]]:
    """``{rel_path: [(function_name, addresses_staff), …]}`` по всем ``.py``.

    ``addresses_staff`` истинно, когда функция с прямым ``send_message(``
    читает адрес персонала (``manager_address`` и родня, ``send_kwargs()``)
    или лежит в модуле персонала. Метод ``.send_message`` у объекта (телеграм-клиент, вьюха
    ``conversation_send_message``) не считается: считается только вызов
    свободной функции ``send_message`` или ``outbound.send_message``.
    """
    found: dict[str, list[tuple[str, bool]]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(rel_to).as_posix()
        if (
            "/tests/" in f"/{rel}"
            or rel.endswith("/outbound.py")
            or rel.endswith("/staff_outbound.py")
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            direct = [n for n in _own_nodes(fn) if _is_send_message_call(n)]
            if not direct:
                continue
            staff_by_place = (
                any(rel.startswith(prefix) for prefix in STAFF_MODULES)
                or f"{rel}:{fn.name}" in STAFF_FUNCTIONS
            )
            found.setdefault(rel, []).append((fn.name, staff_by_place or _reads_staff_address(fn)))
    return found


def _site_key(rel: str, fn: str, registry: dict[str, str]) -> str | None:
    if f"{rel}:{fn}" in registry:
        return f"{rel}:{fn}"
    if rel in registry:
        return rel
    return None


class TestP2DirectSendCensusByRole:
    def _scan(self) -> dict[str, list[tuple[str, bool]]]:
        return scan_direct_sends(APPS_ROOT, APPS_ROOT.parent)

    def test_every_direct_send_is_registered_with_a_role(self) -> None:
        found = self._scan()
        assert len(found) >= 5  # положительно: прямые вызовы найдены
        unregistered = sorted(
            f"{rel}:{fn}"
            for rel, fns in found.items()
            for fn, _ in fns
            if _site_key(rel, fn, DIRECT_SEND_SITES) is None and rel not in HANDED_TO_DRF_2130
        )
        assert unregistered == [], f"прямой send_message без роли в переписи: {unregistered}"

    def test_no_registered_site_addresses_staff(self) -> None:
        found = self._scan()
        assert len(found) >= 5  # положительно: прямые вызовы найдены
        staff_direct = sorted(
            f"{rel}:{fn}"
            for rel, fns in found.items()
            for fn, addresses_staff in fns
            if addresses_staff and rel not in HANDED_TO_DRF_2130
        )
        why = f"персоналу — только через send_to_staff (DRF-2128), прямые вызовы: {staff_direct}"
        assert staff_direct == [], why  # empty-assert-ok: положительно len(found) выше

    def test_registry_and_handoff_are_not_stale(self) -> None:
        found = self._scan()
        assert len(found) >= 5  # положительно: прямые вызовы найдены
        present = {f"{rel}:{fn}" for rel, fns in found.items() for fn, _ in fns} | set(found)
        stale = sorted(key for key in DIRECT_SEND_SITES if key not in present)
        assert stale == [], f"перепись устарела: {stale}"  # empty-assert-ok: len(found) выше
        stale_handoff = sorted(
            rel
            for rel in HANDED_TO_DRF_2130
            if not any(addresses_staff for _, addresses_staff in found.get(rel, []))
        )
        why = f"DRF-2130 снял отправку менеджеру — удалить из HANDED_TO_DRF_2130: {stale_handoff}"
        assert stale_handoff == [], why  # empty-assert-ok: положительно len(found) выше

    def test_roles_are_from_the_closed_set(self) -> None:
        assert set(DIRECT_SEND_SITES.values()) <= {"client", "operator", "transport", "in_scope"}


class TestP3APlantedDirectSendToStaffIsCaught:
    PLANTED = (
        "from apps.channels.max.addressing import manager_address\n"
        "from apps.channels.max.outbound import send_message\n"
        "def _notify_owner(tenant, text):\n"
        "    manager = manager_address(tenant)\n"
        "    send_message(**manager.send_kwargs(), text=text)\n"
    )
    CLEAN = (
        "from apps.channels.max.staff_outbound import MANAGER, send_to_staff\n"
        "def _notify_owner(tenant, text):\n"
        "    send_to_staff(tenant, MANAGER, text)\n"
    )

    def test_planted(self, tmp_path: Path) -> None:
        pkg = tmp_path / "apps" / "planted"
        pkg.mkdir(parents=True)
        (pkg / "owner_dm.py").write_text(self.PLANTED, encoding="utf-8")
        found = scan_direct_sends(tmp_path / "apps", tmp_path)
        assert found == {"apps/planted/owner_dm.py": [("_notify_owner", True)]}

    def test_clean(self, tmp_path: Path) -> None:
        pkg = tmp_path / "apps" / "planted"
        pkg.mkdir(parents=True)
        (pkg / "owner_dm.py").write_text(self.CLEAN, encoding="utf-8")
        assert "send_to_staff" in self.CLEAN  # положительно: отправка в файле есть
        found = scan_direct_sends(tmp_path / "apps", tmp_path)
        assert found == {}  # empty-assert-ok: положительная пара строкой выше

    def test_a_client_send_in_a_staff_module_is_still_flagged(self, tmp_path: Path) -> None:
        """Модуль персонала — персонал по определению, даже без manager_address."""
        pkg = tmp_path / "apps" / "master_api"
        pkg.mkdir(parents=True)
        (pkg / "x.py").write_text(
            "from apps.channels.max.outbound import send_message\n"
            "def f(uid, text):\n    send_message(user_id=uid, text=text)\n",
            encoding="utf-8",
        )
        assert scan_direct_sends(tmp_path / "apps", tmp_path) == {
            "apps/master_api/x.py": [("f", True)]
        }


# ── p4 — без салонного бота: legacy + ERROR, не тихо ─────────────────


@pytest.mark.django_db
class TestP4NoSalonBotIsLoudLegacy:
    def test_legacy_token_and_error_log(self, settings, capture, caplog) -> None:
        from apps.channels.max.addressing import MaxAddress
        from apps.channels.max.staff_outbound import send_to_staff

        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_BOT_TOKEN = CLIENT_TOKEN
        with caplog.at_level(logging.ERROR, logger="apps.channels.max.staff_outbound"):
            result = send_to_staff(_tenant_double(), MaxAddress(user_id="9001"), "Привет")
        assert result.sent == 1
        assert capture[0]["token"] == CLIENT_TOKEN
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "channels.max.staff_outbound.no_salon_bot" in errors[0].getMessage()

    def test_with_a_salon_bot_there_is_no_error(self, two_bots, capture, caplog) -> None:
        from apps.channels.max.addressing import MaxAddress
        from apps.channels.max.staff_outbound import send_to_staff

        with caplog.at_level(logging.ERROR, logger="apps.channels.max.staff_outbound"):
            send_to_staff(_tenant_double(), MaxAddress(user_id="9001"), "Привет")
        assert capture[0]["token"] == SALON_TOKEN  # положительно: ушло салонным
        assert [r for r in caplog.records if r.levelno == logging.ERROR] == []


# ── p5 — сторож DRF-2129 на общем входе ──────────────────────────────


@pytest.mark.django_db
class TestP5NoClientPhoneReachesTheStaffEntry:
    @pytest.mark.parametrize("path", sorted(STAFF_PATHS))
    def test_text_at_the_entry(self, path: str, two_bots, capture) -> None:
        STAFF_PATHS[path]()
        assert len(capture) == 1 and capture[0]["text"]  # положительно: текст дошёл
        assert not violates_phone_rule(capture[0]["text"]), (path, capture[0]["text"])


# ── p6 — адресаты менеджерского уведомления ──────────────────────────


@pytest.mark.django_db
class TestP6ManagerRecipients:
    @staticmethod
    def _staff(tenant, *, uid: str, role: str, deactivated=None):
        from apps.identity.models import BotUser
        from apps.tenancy.models import TenantStaff

        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=uid,
            chat_id=f"chat-{uid}",
            display_name=f"Staff {uid}",
        )
        return TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bu, role=role, deactivated_at=deactivated
        )

    def test_owner_admin_and_manager_address_deduplicated(self, two_bots, capture) -> None:
        from apps.channels.max.staff_outbound import MANAGER, manager_recipients, send_to_staff
        from apps.tenancy.models import Tenant, TenantStaff

        tenant = Tenant.objects.create(slug="rcp-2128", name="Адресаты", manager_user_id="owner-1")
        self._staff(tenant, uid="owner-1", role=TenantStaff.Role.OWNER)
        self._staff(tenant, uid="admin-1", role=TenantStaff.Role.ADMIN)
        self._staff(tenant, uid="recept-1", role=TenantStaff.Role.RECEPTIONIST)
        self._staff(
            tenant, uid="admin-fired", role=TenantStaff.Role.ADMIN, deactivated=timezone.now()
        )

        recipients = manager_recipients(tenant)
        assert sorted(a.user_id for a in recipients) == ["admin-1", "owner-1"]

        result = send_to_staff(tenant, MANAGER, "Заявка")
        assert result.sent == 2
        assert sorted(c["user_id"] for c in capture) == ["admin-1", "owner-1"]
        assert {c["token"] for c in capture} == {SALON_TOKEN}

    def test_manager_address_alone_when_there_is_no_staff(self, two_bots, capture) -> None:
        from apps.channels.max.staff_outbound import MANAGER, send_to_staff
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="rcp-solo", name="Без персонала", manager_user_id="m-1")
        result = send_to_staff(tenant, MANAGER, "Заявка")
        assert result.sent == 1 and capture[0]["user_id"] == "m-1"

    def test_nobody_is_a_named_outcome(self, two_bots, capture, caplog) -> None:
        from apps.channels.max.staff_outbound import MANAGER, send_to_staff
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="rcp-none", name="Никого")
        with caplog.at_level(logging.WARNING, logger="apps.channels.max.staff_outbound"):
            result = send_to_staff(tenant, MANAGER, "Заявка")
        assert result.recipients == 0 and result.sent == 0
        assert capture == []  # empty-assert-ok: recipients == 0 строкой выше
        assert any("no_recipients" in r.getMessage() for r in caplog.records)
