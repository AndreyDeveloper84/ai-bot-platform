"""Приветствия персонала по роли с живой сводкой (DRF-2114, §50 п.4).

* g1 — тексты владельца — константы, сверяются дословно (мастер, владелец /
  администратор, первое, спокойное — без «графики и услуги настроены» до
  DRF-2117);
* g2 — мастер: имя с карточки, салон, «Сегодня у вас N записей», «Ближайшая —
  клиент, услуга N минут, в HH:MM» из подменённого дня; четыре кнопки;
* g3 — владелец / администратор: три строки сводки из подменённых
  источников, пять кнопок (§50 п.6: Команда/Услуги/Чаты/Настройки — нет);
* g4 — первое приветствие по ``welcomed_at`` рабочей строки, «Проверить
  готовность» → «Сегодня» с честной строкой, штамп после; второй вход —
  обычное;
* g5 — числа живые: подмена источника меняет число; отказ источника —
  строка опущена, не «0»; ноль внимания — спокойная форма;
* g6 — когда звучит: «/start», тап «какой салон?», «Повторить проверку»,
  свободный текст владельца (без ассистента); мастеру на текст —
  ассистент (как раньше);
* g7 — слаги семи экранов тройки в ``MINIAPP_ROUTES`` и ``_ROUTE_MAP`` —
  один к одному;
* g8 — склонения: записи / мастера / ситуации.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.admin_api.services.salon_day import DayMaster, DaySummary, DayVisit, SalonDay
from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_entry, salon_greeting, salon_handler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.skills.welcome.skill import MINIAPP_ROUTES
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "2114001"
CHAT_ID = "2114"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="secret-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    web_app="https://app.example/salon",
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _staff_are_linked(monkeypatch):
    """Связь с каталогом — предмет DRF-2113, здесь строки считаются связанными."""
    monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")


@pytest.fixture(autouse=True)
def _fresh_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _payload(text: str, *, update_id: int = 1) -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Андрей Иванов", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, *, update_id: int = 1) -> None:
    handle_salon_max_event(_payload(text, update_id=update_id))


def _labels(call) -> list[str]:
    out: list[str] = []
    for att in call.kwargs.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            out.extend(b["text"] for b in row)
    return out


def _buttons(call) -> list[dict]:
    out: list[dict] = []
    for att in call.kwargs.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            out.extend(row)
    return out


def _owner(tenant: Tenant, *, greeted: bool = False) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=CHAT_ID,
        display_name="Андрей Иванов",
        welcomed_at=timezone.now() if greeted else None,
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.OWNER)
    return user


def _master(tenant: Tenant) -> tuple[BotUser, CatalogMaster]:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=CHAT_ID,
        display_name="Анна Петрова",
    )
    card = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-2114,
        external_updated_at=timezone.now(),
        name="Анна Петрова",
        linked_bot_user=user,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        accepted_at=timezone.now(),
        ayla_user_id=uuid.uuid4(),
    )
    return user, card


MSK = dt_timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 19, 9, 30, tzinfo=MSK)


def _visit(
    start: datetime,
    *,
    status: str = "confirmed",
    client: str = "Мария",
    service: str = "массаж",
    minutes: int = 60,
) -> DayVisit:
    return DayVisit(
        id=str(uuid.uuid4()),
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        duration_min=minutes,
        status=status,
        service_id="svc",
        service_name=service,
        client_first_name=client,
        client_last_initial="К",
        is_in_progress=False,
    )


def _day(masters: list[DayMaster]) -> SalonDay:
    visits = [v for m in masters for v in m.visits]
    return SalonDay(
        date=date(2026, 9, 19),
        timezone_name="Europe/Moscow",
        masters=masters,
        summary=DaySummary(
            total=len(visits),
            upcoming=sum(1 for v in visits if v.status == "confirmed"),
            completed=sum(1 for v in visits if v.status == "completed"),
            released=sum(1 for v in visits if v.status in ("cancelled", "no_show")),
        ),
    )


def _fake_day(master_id: str, n_confirmed: int, *, released: int = 0) -> SalonDay:
    visits = [
        _visit(NOW + timedelta(hours=i + 0.5), client=("Мария" if i == 0 else f"Клиент{i}"))
        for i in range(n_confirmed)
    ]
    visits += [_visit(NOW + timedelta(hours=9), status="cancelled") for _ in range(released)]
    return _day(
        [DayMaster(master_id=master_id, name="Анна Петрова", is_active=True, visits=visits)]
    )


@pytest.fixture
def sources(monkeypatch):
    """Три источника сводки — подменяемые; по умолчанию: 7 записей, 3 мастера, 1 ситуация."""
    state = {"day": _fake_day("m-1", 7), "masters": 3, "attention": 1}
    monkeypatch.setattr(salon_greeting, "_salon_day", lambda tenant, now: state["day"])
    monkeypatch.setattr(salon_greeting, "_masters_available", lambda: state["masters"])
    monkeypatch.setattr(salon_greeting, "_attention", lambda: state["attention"])
    monkeypatch.setattr(salon_greeting, "_tenant_now", lambda tenant: NOW)
    return state


class TestG1TheOwnersTextsAreConstants:
    def test_verbatim(self) -> None:
        assert salon_greeting.MASTER_ROLE_LINE == "Вы вошли в Ayla для салона «{salon}» как мастер."
        assert salon_greeting.MASTER_TODAY_LINE == "Сегодня у вас {records}."
        assert (
            salon_greeting.MASTER_NEXT_LINE
            == "Ближайшая — {client}, {service} {minutes} минут, в {time}."
        )
        assert salon_greeting.ADMIN_ROLE_LINE == "Вы вошли в Ayla для салона «{salon}» как {role}."
        assert salon_greeting.ADMIN_TODAY_HEAD == "Сегодня:"
        assert salon_greeting.ADMIN_CALM_LINE == (
            "В салоне «{salon}» всё в порядке: сегодня {records}; ожидающих ответа нет."
        )
        assert "графики и услуги настроены" not in salon_greeting.ADMIN_CALM_LINE  # до DRF-2117
        assert salon_greeting.FIRST_GREETING.endswith(
            "Сначала проверим, готов ли салон принимать записи."
        )
        assert (
            "Здесь можно управлять записями, диалогами с клиентами, расписанием, услугами и командой."
            in (salon_greeting.FIRST_GREETING)
        )
        assert salon_greeting.FIRST_GREETING_READINESS_PENDING == (
            "Проверка готовности появится здесь позже — пока откроется «Сегодня»."
        )


class TestG2TheMasterGreeting:
    def test_text_and_four_buttons(self, tenant, sources, sent) -> None:
        _user, card = _master(tenant)
        sources["day"] = _fake_day(str(card.id), 4)
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert text == (
            "Здравствуйте, Анна!\n"
            "Вы вошли в Ayla для салона «Формула тела» как мастер.\n"
            "Сегодня у вас 4 записи.\n"
            "Ближайшая — Мария, массаж 60 минут, в 10:00."
        )
        assert _labels(sent.call_args) == [
            "Открыть кабинет",
            "Сегодня",
            "Расписание",
            "Спросить Ayla",
        ]
        payloads = [
            b.get("payload") for b in _buttons(sent.call_args) if b.get("type") == "open_app"
        ]
        assert payloads == [
            "staff_open_app",
            "open_master_today",
            "open_master_schedule",
            "open_master_ayla",
        ]

    def test_no_visits_today_no_next_line(self, tenant, sources, sent) -> None:
        _user, card = _master(tenant)
        sources["day"] = _fake_day(str(card.id), 0)
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert "Сегодня у вас 0 записей." in text
        assert "Ближайшая" not in text


class TestG3TheOwnerGreeting:
    def test_summary_and_five_buttons(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert text == (
            "Здравствуйте, Андрей!\n"
            "Вы вошли в Ayla для салона «Формула тела» как владелец.\n"
            "Сегодня:\n"
            "7 записей;\n"
            "работают 3 мастера;\n"
            "одна ситуация требует внимания."
        )
        assert _labels(sent.call_args) == [
            "Открыть салон",
            "Сегодня",
            "Расписание",
            "Спросить Ayla",
            "＋ Новая запись",
        ]
        payloads = [
            b.get("payload") for b in _buttons(sent.call_args) if b.get("type") == "open_app"
        ]
        assert payloads == [
            "staff_open_app",
            "open_admin_today",
            "open_admin_schedule",
            "open_admin_ayla",
            "open_admin_booking_new",
        ]
        for forbidden in ("Команда", "Услуги", "Чаты", "Настройки"):
            assert forbidden not in _labels(sent.call_args)  # §50 п.6

    def test_admin_role_word(self, tenant, sources, sent) -> None:
        user = _owner(tenant, greeted=True)
        TenantStaff.all_tenants.filter(bot_user=user).update(role=TenantStaff.Role.ADMIN)
        _handle("/start")
        assert "как администратор." in sent.call_args.kwargs["text"]


class TestG4FirstThenRegular:
    def test_first_entry_then_second(self, tenant, sources, sent) -> None:
        user = _owner(tenant, greeted=False)
        _handle("/start", update_id=1)
        text = sent.call_args.kwargs["text"]
        assert text == (
            "Здравствуйте, Андрей!\n\n"
            "Вы вошли в Ayla для салона «Формула тела».\n"
            "Ваша роль — владелец.\n\n"
            "Здесь можно управлять записями, диалогами с клиентами, расписанием, услугами "
            "и командой. Ayla будет писать сюда, когда потребуется ваше решение.\n\n"
            "Сначала проверим, готов ли салон принимать записи.\n"
            "Проверка готовности появится здесь позже — пока откроется «Сегодня»."
        )
        assert _labels(sent.call_args) == ["Проверить готовность", "Открыть салон"]
        readiness = _buttons(sent.call_args)[0]
        assert readiness["payload"] == "open_admin_today"  # до DRF-2117 — на «Сегодня»
        user.refresh_from_db()
        assert user.welcomed_at is not None

        _handle("/start", update_id=2)
        assert sent.call_count == 2
        assert sent.call_args.kwargs["text"].startswith(
            "Здравствуйте, Андрей!\nВы вошли в Ayla для салона"
        )
        assert "Сначала проверим" not in sent.call_args.kwargs["text"]


class TestG5TheNumbersAreLive:
    def test_swapping_a_source_changes_the_number(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        sources["day"] = _fake_day("m-1", 12, released=2)  # 14 всего, 2 снято → 12
        sources["masters"] = 5
        sources["attention"] = 3
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert "12 записей;" in text and "работают 5 мастеров;" in text
        assert "3 ситуации требуют внимания." in text

    def test_an_unavailable_source_drops_its_line_not_a_zero(
        self, tenant, sources, sent, monkeypatch
    ) -> None:
        _owner(tenant, greeted=True)

        def _boom(*a, **kw):
            raise RuntimeError("mirror down")

        monkeypatch.setattr(salon_greeting, "_salon_day", _boom)
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert text.startswith("Здравствуйте, Андрей!")  # положительно: приветствие есть
        assert "запис" not in text  # ни «0 записей», ни строки вовсе
        assert "работают 3 мастера;" in text
        assert "одна ситуация требует внимания." in text

    def test_zero_attention_is_the_calm_form(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        sources["attention"] = 0
        _handle("/start")
        text = sent.call_args.kwargs["text"]
        assert text.endswith(
            "В салоне «Формула тела» всё в порядке: сегодня 7 записей; ожидающих ответа нет."
        )
        assert "графики и услуги настроены" not in text

    def test_gather_reads_the_real_available_predicate(self, tenant) -> None:
        """Положительная стража источнику: реальная карточка — реальное число."""
        _user, card = _master(tenant)
        card.catalog_specialist_id = uuid.uuid4()
        card.save(update_fields=["catalog_specialist_id"])
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            assert salon_greeting._masters_available() == 1
            assert salon_greeting._attention() == 0


class TestG6WhenItSounds:
    def test_free_text_from_the_owner_greets(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        _handle("привет")
        assert sent.call_args.kwargs["text"].startswith("Здравствуйте, Андрей!")

    def test_recheck_passed_greets(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        _handle(salon_handler.CB_SALON_RECHECK)
        assert sent.call_args.kwargs["text"].startswith("Здравствуйте, Андрей!")

    def test_salon_choice_tap_greets(self, tenant, sources, sent) -> None:
        _owner(tenant, greeted=True)
        _handle(f"{salon_handler.CB_SALON_CHOOSE_PREFIX}{tenant.slug}")
        assert sent.call_args.kwargs["text"].startswith("Здравствуйте, Андрей!")

    def test_master_free_text_goes_to_the_assistant(
        self, tenant, sources, sent, monkeypatch
    ) -> None:
        _master(tenant)
        seen: list[str] = []

        def _assistant(bot_user, thread, text, *, exclude_id=None):
            seen.append(text)
            return SimpleNamespace(
                text="Ответ ассистента",
                tool_name="",
                tokens_in=0,
                tokens_out=0,
                llm_provider="",
                llm_model="",
                llm_cost_usd=0,
            )

        monkeypatch.setattr(salon_handler, "_ask_assistant", _assistant)
        _handle("сколько у меня записей?")
        assert seen == ["сколько у меня записей?"]
        assert sent.call_args.kwargs["text"] == "Ответ ассистента"

    def test_the_precheck_still_gates(self, tenant, sources, sent, monkeypatch) -> None:
        """Приветствие звучит только за пре-чеком: NOT_LINKED — прежнее честное состояние."""
        _owner(tenant, greeted=True)
        monkeypatch.setattr(
            "apps.channels.max.salon_entry.unlinked_reason",
            lambda *a, **kw: salon_entry.REASON_NO_AYLA_USER,
        )
        _handle("/start")
        assert sent.call_args.kwargs["text"] == salon_handler.NOT_LINKED_TEXT


class TestG7TheSlugsOfTheTriple:
    def test_both_maps_carry_the_seven(self) -> None:
        expected = {
            "open_admin_today": "admin/today",
            "open_admin_schedule": "admin/schedule",
            "open_admin_ayla": "admin/ayla",
            "open_admin_booking_new": "admin/booking/new",
            "open_master_today": "master/dashboard",
            "open_master_schedule": "master/schedule",
            "open_master_ayla": "master/ayla",
        }
        assert {k: MINIAPP_ROUTES[k] for k in expected} == expected
        sdk = (Path(__file__).resolve().parents[3] / "apps/miniapp/src/lib/max-sdk.ts").read_text(
            encoding="utf-8"
        )
        for slug, path in expected.items():
            assert re.search(rf'\b{slug}: "/{re.escape(path)}"', sdk), slug
        assert "open_admin_handoff" not in MINIAPP_ROUTES  # вход — только с карточки «Сегодня»
        assert set(salon_greeting.ADMIN_SLUGS.values()) | set(
            salon_greeting.MASTER_SLUGS.values()
        ) == set(expected)


class TestG8Declensions:
    @pytest.mark.parametrize(
        ("n", "text"),
        [(1, "1 запись"), (2, "2 записи"), (7, "7 записей"), (11, "11 записей"), (21, "21 запись")],
    )
    def test_records(self, n, text) -> None:
        assert salon_greeting.records_phrase(n) == text

    @pytest.mark.parametrize(
        ("n", "text"),
        [(1, "работает 1 мастер"), (3, "работают 3 мастера"), (5, "работают 5 мастеров")],
    )
    def test_masters(self, n, text) -> None:
        assert salon_greeting.masters_phrase(n) == text

    @pytest.mark.parametrize(
        ("n", "text"),
        [
            (1, "одна ситуация требует внимания"),
            (2, "2 ситуации требуют внимания"),
            (5, "5 ситуаций требуют внимания"),
        ],
    )
    def test_attention(self, n, text) -> None:
        assert salon_greeting.attention_phrase(n) == text
