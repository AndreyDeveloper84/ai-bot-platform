"""Уведомления-решения в чате салонного бота: один формат, пять типов (DRF-2118, §50 п.8).

Принцип владельца: «Чат сообщает и просит решения. Mini App — для
полноценного управления». Замер на нетронутом ``dev 5c3047c3`` (20.09):

* тип 1 «клиент ждёт ответа» — ``handoff/services.py::create_admin_task`` →
  ``notify_admin_task_created`` → ГЛОБАЛЬНЫЙ список операторов
  (``HANDOFF_NOTIFY_MAX_*``), legacy-токен; персонал салона не узнаёт, кнопок нет;
* тип 2 «мастер просит изменить график» —
  ``master_api/services/schedule.py::notify_manager_of_availability_request``
  (через ``send_to_staff`` с DRF-2128): текст «{мастер} просит изменить
  расписание. [Открыть запрос](url)» — без «Было/Станет/затронуто», без кнопок;
  «Одобрить» в чате есть (``cb:staff:req_ok:``), «Отклонить» — только Mini App;
  impact (§142) живёт только во вьюхе ``views_schedule_impact.py`` и не переиспользуется;
* тип 3 «синхронизация с ошибкой» — ``catalog/tasks.py::alert_stale_catalog_sync``
  → ``alerting.page`` (Telegram операторов + Sentry), персонал салона — никогда;
* тип 4 «мастер перестал продаваться» — уведомления нет нигде; переходы —
  ``upserter.py`` (``is_active`` из Ayla), деактивация (свой DM), отзыв доступа;
  слово причины — ``master_state.sale_block``;
* тип 5 «запись требует вмешательства» — ``booking.cancelled`` /
  ``booking.no_show`` в ``eventbus/consumers/booking.py`` персоналу не сообщают;
  «перенос не завершён» и эскалация напоминания — уже менеджеру, но своим текстом.

Здесь — сторожа на общий рендерер :mod:`apps.channels.max.salon_notify`:

* p1 — каждый тип строится и рендерится: заголовок, факты, кнопки с
  колбэком ``cb:salon:n:<kind>:<id>:<action>`` (разбирается обратно);
  телефон клиента из входа в текст не попадает (DRF-1039, сторож DRF-2129);
* p2 — **узел**: событие → ровно одно сообщение (от салонного бота, с
  клавиатурой) → повтор события → нет дубля;
* p3 — чужой салон не получает: адресаты — только персонал своего тенанта;
* p4 — тип 2 как есть на проводе: «Было / Станет / Затронуто N» + три кнопки;
  impact недоступен → названо словами, не «0»;
* p5 — кнопки типа 2: «Одобрить» → решение записано тем же сервисом, что у
  Mini App; «Отклонить» → причина — константа по коду; повтор на решённой →
  «уже решено», второго решения нет; «Подробнее» → impact;
* p6 — источники подключены: заявка (тип 2), handoff (тип 1), stale sync
  (тип 3), переход ``sale_block`` в синке (тип 4), отмена/неявка (тип 5).
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.bookings.tests.test_staff_texts_no_phone_2129 import violates_phone_rule
from apps.channels.bot_registry import BotEntry

CLIENT_TOKEN = "token-client"  # pragma: allowlist secret
SALON_TOKEN = "token-salon"  # pragma: allowlist secret
PHONE = "79991234567"
APPS_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def two_bots(settings):
    settings.MAX_BOT_REGISTRY = (
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
    settings.MAX_BOT_TOKEN = CLIENT_TOKEN
    return settings


@pytest.fixture
def capture(monkeypatch):
    seen: list[dict] = []

    def _fake_send(*, text, chat_id=None, user_id=None, attachments=None, **_):
        from apps.channels.max.outbound import _token

        seen.append(
            {
                "token": _token(),
                "chat_id": chat_id,
                "user_id": user_id,
                "text": text,
                "attachments": attachments or [],
            }
        )
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _fake_send)
    return seen


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _buttons_of(sent: dict) -> list[dict]:
    out: list[dict] = []
    for att in sent["attachments"]:
        for row in (att.get("payload") or {}).get("buttons", []):
            out.extend(row)
    return out


@pytest.fixture
def salon(db):
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant, TenantStaff

    tenant = Tenant.objects.create(slug="salon-2118", name="Салон", timezone="Europe/Moscow")
    owner = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="owner-2118", chat_id="c-owner-2118"
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER)
    return SimpleNamespace(tenant=tenant, owner=owner)


@pytest.fixture
def other_salon(db):
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant, TenantStaff

    tenant = Tenant.objects.create(slug="other-2118", name="Чужой", timezone="Europe/Moscow")
    owner = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="owner-other", chat_id="c-owner-other"
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER)
    return SimpleNamespace(tenant=tenant, owner=owner)


def _master(tenant, *, name="Анна"):
    from apps.catalog.models import CatalogMaster

    row_id = uuid.uuid4()
    return CatalogMaster.all_tenants.create(
        id=row_id,
        catalog_specialist_id=row_id,
        tenant=tenant,
        external_id=int(str(row_id.int)[:6]),
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name=name,
        invite_status="accepted",
        is_active=True,
    )


def _working_hours(master, *, start="10:00", end="19:00"):
    from datetime import time

    from apps.scheduling.models import WorkingHours

    for wd in range(7):
        WorkingHours.all_tenants.create(
            tenant=master.tenant,
            master=master,
            day_of_week=wd,
            is_working=True,
            start_time=time.fromisoformat(start),
            end_time=time.fromisoformat(end),
        )


def _friday_request(master, *, start="10:00", end="12:00"):
    """Заявка «в пятницу с 10 до 12 меня нет» — окно блокировки утра."""
    from zoneinfo import ZoneInfo

    from apps.scheduling.models import ScheduleChangeRequest

    tz = ZoneInfo("Europe/Moscow")
    today = timezone.now().astimezone(tz).date()
    friday = today + timedelta(days=((4 - today.weekday()) % 7) or 7)
    s = datetime.combine(friday, datetime.strptime(start, "%H:%M").time(), tzinfo=tz)
    e = datetime.combine(friday, datetime.strptime(end, "%H:%M").time(), tzinfo=tz)
    return ScheduleChangeRequest.all_tenants.create(
        tenant=master.tenant,
        master=master,
        requested_change={"type": "off_time", "start": s.isoformat(), "end": e.isoformat()},
        requested_start=s,
        requested_end=e,
        reason_class="personal",
        reason_text=f"позвонить {PHONE}",
        status=ScheduleChangeRequest.Status.PENDING,
    )


# ── p1 — построение и рендер каждого типа ────────────────────────────


@pytest.mark.django_db
class TestP1EveryKindRendersWithButtons:
    def _all(self, salon):
        from apps.channels.max import salon_notify as sn

        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)
        task = SimpleNamespace(
            id=uuid.uuid4(),
            tenant=salon.tenant,
            reason=f"клиент {PHONE} ждёт",
            bot_user=SimpleNamespace(client_name="Анна", phone=PHONE, display_name="Анна"),
            priority="high",
        )
        age = SimpleNamespace(
            slug=salon.tenant.slug,
            tenant_id=str(salon.tenant.id),
            last_ok_at=timezone.now() - timedelta(hours=3),
            age_seconds=3 * 3600,
            threshold_seconds=3600,
            age_human="3 ч",
            is_stale=True,
        )
        proxy = SimpleNamespace(
            appointment_id=uuid.uuid4(),
            tenant=salon.tenant,
            start_at=timezone.now() + timedelta(days=1),
            bot_user=SimpleNamespace(client_name="Анна", phone=PHONE),
            master_name="Лера",
            service_name="Массаж",
        )
        return {
            "handoff": sn.handoff_waiting_notice(task),
            "schedule": sn.schedule_request_notice(req, impact=sn.Impact(state="ok", affected=1)),
            "sync": sn.sync_failed_notice(salon.tenant, age),
            "master_off": sn.master_unavailable_notice(master, block="revoked"),
            "booking": sn.booking_attention_notice(proxy, reason="cancelled_by_client"),
        }

    def test_kinds_are_the_five_of_the_ticket(self) -> None:
        from apps.channels.max import salon_notify as sn

        assert set(sn.KINDS) == {"handoff", "schedule", "sync", "master_off", "booking"}

    def test_each_kind_renders_title_facts_and_parseable_buttons(self, salon, two_bots) -> None:
        from apps.channels.max import salon_notify as sn

        notices = self._all(salon)
        assert set(notices) == set(sn.KINDS)  # положительно: все пять построены
        for kind, notice in notices.items():
            assert notice.kind == kind
            text = sn.render(notice)
            assert notice.title and notice.title in text, kind
            assert notice.buttons, kind  # положительно: кнопки есть
            for b in notice.buttons:
                if b.url:
                    continue
                parsed = sn.parse_callback(sn.callback(notice, b.action))
                assert parsed == (kind, notice.ref, b.action), (kind, b)
            assert not violates_phone_rule(text), (kind, text)

    def test_the_decision_kinds_offer_a_decision_and_the_rest_a_door(self, salon, two_bots) -> None:
        notices = self._all(salon)
        actions = {k: {b.action for b in n.buttons if not b.url} for k, n in notices.items()}
        urls = {k: [b for b in n.buttons if b.url] for k, n in notices.items()}
        assert {"approve", "reject", "details"} <= actions["schedule"]
        assert {"open", "return"} <= actions["handoff"]
        assert {"retry", "details"} <= actions["sync"]
        assert urls["master_off"] and urls["booking"]  # «Открыть карточку» / «Открыть запись»


# ── p2 — узел: событие → одно сообщение; повтор → нет дубля ──────────


@pytest.mark.django_db
class TestP2OneMessagePerEvent:
    def test_sent_once_as_the_salon_bot_with_a_keyboard(self, salon, two_bots, capture) -> None:
        from apps.channels.max import salon_notify as sn

        age = SimpleNamespace(
            slug=salon.tenant.slug,
            tenant_id=str(salon.tenant.id),
            last_ok_at=None,
            age_seconds=None,
            threshold_seconds=3600,
            age_human="никогда",
            is_stale=True,
        )
        notice = sn.sync_failed_notice(salon.tenant, age)
        first = sn.notify(notice)
        again = sn.notify(notice)
        assert first is not None and first.sent == 1
        assert again is None  # повтор — именованный дубль, не отправка
        assert len(capture) == 1
        assert capture[0]["token"] == SALON_TOKEN
        assert capture[0]["user_id"] == "owner-2118"
        payloads = [
            b.get("payload") for b in _buttons_of(capture[0]) if b.get("type") == "callback"
        ]
        assert payloads and all(p.startswith("cb:salon:n:sync:") for p in payloads), payloads

    def test_a_different_event_of_the_same_kind_is_a_new_message(
        self, salon, two_bots, capture
    ) -> None:
        from apps.channels.max import salon_notify as sn

        master = _master(salon.tenant)
        _working_hours(master)
        a = sn.schedule_request_notice(_friday_request(master), impact=sn.Impact("ok", 0))
        b = sn.schedule_request_notice(_friday_request(master), impact=sn.Impact("ok", 0))
        assert sn.notify(a) is not None and sn.notify(b) is not None
        assert len(capture) == 2

    def test_two_concurrent_claims_yield_one_send(self) -> None:
        """Узел: событие один раз при двух конкурентных вызовах — ``cache.add`` атомарен."""
        from concurrent.futures import ThreadPoolExecutor

        from apps.channels.max import salon_notify as sn

        key = f"salon_notify:test:{uuid.uuid4()}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: sn._claim(key), range(2)))
        assert sorted(outcomes) == [False, True]


# ── p3 — чужой салон не получает ─────────────────────────────────────


@pytest.mark.django_db
class TestP3OtherSalonNeverHearsIt:
    def test_only_own_staff(self, salon, other_salon, two_bots, capture) -> None:
        from apps.channels.max import salon_notify as sn

        master = _master(salon.tenant)
        _working_hours(master)
        notice = sn.schedule_request_notice(_friday_request(master), impact=sn.Impact("ok", 0))
        sn.notify(notice)
        assert [c["user_id"] for c in capture] == ["owner-2118"]  # чужого owner-other нет


# ── p4 — тип 2 на проводе: Было / Станет / Затронуто ─────────────────


@pytest.mark.django_db
class TestP4ScheduleRequestFormat:
    def test_was_becomes_affected_and_three_buttons(self, salon, two_bots, capture) -> None:
        from apps.master_api.services.schedule import notify_manager_of_availability_request

        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)  # 10:00–12:00 из 10:00–19:00 → станет 12:00–19:00
        with patch(
            "apps.admin_api.services.schedule_impact.impact_for_window",
            return_value=SimpleNamespace(state="ok", affected=1),
        ):
            notify_manager_of_availability_request(
                tenant=salon.tenant, master=master, request_id=req.id
            )
        assert len(capture) == 1
        text = capture[0]["text"]
        assert "Анна" in text and "пятниц" in text.lower()
        assert "Было: 10:00–19:00" in text
        assert "Станет: 12:00–19:00" in text
        assert "Затронута одна запись" in text or "Затронуто записей: 1" in text
        assert not violates_phone_rule(text)  # reason_text с телефоном — не в тексте
        labels = [b["text"] for b in _buttons_of(capture[0])]
        assert labels[:3] == ["Одобрить", "Отклонить", "Подробнее"]

    def test_impact_unavailable_is_named_not_zero(self, salon, two_bots, capture) -> None:
        from apps.master_api.services.schedule import notify_manager_of_availability_request

        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)
        with patch(
            "apps.admin_api.services.schedule_impact.impact_for_window",
            return_value=SimpleNamespace(state="unavailable", affected=None),
        ):
            notify_manager_of_availability_request(
                tenant=salon.tenant, master=master, request_id=req.id
            )
        text = capture[0]["text"]
        assert "Было: 10:00–19:00" in text  # положительно: диф на месте
        assert "не удалось" in text.lower() or "источник не ответил" in text.lower()
        assert "Затронуто записей: 0" not in text and "Никого не затронет" not in text


# ── p5 — кнопки типа 2: решение тем же сервисом, что у Mini App ──────


@pytest.mark.django_db
class TestP5ScheduleDecisionButtons:
    def _ctx(self, salon):
        from apps.identity.services.role_resolver import resolve_role

        return resolve_role(salon.owner)

    def test_approve_records_the_decision_once(self, salon, two_bots, capture, settings) -> None:
        from apps.channels.max import salon_notify as sn
        from apps.channels.max.salon_notify_actions import handle_action
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)
        payload = sn.callback(sn.schedule_request_notice(req, impact=sn.Impact("ok", 0)), "approve")

        reply = handle_action(
            payload=payload, tenant=salon.tenant, bot_user=salon.owner, role_ctx=self._ctx(salon)
        )
        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.APPROVED
        assert "одобр" in reply.lower()

        again = handle_action(
            payload=payload, tenant=salon.tenant, bot_user=salon.owner, role_ctx=self._ctx(salon)
        )
        assert "уже" in again.lower()
        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.APPROVED  # второго решения нет

    def test_reject_uses_a_coded_reason(self, salon, two_bots, capture, settings) -> None:
        from apps.channels.max import salon_notify as sn
        from apps.channels.max.salon_notify_actions import REJECT_REASON_BY_CODE, handle_action
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)
        payload = sn.callback(sn.schedule_request_notice(req, impact=sn.Impact("ok", 0)), "reject")
        reply = handle_action(
            payload=payload, tenant=salon.tenant, bot_user=salon.owner, role_ctx=self._ctx(salon)
        )
        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.REJECTED
        assert req.resolution_note in set(REJECT_REASON_BY_CODE.values())
        assert "отклон" in reply.lower()

    def test_details_shows_the_impact(self, salon, two_bots, capture) -> None:
        from apps.channels.max import salon_notify as sn
        from apps.channels.max.salon_notify_actions import handle_action

        master = _master(salon.tenant)
        _working_hours(master)
        req = _friday_request(master)
        payload = sn.callback(sn.schedule_request_notice(req, impact=sn.Impact("ok", 2)), "details")
        with patch(
            "apps.admin_api.services.schedule_impact.impact_for_window",
            return_value=SimpleNamespace(state="ok", affected=2),
        ):
            reply = handle_action(
                payload=payload,
                tenant=salon.tenant,
                bot_user=salon.owner,
                role_ctx=self._ctx(salon),
            )
        assert "2" in reply and "Было: 10:00–19:00" in reply
        assert not violates_phone_rule(reply)

    def test_a_master_cannot_decide(self, salon, two_bots, capture, settings) -> None:
        from apps.channels.max import salon_notify as sn
        from apps.channels.max.salon_notify_actions import handle_action
        from apps.identity.models import BotUser
        from apps.identity.services.role_resolver import resolve_role
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        master = _master(salon.tenant)
        _working_hours(master)
        person = BotUser.all_tenants.create(
            tenant=salon.tenant, channel="max", channel_user_id="m-2118", chat_id="c-m-2118"
        )
        master.linked_bot_user = person
        master.save(update_fields=["linked_bot_user"])
        req = _friday_request(master)
        payload = sn.callback(sn.schedule_request_notice(req, impact=sn.Impact("ok", 0)), "approve")
        handle_action(
            payload=payload, tenant=salon.tenant, bot_user=person, role_ctx=resolve_role(person)
        )
        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.PENDING


# ── p6 — источники подключены к рендереру ────────────────────────────


@pytest.mark.django_db
class TestP6SourcesAreWired:
    def test_stale_sync_notifies_the_salon_too(self, salon, two_bots, capture) -> None:
        from apps.catalog import tasks as catalog_tasks

        age = SimpleNamespace(
            slug=salon.tenant.slug,
            tenant_id=str(salon.tenant.id),
            last_ok_at=None,
            age_seconds=None,
            threshold_seconds=3600,
            age_human="никогда",
            is_stale=True,
        )
        with (
            patch.object(catalog_tasks, "sync_ages", return_value=[age]),
            patch.object(catalog_tasks, "page", return_value=True) as page,
        ):
            catalog_tasks.alert_stale_catalog_sync()
        assert page.call_count == 1  # операторский канал остаётся
        assert len(capture) == 1 and capture[0]["user_id"] == "owner-2118"
        assert "синхрониз" in capture[0]["text"].lower()

    def test_master_losing_sale_in_sync_notifies(self, salon, two_bots, capture) -> None:
        from apps.catalog.services.http_client import CatalogSpecialistDTO
        from apps.catalog.services.upserter import upsert_specialists

        master = _master(salon.tenant, name="Лера")
        ayla_user = uuid.uuid4()
        master.ayla_user_id = ayla_user
        master.save(update_fields=["ayla_user_id"])
        dto = CatalogSpecialistDTO(
            ayla_master_id=str(master.id),
            user_id=str(ayla_user),
            name="Лера",
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            tenant=str(salon.tenant.id),
            is_active=False,
        )
        upsert_specialists(salon.tenant, [dto])
        assert len(capture) == 1
        assert "Лера" in capture[0]["text"]
        # Повторный синк с тем же состоянием — не второе сообщение.
        upsert_specialists(salon.tenant, [dto])
        assert len(capture) == 1

    def test_master_off_reaches_the_master_too(self, salon, two_bots, capture) -> None:
        """Тип 4 — мастеру о себе: копия человеку, привязанному к строке.

        Переключателя для этого события в ``MasterNotificationPrefs`` нет
        (new_booking / booking_change / personal_message — про клиентов,
        urgent принудительно включён) — поэтому без чтения чужого."""
        from apps.channels.max import salon_notify as sn
        from apps.identity.models import BotUser

        master = _master(salon.tenant, name="Лера")
        person = BotUser.all_tenants.create(
            tenant=salon.tenant, channel="max", channel_user_id="m-off", chat_id="c-m-off"
        )
        master.linked_bot_user = person
        master.save(update_fields=["linked_bot_user"])
        sn.notify(sn.master_unavailable_notice(master, block="revoked"))
        assert sorted(c["user_id"] for c in capture) == ["m-off", "owner-2118"]

    def test_handoff_creation_notifies_the_salon(self, salon, two_bots, capture) -> None:
        from apps.handoff.notify import notify_admin_task_created

        task = SimpleNamespace(
            id=uuid.uuid4(),
            tenant=salon.tenant,
            reason=f"клиент {PHONE} просит человека",
            bot_user=SimpleNamespace(client_name="Анна", phone=PHONE, display_name="Анна"),
            priority="normal",
            task_type="manual_handoff",
            conversation=SimpleNamespace(id=uuid.uuid4()),
        )
        notify_admin_task_created(task)
        assert len(capture) == 1 and capture[0]["user_id"] == "owner-2118"
        assert not violates_phone_rule(capture[0]["text"])

    def test_cancel_and_no_show_consumers_call_the_attention_hook(self) -> None:
        src = (APPS_ROOT / "eventbus/consumers/booking.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        hooked: dict[str, bool] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in (
                "handle_booking_cancelled",
                "handle_booking_no_show",
            ):
                hooked[fn.name] = any(
                    isinstance(n, ast.Call)
                    and getattr(n.func, "id", getattr(n.func, "attr", ""))
                    == "schedule_booking_attention_notification"
                    for n in ast.walk(fn)
                )
        assert set(hooked) == {"handle_booking_cancelled", "handle_booking_no_show"}
        assert all(hooked.values()), hooked
