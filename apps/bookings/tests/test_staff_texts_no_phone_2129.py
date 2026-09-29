"""Тексты персоналу без телефона клиента; «Карина» не зашита (DRF-2129, DRF-1039 / OD-W2-2).

Два текста менеджеру печатали телефон клиента (эскалация напоминания;
«перенос не завершён» — и его пара «клиент перенёс запись»), а DM мастеру
об отклонённой заявке звал «Карину» по имени из кода. DRF-1039: телефон
клиента исполнителю и салону в текстах не передаётся; сторож
``master_api/tests/test_pii_boundary`` держит только ``master_api`` — тексты
менеджеру были без стража.

Сторож здесь — **перепись по роли, по текстам как они есть** (адресат —
менеджер / мастер). С DRF-2128 общий вход есть —
:func:`apps.channels.max.staff_outbound.send_to_staff` — и проверка «телефона
нет» стоит и на нём: ``channels/max/tests/test_staff_outbound_2128`` p5
прогоняет каждый путь персоналу и смотрит текст, дошедший до провода.
Перепись строителей ниже остаётся: она ловит новый строитель раньше, чем
у него появится вызывающий.

* p1 — перепись строителей текста персоналу: каждая функция
  ``_render_*_text`` / ``_format_*_text`` / ``render_*_text`` в трёх модулях
  зарегистрирована с ролью; незарегистрированная → красный;
* p2 — каждый зарегистрированный текст менеджеру: цифр телефона нет
  (ни хвоста из 7, ни в любом форматировании), имя клиента есть
  (положительная пара), ID записи на месте;
* p3 — ложный вход: текст с подсаженным телефоном в четырёх написаниях →
  страж красный; текст без телефона → зелёный;
* p4 — DM мастеру: «Карин» нет; с известным решившим — его имя, без —
  «у администратора салона»;
* p5 — ссылка на день салона в текстах менеджеру: есть, когда у салонного
  бота настроен Mini App; нет — текст тот же, без телефона.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.admin_api import tasks as admin_tasks
from apps.bookings import callbacks, escalation

PHONE = "79991234567"
PHONE_TAIL = PHONE[-7:]

#: Строители текста персоналу — по роли адресата. Новый строитель без
#: записи здесь — красный p1.
REGISTRY: dict[str, str] = {
    "apps.bookings.escalation._format_escalation_text": "manager",
    "apps.bookings.callbacks._render_partial_failure_text": "manager",
    "apps.bookings.callbacks._render_reschedule_success_text": "manager",
    "apps.admin_api.tasks.render_master_decision_text": "master",
}

_MODULES = (escalation, callbacks, admin_tasks)
_BUILDER_RE = re.compile(r"^(_render_.*_text|_format_.*_text|render_.*_text)$")


def phone_digit_runs(text: str) -> list[str]:
    """Все цепочки ≥ 7 цифр после снятия форматирования — то, что читается как телефон."""
    digits = re.sub(r"[^\d]", "", text)
    # Отдельно: любая последовательность из ≥ 7 цифр внутри одного «слова с разделителями».
    runs = re.findall(r"(?:\+?\d[\s().-]*){7,}", text)
    normalised = [re.sub(r"[^\d]", "", r) for r in runs]
    return [r for r in normalised if len(r) >= 7] + ([digits] if PHONE_TAIL in digits else [])


def violates_phone_rule(text: str, phone: str = PHONE) -> bool:
    """Телефон клиента — в любом написании — в тексте персоналу."""
    tail = re.sub(r"[^\d]", "", phone)[-7:]
    return any(tail in run for run in phone_digit_runs(text))


def _builders(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return [
        f"{module.__name__}.{node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _BUILDER_RE.match(node.name)
    ]


VISIT_AT = datetime(2026, 9, 20, 11, 0, tzinfo=dt_timezone.utc)


def _reminder(**over):
    base = dict(
        id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        booking_request_id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        bot_user=SimpleNamespace(phone=PHONE, client_name="Anna"),
        visit_at=VISIT_AT,
        service_name="Массаж",
        master_name="Lera",
        tenant=SimpleNamespace(slug="formula-tela", name="Формула тела"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _pending(**over):
    payload = {
        "record_id": 555,
        "new_datetime": "2026-06-01T14:00:00",
        "service_name": "Массаж",
        "master_name": "Ольга",
        "client_name": "Anna",
        "client_phone": PHONE,
    }
    payload.update(over)
    return SimpleNamespace(payload=payload, tenant=SimpleNamespace(slug="formula-tela"))


class TestP1EveryStaffTextBuilderIsRegistered:
    def test_census(self) -> None:
        found = [name for m in _MODULES for name in _builders(m)]
        assert len(found) >= 3  # положительно: строители найдены
        missing = sorted(set(found) - set(REGISTRY))
        assert missing == [], f"строители текста персоналу без роли в реестре: {missing}"
        stale = sorted(set(REGISTRY) - set(found))
        assert stale == [], f"реестр устарел: {stale}"


class TestP2ManagerTextsCarryNoPhone:
    def test_escalation(self) -> None:
        text = escalation._format_escalation_text(_reminder())
        assert "Anna" in text and "Массаж" in text and "Lera" in text  # положительная пара
        assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in text  # ID записи на месте
        assert not violates_phone_rule(text), text

    def test_partial_failure(self) -> None:
        text = callbacks._render_partial_failure_text(_pending())
        assert "Anna" in text and "555" in text
        assert not violates_phone_rule(text), text

    def test_reschedule_success(self) -> None:
        text = callbacks._render_reschedule_success_text(
            _pending(), SimpleNamespace(confirmation=SimpleNamespace(record_id="777"))
        )
        assert "Anna" in text and "777" in text
        assert not violates_phone_rule(text), text

    def test_the_name_survives_without_the_phone(self) -> None:
        """Имя клиента — в тексте; строки «Телефон:» и «(79…)» нет."""
        text = escalation._format_escalation_text(_reminder())
        assert "Имя: Anna" in text
        assert "Телефон" not in text


class TestP3TheGuardCatchesAPlantedPhone:
    @pytest.mark.parametrize(
        "planted",
        [
            "Клиент: Anna (79991234567)",
            "Телефон: +7 (999) 123-45-67",
            "тел. 8 999 123 45 67",
            "Anna, 999-123-45-67, массаж",
        ],
    )
    def test_planted(self, planted: str) -> None:
        assert violates_phone_rule(planted), planted

    def test_a_clean_text_with_ids_passes(self) -> None:
        clean = "Клиент: Anna. ID записи: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee. record_id=555. Визит: 20.09 в 11:00."
        assert "Anna" in clean and "555" in clean  # положительно: текст с ID на месте
        assert phone_digit_runs(clean) == []
        assert not violates_phone_rule(clean)


class TestP4TheMasterDecisionNamesNoHardcodedAdmin:
    def test_rejected_without_a_known_decider(self) -> None:
        text = admin_tasks.render_master_decision_text(
            decision="rejected",
            date_range_human="20–21 сентября",
            rejection_reason="в эти дни полная запись",
            decided_by=None,
            master_mini_app_url="https://app.example/master/schedule",
        )
        assert "у администратора салона" in text
        assert "Карин" not in text  # empty-assert-ok: положительная пара строкой выше
        assert "в эти дни полная запись" in text

    def test_rejected_with_a_known_decider(self) -> None:
        text = admin_tasks.render_master_decision_text(
            decision="rejected",
            date_range_human="20–21 сентября",
            rejection_reason="в эти дни полная запись",
            decided_by="Ольга",
            master_mini_app_url="https://app.example/master/schedule",
        )
        assert "Карин" not in text and "Ольга" in text

    def test_approved_is_unchanged_in_substance(self) -> None:
        text = admin_tasks.render_master_decision_text(
            decision="approved",
            date_range_human="20–21 сентября",
            rejection_reason="",
            decided_by=None,
            master_mini_app_url="https://app.example/master/schedule",
        )
        assert "одобрен" in text and "https://app.example/master/schedule" in text

    def test_no_hardcoded_first_name_in_the_module(self) -> None:
        source = Path(admin_tasks.__file__).read_text(encoding="utf-8")
        assert "render_master_decision_text" in source  # положительно: модуль прочитан
        assert "Карин" not in source  # empty-assert-ok: положительная пара строкой выше


@pytest.mark.django_db
class TestP4bTheDeciderIsReadFromTheRequestRow:
    """``_decider_first_name``: первое слово ``display_name`` решившего; пусто → None."""

    @staticmethod
    def _rows():
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        from apps.catalog.models import CatalogMaster
        from apps.identity.models import BotUser
        from apps.scheduling.models import ScheduleChangeRequest
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="t-2129", name="Салон", timezone="Europe/Moscow")
        decider = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9001",
            display_name="Ольга Петрова",
            chat_id="9001",
            phone="+79160000001",
        )
        row_id = _uuid.uuid4()
        master = CatalogMaster.all_tenants.create(
            id=row_id,
            catalog_specialist_id=row_id,
            tenant=tenant,
            external_id=1,
            external_updated_at=_dt.now(tz=_tz.utc),
            name="Лера",
        )
        req = ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            requested_change={"type": "exception_add", "date": "2026-06-11"},
            status=ScheduleChangeRequest.Status.REJECTED,
            resolved_by_bot_user_id=decider.id,
        )
        return req, master, decider

    def test_first_word_only(self) -> None:
        req, master, decider = self._rows()
        name = admin_tasks._decider_first_name(str(req.id), str(master.id))
        assert name == "Ольга"
        assert "Петрова" not in (name or "")  # empty-assert-ok: равенство строкой выше
        assert not violates_phone_rule(name or "", decider.phone)

    def test_empty_resolver_gives_none(self) -> None:
        req, master, _ = self._rows()
        type(req).all_tenants.filter(pk=req.id).update(resolved_by_bot_user_id=None)
        assert admin_tasks._decider_first_name(str(req.id), str(master.id)) is None

    def test_other_master_gives_none(self) -> None:
        req, _, _ = self._rows()
        assert admin_tasks._decider_first_name(str(req.id), str(uuid.uuid4())) is None


class TestP5TheSalonDayLink:
    def test_link_when_the_salon_mini_app_is_configured(self, settings) -> None:
        from apps.channels.bot_registry import BotEntry

        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                webhook_secret="s",  # pragma: allowlist secret
                api_token="t",  # pragma: allowlist secret
                stream="max_salon",
                miniapp_url="https://app.example",
            ),
        )
        text = escalation._format_escalation_text(_reminder())
        assert "https://app.example/admin/day?date=2026-09-20" in text
        assert not violates_phone_rule(text)
        # Та же ссылка в текстах переноса — день из payload.new_datetime.
        for built in (
            callbacks._render_partial_failure_text(_pending()),
            callbacks._render_reschedule_success_text(
                _pending(), SimpleNamespace(confirmation=SimpleNamespace(record_id="777"))
            ),
        ):
            assert "https://app.example/admin/day?date=2026-06-01" in built
            assert not violates_phone_rule(built)

    def test_no_link_without_a_salon_mini_app(self, settings) -> None:
        settings.MAX_BOT_REGISTRY = ()
        text = escalation._format_escalation_text(_reminder())
        assert "Anna" in text  # положительно: текст на месте
        assert "admin/day" not in text
        assert not violates_phone_rule(text)
