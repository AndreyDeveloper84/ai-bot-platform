"""DRF-1553 — верификация как сервис, а не тело действия админки.

Что здесь доказывается:

* :data:`AWAITING_VERIFICATION` — это ровно ``AVAILABLE`` с перевёрнутым
  приглашением, а не отдельный набор столбцов. Иначе кнопка на экране
  подключения считала бы в подписи мастеров, которых верификация
  бронируемыми не сделает, и её обещание «салон появится в поиске»
  оказалось бы враньём;
* сервис меняет ровно непринятые строки, пропускает принятые и пишет по
  строке журнала на каждое изменение — по факту, а не по вызову;
* сервис ходит через ``save``, а не ``update``: ``accepted_at`` штампует
  модель, и массовое обновление оставило бы принятую строку без штампа.

Действие ``verify_masters`` в админке каталога после выноса продолжает
работать — это держат тесты DRF-1496
(``test_master_admin_drf1496.py``), которые не менялись.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.catalog.master_state import AVAILABLE, AWAITING_VERIFICATION
from apps.catalog.models import CatalogMaster
from apps.catalog.services.verification import verify_masters
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_PENDING = CatalogMaster.InviteStatus.PENDING
_ACCEPTED = CatalogMaster.InviteStatus.ACCEPTED
_CANCELLED = CatalogMaster.InviteStatus.CANCELLED


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="drf1553-catalog", name="Салон DRF-1553", city="Москва")


@pytest.fixture
def operator():
    return get_user_model().objects.create_superuser(
        username="drf1553-operator",
        email="operator1553@example.com",
        password="x",  # pragma: allowlist secret
    )


def _master(salon: Tenant, name: str, **kwargs) -> CatalogMaster:
    defaults = {
        "tenant": salon,
        "external_id": None,
        "external_updated_at": timezone.now(),
        "name": name,
        "is_active": True,
        "ayla_user_id": uuid.uuid4(),
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(**defaults)


class TestAwaitingVerificationPredicate:
    """Предикат обещает бронируемость — и обязан её отдать."""

    def test_awaiting_verification_is_available_minus_the_invite(self, salon: Tenant) -> None:
        """На всех формах строки: попал в ожидание ⟺ после приёма продаётся.

        Матрица, а не один случай: разъезжается такой предикат ровно на
        краях — архив с непринятым приглашением, снятая активность,
        отсутствующий ``ayla_user_id``.
        """
        rows = {
            "синхронизированная, ждёт": _master(salon, "Ждёт", invite_status=_PENDING),
            "приглашение отозвано": _master(salon, "Отозвана", invite_status=_CANCELLED),
            "уже принята": _master(salon, "Принята", invite_status=_ACCEPTED),
            "в архиве": _master(
                salon, "В архиве", invite_status=_PENDING, archived_at=timezone.now()
            ),
            "снята с активности": _master(
                salon, "Неактивна", invite_status=_PENDING, is_active=False
            ),
            "не связана с Ayla": _master(
                salon, "Несвязанная", invite_status=_PENDING, ayla_user_id=None
            ),
        }

        awaiting = set(
            CatalogMaster.all_tenants.filter(AWAITING_VERIFICATION).values_list("pk", flat=True)
        )
        # Присутствие: выборка непуста — значит «не нашёл» ниже это форма
        # строки, а не сломанный запрос.
        assert awaiting, "предикат ожидания не нашёл ни одной строки"

        for label, master in rows.items():
            was_awaiting = master.pk in awaiting

            # Что обещает предикат: приём приглашения делает строку
            # бронируемой. Проверяем ровно это — приняв приглашение и
            # спросив тот же ``AVAILABLE``, что читает витрина.
            before = master.invite_status
            CatalogMaster.all_tenants.filter(pk=master.pk).update(invite_status=_ACCEPTED)
            becomes_bookable = CatalogMaster.all_tenants.filter(AVAILABLE, pk=master.pk).exists()
            CatalogMaster.all_tenants.filter(pk=master.pk).update(invite_status=before)

            if master.invite_status == _ACCEPTED:
                # Уже принятая в ожидании не числится по определению —
                # верифицировать в ней нечего.
                assert not was_awaiting, label
                continue
            assert was_awaiting == becomes_bookable, label


class TestVerificationService:
    def test_verifies_unaccepted_and_skips_accepted(self, salon: Tenant, operator) -> None:
        waiting = _master(salon, "Ждёт", invite_status=_PENDING)
        already = _master(salon, "Принята", invite_status=_ACCEPTED)

        outcome = verify_masters([waiting, already], user=operator)

        assert outcome.verified == 1
        assert outcome.skipped == 1
        assert outcome.total == 2
        waiting.refresh_from_db()
        already.refresh_from_db()
        assert waiting.invite_status == _ACCEPTED
        assert already.invite_status == _ACCEPTED

    def test_stamps_accepted_at_because_it_goes_through_save(self, salon: Tenant, operator) -> None:
        """``save``, а не ``update``: ``accepted_at`` ставит модель.

        Требование не косметическое: по докстрингу гейта
        ``accepted_at IS NULL`` у связанной принятой строки — сигнал,
        что состояние записали в обход модели. ``queryset.update()``
        оставил бы ровно такую строку, и разошлись бы не тесты, а
        диагностика.
        """
        bot_user = BotUser.all_tenants.create(
            tenant=salon,
            channel="max",
            channel_user_id="drf1553-max-user",
            display_name="Анна",
            chat_id="drf1553-max-user",
        )
        master = _master(salon, "Со штампом", invite_status=_PENDING, linked_bot_user=bot_user)
        stamp_before = CatalogMaster.all_tenants.values_list("accepted_at", flat=True).get(
            pk=master.pk
        )
        assert stamp_before is None

        verify_masters([master], user=operator)

        stamp_after = CatalogMaster.all_tenants.values_list("accepted_at", flat=True).get(
            pk=master.pk
        )
        # Штамп появился — значит запись шла через ``save`` модели.
        assert stamp_after is not None

    def test_writes_one_journal_line_per_changed_master(self, salon: Tenant, operator) -> None:
        waiting = _master(salon, "Ждёт", invite_status=_PENDING)
        already = _master(salon, "Принята", invite_status=_ACCEPTED)

        verify_masters([waiting, already], user=operator)

        changed_lines = LogEntry.objects.filter(object_id=str(waiting.pk))
        assert changed_lines.count() == 1
        entry = changed_lines.get()
        assert entry.user_id == operator.pk
        assert entry.action_flag == CHANGE
        assert "Верификация вручную" in entry.get_change_message()

        # Пропущенной строке журнал не пишется: изменения не было, и
        # запись о нём была бы ложным следом. Пара к утверждению выше —
        # тот же запрос, та же таблица, другая строка.
        skipped_lines = LogEntry.objects.filter(object_id=str(already.pk))
        assert not skipped_lines.exists()
