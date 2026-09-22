"""`list_solo_tenant_slugs` — кого бот доказал соло-мастером (DRF-2325, №31).

Каталог сам происхождение не знает: у него для тенантов до G4 стоит
`kind=salon` по умолчанию миграции 0007. Доказательство даёт бот — и
доказательство это **пересчёт**, а не вид слага: G4 прямо сказал, что имя
производной происхождения не доказывает. Команда берёт личности владельцев
тенанта (`channel`, `channel_user_id`), считает
`solo_onboarding._solo_tenant_slug` и печатает слаг, только если он сошёлся
с настоящим слагом тенанта.

Связь бота и каталога здесь — слаг: до G4 каталог заводил строку через
`ensure_tenant(slug=…)` со своим UUID, общего ключа нет.

* a1 — сошёлся пересчёт → слаг в списке;
* a2 — салон (слаг не сходится) → не в списке, и счётчик вырос;
* a3 — слаг ВЫГЛЯДИТ соло, а личность владельца другая → не в списке, но
  назван диагностической строкой: так видна системная поломка пересчёта;
* a4 — тенант без владельца → пропущен и назван числом;
* a5 — в stdout только слаги (годятся прямо в канал каталожной команде),
  отчёт — в stderr, и в нём нет ни имён, ни логинов, ни идентификаторов;
* a6 — неактивный тенант не попадает в список, но назван в отчёте;
* a7 — команда не пишет: ни одного INSERT/UPDATE/DELETE за прогон;
* a8 — владельца передавали (снятая строка + действующая) → тенант всё
  равно доказан: `deactivated_at` — мягкая деактивация, и требование ровно
  одной строки выкинуло бы доказанный тенант в «без владельца».

Числа берутся приростом либо ключом: в базе есть служебные тенанты
окружения (миграция `0014_seed_global_bot_tenant` заводит тенанта без
владельца), и абсолютные числа мерили бы их, а не поведение команды.
"""

from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import _solo_tenant_slug
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL = "max"


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command("list_solo_tenant_slugs", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _slugs(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _count(err: str, key: str) -> int:
    """Число из строки отчёта «ключ: N»."""
    for line in err.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == key:
            return int(value.strip())
    raise AssertionError(f"в отчёте нет строки {key!r}: {err!r}")


def _identity() -> str:
    return f"id-{uuid.uuid4().hex[:8]}"


def _owner(tenant: Tenant, channel_user_id: str, *, channel: str = CHANNEL, revoked: bool = False):
    # get_or_create: BotUser уникален по (тенант, канал, id в канале), и
    # передача владения той же личности переиспользует ту же строку.
    bot_user, _ = BotUser.all_tenants.get_or_create(
        tenant=tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        defaults={"display_name": "Ольга Иванова"},
    )
    return TenantStaff.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        role=TenantStaff.Role.OWNER,
        deactivated_at=timezone.now() if revoked else None,
    )


def _tenant_with_owner(
    slug: str,
    *,
    channel_user_id: str,
    channel: str = CHANNEL,
    is_active: bool = True,
) -> Tenant:
    tenant = Tenant.all_objects.create(slug=slug, name=f"Мастерская {slug}", is_active=is_active)
    _owner(tenant, channel_user_id, channel=channel)
    return tenant


def _solo_tenant(**kw) -> tuple[Tenant, str]:
    """Тенант, заведённый как соло: слаг посчитан по личности владельца."""
    channel_user_id = kw.pop("channel_user_id", None) or _identity()
    slug = _solo_tenant_slug(CHANNEL, channel_user_id)
    return _tenant_with_owner(slug, channel_user_id=channel_user_id, **kw), channel_user_id


class TestA1ProvenSoloIsListed:
    def test_a_recomputed_slug_that_matches_is_printed(self) -> None:
        tenant, _ = _solo_tenant()

        out, err = _run()

        assert _slugs(out) == [tenant.slug]
        assert _count(err, "доказано пересчётом") == 1


class TestA2SalonIsNotListed:
    def test_a_salon_tenant_is_counted_but_not_listed(self) -> None:
        solo, _ = _solo_tenant()
        _out, before = _run()

        _tenant_with_owner("krasota-na-lenina", channel_user_id=_identity())
        out, err = _run()

        assert solo.slug in _slugs(out)  # наличие: доказанный в списке есть
        assert "krasota-na-lenina" not in out
        assert _count(err, "пересчёт не сошёлся (не соло)") == (
            _count(before, "пересчёт не сошёлся (не соло)") + 1
        )


class TestA3PrefixIsNotProof:
    def test_a_solo_looking_slug_with_another_identity_is_refused(self) -> None:
        """Слаг чужой личности: выглядит как соло, пересчёт не сходится."""
        proven, _ = _solo_tenant()
        stranger = _solo_tenant_slug(CHANNEL, _identity())
        _tenant_with_owner(stranger, channel_user_id=_identity())

        out, err = _run()

        assert proven.slug in _slugs(out)  # наличие: доказанный печатается
        assert stranger not in out
        # Диагностика: отказ слагу соло-вида виден отдельным числом, иначе
        # системная поломка пересчёта пряталась бы за «не соло».
        assert _count(err, "из них слаги соло-вида (ожидается 0)") == 1


class TestA4TenantWithoutOwner:
    def test_it_is_skipped_and_named_by_count(self) -> None:
        solo, _ = _solo_tenant()
        _out, before = _run()

        Tenant.all_objects.create(slug="bez-vladelca", name="Без владельца")
        out, err = _run()

        assert _slugs(out) == [solo.slug]
        assert _count(err, "без владельца") == _count(before, "без владельца") + 1


class TestA5TheListIsPipeableAndCarriesNoPeople:
    def test_stdout_is_only_slugs_and_the_report_goes_to_stderr(self) -> None:
        tenant, channel_user_id = _solo_tenant()

        out, err = _run()

        assert _slugs(out) == [tenant.slug]  # наличие: слаг напечатан
        assert "Ольга" not in out and "Ольга" not in err
        assert channel_user_id not in out and channel_user_id not in err
        assert str(tenant.id) not in out and str(tenant.id) not in err
        assert _count(err, "доказано пересчётом") == 1


class TestA6InactiveTenantsAreNotListed:
    def test_an_inactive_tenant_is_named_but_not_listed(self) -> None:
        solo, _ = _solo_tenant()
        sleeping, _ = _solo_tenant(is_active=False)

        out, err = _run()

        assert _slugs(out) == [solo.slug]
        assert sleeping.slug not in out
        assert _count(err, "неактивные доказанные, не в списке") == 1


class TestA7TheCommandDoesNotWrite:
    def test_not_a_single_write_is_issued(self) -> None:
        _solo_tenant()

        with CaptureQueriesContext(connection) as queries:
            _run()

        writes = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert [q["sql"] for q in queries.captured_queries]  # наличие: запросы были
        assert writes == []


class TestA8OwnerHandoverStillProves:
    def test_a_revoked_owner_row_beside_a_live_one_does_not_drop_the_tenant(self) -> None:
        """`deactivated_at` — мягкая деактивация, и передача владения
        оставляет две строки: требование ровно одной выкинуло бы доказанный
        тенант в «без владельца», то есть назвало бы его неправдой."""
        channel_user_id = _identity()
        tenant, _ = _solo_tenant(channel_user_id=channel_user_id)
        _owner(tenant, channel_user_id, revoked=True)

        out, err = _run()

        assert _slugs(out) == [tenant.slug]
        assert _count(err, "доказано пересчётом") == 1

    def test_an_owner_whose_row_was_revoked_still_proves_origin(self) -> None:
        """Слаг посчитан при заведении; снятие роли этого не отменяет."""
        channel_user_id = _identity()
        slug = _solo_tenant_slug(CHANNEL, channel_user_id)
        tenant = Tenant.all_objects.create(slug=slug, name="Мастерская")
        _owner(tenant, channel_user_id, revoked=True)

        out, _err = _run()

        assert _slugs(out) == [slug]

    def test_a_second_owner_identity_does_not_hide_the_proven_one(self) -> None:
        channel_user_id = _identity()
        tenant, _ = _solo_tenant(channel_user_id=channel_user_id)
        _owner(tenant, _identity(), revoked=True)

        out, _err = _run()

        assert _slugs(out) == [tenant.slug]
