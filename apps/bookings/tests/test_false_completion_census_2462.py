"""DRF-2462: перепись поставленных ложных штампов — три корзины и очередь.

Производство ложных штампов остановлено (DRF-2454, #2072). Остались поставленные,
и они **видны мастеру сегодня**: список клиентов фильтрует по ``completed_at`` без
гейта ``completed_by``, то есть счётчик визитов и «последняя услуга» уже считают
отменённые визиты.

Узлы держат ровно то, чем можно ошибиться в обе стороны:

* ложный штамп по канону **попадает** в корзину уборки;
* законный **не попадает** — ни закрытый человеком, ни подтверждённый зеркалом;
* недоказуемый (нет зеркала / два зеркала / нет человека в строке) **не объявлен
  ложным**: обнулить его — та же ошибка, что поставить;
* очередь названа числом, и неразобранные события отделены от разобранных;
* ноль **доказан охватом**: на пустой выборке команда говорит, что нули ничего не
  доказывают;
* команда **ничего не меняет** — ни штампов, ни статусов, ни очереди.

``event_id`` строится тем же ``new_ulid()``, что и продакшн: колонка — 26 символов,
и Postgres это требование держит, а локальный SQLite молчит. Первая версия писала
``uuid4`` (36 символов) и была зелёной локально, красной в CI — предмет проверки
здесь очередь, и фикстура обязана быть такой же длины, как живая строка.
"""

from __future__ import annotations

import datetime as dt
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.eventbus.models import DomainEvent
from apps.eventbus.ulid import new_ulid
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="census-2462", name="Salon 2462")


def _customer(tenant, suffix: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=f"census-2462-{suffix}"
    )


def _stamped(
    tenant,
    customer,
    *,
    mirror_status: str | None,
    completed_by: str = "system",
    minutes: int = -300,
) -> BookingRequest:
    visit_at = timezone.now() + dt.timedelta(minutes=minutes)
    if mirror_status is not None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            start_at=visit_at,
            end_at=visit_at + dt.timedelta(minutes=60),
            status=mirror_status,
            source=RemoteBookingProxy.Source.MOBILE_APP,
        )
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service_name="Маникюр",
        client_name="Customer",
        client_phone="snapshot",
        visit_at=visit_at,
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        completed_at=timezone.now(),
        completed_by=completed_by,
        source="bot",
        booking_source="external",
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command("audit_false_completions", *args, stdout=out)
    return out.getvalue()


# ─── три корзины ───────────────────────────────────────────────────────────


class TestTheThreeBuckets:
    @pytest.mark.parametrize(
        "mirror_status", ("cancelled", "no_show", "pending_payment", "tentative")
    )
    def test_a_stamp_the_canon_contradicts_is_false(self, tenant, mirror_status: str) -> None:
        _stamped(tenant, _customer(tenant, mirror_status), mirror_status=mirror_status)

        text = _run()

        assert "охват (строк со штампом): 1" in text
        assert "ложных по канону (false_by_canon): 1" in text
        assert f"false_by_canon:mirror_{mirror_status}" in text

    def test_a_human_closer_is_legitimate_even_if_the_mirror_says_cancelled(self, tenant) -> None:
        """Зеркало отстаёт, человек — нет: его подпись сильнее."""
        _stamped(
            tenant,
            _customer(tenant, "human"),
            mirror_status="cancelled",
            completed_by="master:42",
        )

        text = _run()

        assert "законных (legitimate):             1" in text
        assert "legitimate:human_closer" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_a_mirror_that_says_completed_is_legitimate(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "done"), mirror_status="completed")

        text = _run()

        assert "законных (legitimate):             1" in text
        assert "legitimate:mirror_completed" in text

    def test_no_mirror_is_unprovable_not_false(self, tenant) -> None:
        """Нет доказательства ни в одну сторону — обнулять нельзя (DRF-2461)."""
        _stamped(tenant, _customer(tenant, "nomirror"), mirror_status=None)

        text = _run()

        assert "недоказуемых (unprovable):         1" in text
        assert "unprovable:no_mirror" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_two_mirrors_on_one_key_are_unprovable(self, tenant) -> None:
        customer = _customer(tenant, "ambig")
        booking = _stamped(tenant, customer, mirror_status="cancelled")
        visit_at = booking.visit_at
        assert visit_at is not None  # ключ сопоставления без времени не существует
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            start_at=visit_at,
            end_at=visit_at + dt.timedelta(minutes=60),
            status="confirmed",
            source=RemoteBookingProxy.Source.MOBILE_APP,
        )

        text = _run()

        assert "unprovable:ambiguous" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_an_unknown_canon_state_is_unprovable(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "future"), mirror_status="some_future_state")

        text = _run()

        assert "unprovable:mirror_some_future_state" in text


# ─── очередь ───────────────────────────────────────────────────────────────


class TestTheQueueIsNamed:
    def test_pending_events_for_false_rows_are_counted_and_warned_about(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "queued"), mirror_status="cancelled")
        for dispatched in (False, True):
            DomainEvent.objects.create(
                event_id=new_ulid(),
                event_name="booking.completed",
                event_version="1.0.0",
                occurred_at=timezone.now(),
                tenant=tenant,
                actor={"type": "system"},
                data={"booking_id": str(booking.pk)},
                is_dispatched=dispatched,
            )

        text = _run()

        assert "событий booking.completed:  2" in text
        assert "из них НЕ разобрано:        1" in text
        assert "необратимо" in text  # предупреждение названо

    def test_events_of_legitimate_rows_are_not_counted(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "ok"), mirror_status="completed")
        DomainEvent.objects.create(
            event_id=new_ulid(),
            event_name="booking.completed",
            event_version="1.0.0",
            occurred_at=timezone.now(),
            tenant=tenant,
            actor={"type": "system"},
            data={"booking_id": str(booking.pk)},
            is_dispatched=False,
        )

        text = _run()

        assert "событий booking.completed:  0" in text  # предмет — только ложные строки


# ─── ноль, охват и неприкосновенность ──────────────────────────────────────


class TestTheZeroAndTheReadOnlyPromise:
    def test_an_empty_scope_says_the_zeros_prove_nothing(self, tenant) -> None:
        text = _run()

        assert "охват (строк со штампом): 0" in text
        assert "нули ниже ничего не доказывают" in text

    def test_the_census_changes_nothing(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "untouched"), mirror_status="cancelled")
        before = (booking.completed_at, booking.completed_by, booking.status)
        # Строки фикстур эмитят свои события (сигналы моделей), поэтому предмет
        # проверки — НЕ «событий ноль», а «их число не изменилось этим прогоном».
        events_before = DomainEvent.objects.count()
        assert events_before >= 1  # положительно: счётчик живой, ему есть что мерить

        _run("--ids")

        booking.refresh_from_db()
        assert (booking.completed_at, booking.completed_by, booking.status) == before
        assert DomainEvent.objects.count() == events_before  # команда не эмитит
        assert not DomainEvent.objects.filter(
            event_name="booking.completed", data__booking_id=str(booking.pk)
        ).exists()

    def test_ids_are_printed_only_for_the_cleanup_bucket(self, tenant) -> None:
        false_row = _stamped(tenant, _customer(tenant, "f"), mirror_status="cancelled")
        legit_row = _stamped(tenant, _customer(tenant, "l"), mirror_status="completed")

        text = _run("--ids")

        assert str(false_row.pk) in text
        assert str(legit_row.pk) not in text

    def test_the_dry_run_says_it_changed_nothing(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "dry"), mirror_status="cancelled")

        text = _run()

        assert "сухой прогон: ничего не изменено" in text


# ─── --apply: снимает только доказанно ложное ──────────────────────────────


class TestApplyClearsOnlyTheProvenFalse:
    """Этап 7 промпта владельца: механизм подготовлен, запускает его владелец.

    Узлы держат то, чем такая коррекция опасна: снять законное, снять
    недоказуемое, тронуть чужое поле или чужую строку, тронуть очередь, потерять
    идемпотентность, и довериться устаревшему вердикту переписи.
    """

    def test_a_cancelled_stamp_is_cleared_and_logged_before_after(self, tenant, caplog) -> None:
        import logging

        booking = _stamped(tenant, _customer(tenant, "clear"), mirror_status="cancelled")
        was_at, was_by = booking.completed_at, booking.completed_by
        assert was_at is not None and was_by == "system"  # положительно: было что снимать

        logger_name = "apps.booking.management.commands.audit_false_completions"
        target = logging.getLogger(logger_name)
        target.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING, logger=logger_name):
                text = _run("--apply")
        finally:
            target.removeHandler(caplog.handler)

        booking.refresh_from_db()
        assert booking.completed_at is None
        assert booking.completed_by == ""
        assert "ЗАПИСЬ (--apply): кандидатов 1" in text  # число показано ДО записи
        assert "снято штампов: 1" in text
        assert "booking.false_completion.cleared" in caplog.text
        assert "before_completed_by='system'" in caplog.text

    def test_awaiting_payment_is_a_candidate_too(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "pay"), mirror_status="pending_payment")

        _run("--apply")

        booking.refresh_from_db()
        assert booking.completed_at is None

    @pytest.mark.parametrize(
        ("mirror_status", "completed_by"),
        (
            ("completed", "system"),
            ("cancelled", "master:42"),
            ("confirmed", "system"),
            (None, "system"),
        ),
    )
    def test_a_legitimate_or_unprovable_stamp_survives_apply(
        self, tenant, mirror_status: str | None, completed_by: str
    ) -> None:
        """Узел B промпта: законное завершение сохраняется. И недоказуемое тоже."""
        booking = _stamped(
            tenant,
            _customer(tenant, f"keep-{mirror_status}-{completed_by}"),
            mirror_status=mirror_status,
            completed_by=completed_by,
        )

        _run("--apply")

        booking.refresh_from_db()
        assert booking.completed_at is not None, "законное/недоказуемое завершение снято"
        assert booking.completed_by == completed_by

    def test_apply_touches_no_other_field_and_no_other_row(self, tenant) -> None:
        false_row = _stamped(tenant, _customer(tenant, "apply-f"), mirror_status="cancelled")
        other = _stamped(tenant, _customer(tenant, "apply-o"), mirror_status="completed")
        before_status, before_visit = false_row.status, false_row.visit_at

        _run("--apply")

        false_row.refresh_from_db()
        other.refresh_from_db()
        assert false_row.status == before_status  # статус не наш предмет
        assert false_row.visit_at == before_visit
        assert other.completed_at is not None  # соседняя строка не тронута

    def test_apply_dispatches_nothing_and_emits_nothing(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "queue"), mirror_status="cancelled")
        event = DomainEvent.objects.create(
            event_id=new_ulid(),
            event_name="booking.completed",
            event_version="1.0.0",
            occurred_at=timezone.now(),
            tenant=tenant,
            actor={"type": "system"},
            data={"booking_id": str(booking.pk)},
            is_dispatched=False,
        )
        events_before = DomainEvent.objects.count()

        _run("--apply")

        event.refresh_from_db()
        assert event.is_dispatched is False  # очередь не разобрана
        assert DomainEvent.objects.count() == events_before  # и ничего не добавлено

    def test_a_second_apply_is_a_no_op(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "idem"), mirror_status="cancelled")

        first = _run("--apply")
        second = _run("--apply")

        booking.refresh_from_db()
        assert "снято штампов: 1" in first
        assert "снято штампов: 0" in second  # область — строки со штампом, её больше нет
        assert booking.completed_at is None

    def test_a_row_that_became_legitimate_between_census_and_write_is_skipped(
        self, tenant, monkeypatch
    ) -> None:
        """Перепроверка под блокировкой: вердикт переписи не переживает изменение строки."""
        booking = _stamped(tenant, _customer(tenant, "race"), mirror_status="cancelled")
        from apps.booking.management.commands import audit_false_completions as mod

        real_classify = mod._classify
        calls = {"n": 0}

        def _flaky(row, canon=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_classify(row, canon)  # перепись: ложный
            return (mod.BUCKET_LEGITIMATE, "human_closer")  # к записи стал законным

        monkeypatch.setattr(mod, "_classify", _flaky)

        text = _run("--apply")

        booking.refresh_from_db()
        assert booking.completed_at is not None  # штамп сохранён
        assert "ПРОПУСК" in text
        assert "снято штампов: 0" in text
        assert "пропущено (строка изменилась): 1" in text


# ─── канон сильнее зеркала (DRF-2519) ──────────────────────────────────────


class TestTheCanonOverridesTheStaleMirror:
    """Замер 25.09: зеркало расходится с каноном на 5 строках из 10, и всегда так,
    что зеркало стоит на ``confirmed``, а канон ушёл дальше. У зеркала нет даже
    ``updated_at``: строка пишется один раз. Значит ``mirror_confirmed`` — не
    доказательство, а корзину надо считать по источнику.
    """

    def test_mirror_confirmed_is_unprovable_not_legitimate(self, tenant) -> None:
        """Имя корзины закреплено узлом.

        Прежний докстринг обещал ``legitimate``, код давал ``unprovable``, и ни один
        узел этого не ловил: оба исхода защищают строку, поэтому проверка следствия
        («штамп выжил») пропускала неверное имя. Теперь проверяется имя.
        """
        _stamped(tenant, _customer(tenant, "conf"), mirror_status="confirmed")

        text = _run()

        assert "unprovable:mirror_confirmed" in text
        assert "legitimate:mirror_confirmed" not in text
        assert "недоказуемых (unprovable):         1" in text

    def test_the_report_names_what_decided(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "decider"), mirror_status="confirmed")

        by_mirror = _run()

        assert "решает:  ЗЕРКАЛО" in by_mirror
        assert "DRF-2519" in by_mirror  # стухание названо в самом отчёте

    def test_canon_awaiting_payment_makes_a_mirror_confirmed_row_false(
        self, tenant, tmp_path
    ) -> None:
        """Живой случай: зеркало confirmed, канон awaiting_payment."""
        booking = _stamped(tenant, _customer(tenant, "unpaid"), mirror_status="confirmed")
        canon = tmp_path / "canon.txt"
        canon.write_text(f"# сверка\n{booking.pk} awaiting_payment\n", encoding="utf-8")

        text = _run("--canon-file", str(canon))

        assert "решает:  КАНОН" in text
        assert "false_by_canon:canon_awaiting_payment" in text
        assert "ложных по канону (false_by_canon): 1" in text

    def test_canon_completed_protects_a_row_the_mirror_calls_cancelled(
        self, tenant, tmp_path
    ) -> None:
        """Канон сильнее и когда он ЗАЩИЩАЕТ строку, а не только когда обвиняет."""
        booking = _stamped(tenant, _customer(tenant, "done-canon"), mirror_status="cancelled")
        canon = tmp_path / "canon.txt"
        canon.write_text(f"{booking.pk} completed\n", encoding="utf-8")

        text = _run("--canon-file", str(canon), "--apply")

        booking.refresh_from_db()
        assert "legitimate:canon_completed" in text
        assert booking.completed_at is not None  # снять законное канон не позволил
        assert "снято штампов: 0" in text

    def test_canon_confirmed_stays_unprovable(self, tenant, tmp_path) -> None:
        booking = _stamped(tenant, _customer(tenant, "canon-conf"), mirror_status="cancelled")
        canon = tmp_path / "canon.txt"
        canon.write_text(f"{booking.pk} confirmed\n", encoding="utf-8")

        text = _run("--canon-file", str(canon))

        assert "unprovable:canon_confirmed" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_apply_with_canon_clears_the_unpaid_row(self, tenant, tmp_path) -> None:
        booking = _stamped(tenant, _customer(tenant, "unpaid-apply"), mirror_status="confirmed")
        canon = tmp_path / "canon.txt"
        canon.write_text(f"{booking.pk} awaiting_payment\n", encoding="utf-8")

        text = _run("--canon-file", str(canon), "--apply")

        booking.refresh_from_db()
        assert booking.completed_at is None
        assert booking.completed_by == ""
        assert "снято штампов: 1" in text

    def test_a_row_absent_from_the_canon_file_falls_back_to_the_mirror(
        self, tenant, tmp_path
    ) -> None:
        """Отсутствие строки в сверке — не «канон подтвердил»."""
        listed = _stamped(tenant, _customer(tenant, "listed"), mirror_status="confirmed")
        _stamped(tenant, _customer(tenant, "missing"), mirror_status="cancelled")
        canon = tmp_path / "canon.txt"
        canon.write_text(f"{listed.pk} awaiting_payment\n", encoding="utf-8")

        text = _run("--canon-file", str(canon))

        assert "false_by_canon:canon_awaiting_payment" in text  # решил канон
        assert "false_by_canon:mirror_cancelled" in text  # решило зеркало
        assert "ложных по канону (false_by_canon): 2" in text

    @pytest.mark.parametrize(
        "content",
        (
            "не-два-поля\n",
            "id status лишнее\n",
            "# только комментарий\n",
            "",
        ),
    )
    def test_a_broken_or_empty_canon_file_is_refused_whole(
        self, tenant, tmp_path, content: str
    ) -> None:
        """Половина сверки хуже её отсутствия: она выглядит как сверка."""
        from django.core.management.base import CommandError

        _stamped(tenant, _customer(tenant, "broken"), mirror_status="cancelled")
        canon = tmp_path / "canon.txt"
        canon.write_text(content, encoding="utf-8")

        with pytest.raises(CommandError):
            _run("--canon-file", str(canon))

    def test_a_missing_canon_file_is_refused(self, tenant, tmp_path) -> None:
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            _run("--canon-file", str(tmp_path / "nope.txt"))


# ─── живой канон: --canon (DRF-2519) ───────────────────────────────────────


class TestTheLiveCanonIsTheStrongestEvidence:
    """Идентификатор зеркала не стухает, статус стухает — значит спрашиваем канон
    по ``appointment_id``. Вызов уже был в репозитории (``get_appointment_version``).
    """

    @staticmethod
    def _stub(monkeypatch, answers: dict[str, str] | None = None, raises: bool = False):
        """Подменить каноничный клиент: словарь «appointment_id → статус»."""
        from apps.integrations.ayla import booking_client as bc

        class _Stub:
            calls: list[tuple[str, str]] = []

            def get_appointment_version(self, *, external_user_id: str, booking_id: str):
                self.calls.append((external_user_id, booking_id))
                if raises:
                    raise bc.BookingUnavailableError("stub_outage")
                status = (answers or {}).get(booking_id, "confirmed")
                return bc.AylaAppointmentVersion(
                    id=booking_id, version=1, status=status, start_datetime="2026-09-25T06:00:00Z"
                )

        stub = _Stub()
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        return stub

    def test_canon_says_awaiting_payment_where_the_mirror_said_confirmed(
        self, tenant, monkeypatch
    ) -> None:
        customer = _customer(tenant, "live-unpaid")
        booking = _stamped(tenant, customer, mirror_status="confirmed")
        appointment_id = str(
            RemoteBookingProxy.all_tenants.filter(bot_user=customer)
            .values_list("appointment_id", flat=True)
            .first()
        )
        stub = self._stub(monkeypatch, {appointment_id: "awaiting_payment"})

        text = _run("--canon")

        assert "решает:  КАНОН (живой опрос" in text
        assert "false_by_canon:canon_awaiting_payment" in text
        assert stub.calls and stub.calls[0][1] == appointment_id  # спрошено по id зеркала
        booking.refresh_from_db()
        assert booking.completed_at is not None  # сухой прогон ничего не снял

    def test_canon_completed_keeps_the_stamp_even_with_a_cancelled_mirror(
        self, tenant, monkeypatch
    ) -> None:
        customer = _customer(tenant, "live-done")
        booking = _stamped(tenant, customer, mirror_status="cancelled")
        appointment_id = str(
            RemoteBookingProxy.all_tenants.filter(bot_user=customer)
            .values_list("appointment_id", flat=True)
            .first()
        )
        self._stub(monkeypatch, {appointment_id: "completed"})

        text = _run("--canon", "--apply")

        booking.refresh_from_db()
        assert "legitimate:canon_completed" in text
        assert booking.completed_at is not None  # канон защитил строку
        assert "снято штампов: 0" in text

    def test_an_unreachable_canon_is_unprovable_and_never_cleared(
        self, tenant, monkeypatch
    ) -> None:
        """«Не знаем» — не повод чистить, и зеркало вместо канона не подставляется."""
        booking = _stamped(tenant, _customer(tenant, "live-outage"), mirror_status="cancelled")
        self._stub(monkeypatch, raises=True)

        text = _run("--canon", "--apply")

        booking.refresh_from_db()
        assert "unprovable:canon_unavailable" in text
        assert "не ответил по 1" in text  # число названо в строке «решает»
        assert booking.completed_at is not None
        assert "снято штампов: 0" in text

    def test_the_live_canon_overrides_the_file_snapshot(
        self, tenant, monkeypatch, tmp_path
    ) -> None:
        customer = _customer(tenant, "live-over-file")
        booking = _stamped(tenant, customer, mirror_status="confirmed")
        appointment_id = str(
            RemoteBookingProxy.all_tenants.filter(bot_user=customer)
            .values_list("appointment_id", flat=True)
            .first()
        )
        canon_file = tmp_path / "canon.txt"
        canon_file.write_text(f"{booking.pk} completed\n", encoding="utf-8")
        self._stub(monkeypatch, {appointment_id: "cancelled"})

        text = _run("--canon", "--canon-file", str(canon_file))

        # Файл говорил completed, живой канон говорит cancelled — верх живого.
        assert "false_by_canon:canon_cancelled" in text
        assert "legitimate:canon_completed" not in text

    def test_a_row_with_two_mirrors_is_not_asked_about(self, tenant, monkeypatch) -> None:
        """Ключ неточен — канон не спрашивается, и строка остаётся недоказуемой."""
        customer = _customer(tenant, "live-ambig")
        booking = _stamped(tenant, customer, mirror_status="cancelled")
        visit_at = booking.visit_at
        assert visit_at is not None
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            start_at=visit_at,
            end_at=visit_at + dt.timedelta(minutes=60),
            status="confirmed",
            source=RemoteBookingProxy.Source.MOBILE_APP,
        )
        stub = self._stub(monkeypatch, {})

        text = _run("--canon")

        assert stub.calls == []  # по неточному ключу канон не спрашивают
        assert "unprovable:ambiguous" in text
