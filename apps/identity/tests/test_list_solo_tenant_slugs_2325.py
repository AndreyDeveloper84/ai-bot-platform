"""`list_solo_tenant_slugs` — кого бот доказал соло-мастером (DRF-2325, №31).

Каталог сам происхождение не знает: у него для тенантов до G4 стоит
`kind=salon` по умолчанию миграции 0007. Доказательство даёт бот — и
доказательство это **пересчёт**, а не вид слага: G4 прямо сказал, что имя
производной происхождения не доказывает. Команда берёт личность владельца
тенанта (`channel`, `channel_user_id`), считает
`solo_onboarding._solo_tenant_slug` и печатает слаг, только если он сошёлся
с настоящим слагом тенанта.

Связь бота и каталога здесь — слаг: до G4 каталог заводил строку через
`ensure_tenant(slug=…)` со своим UUID, общего ключа нет.

* a1 — сошёлся пересчёт → слаг в списке;
* a2 — салон (слаг не сходится) → не в списке, но посчитан;
* a3 — слаг ВЫГЛЯДИТ соло, а личность владельца другая → не в списке:
  префикс ничего не доказывает;
* a4 — тенант без владельца → пропущен и назван числом;
* a5 — в stdout только слаги (годятся прямо в канал каталожной команде),
  отчёт — в stderr, и в нём нет ни имён, ни логинов, ни идентификаторов;
* a6 — неактивный тенант не попадает в список, но назван в отчёте;
* a7 — команда не пишет: снимок базы до и после совпадает.
"""
from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.management import call_command

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


def _identity() -> str:
    return f"id-{uuid.uuid4().hex[:8]}"


def _tenant_with_owner(
    slug: str,
    *,
    channel_user_id: str,
    channel: str = CHANNEL,
    is_active: bool = True,
) -> Tenant:
    tenant = Tenant.all_objects.create(
        slug=slug, name=f"Мастерская {slug}", is_active=is_active
    )
    owner = BotUser.all_tenants.create(
        tenant=tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        display_name="Ольга Иванова",
    )
    TenantStaff.all_tenants.create(
        tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER
    )
    return tenant


def _solo_tenant(**kw) -> tuple[Tenant, str]:
    """Тенант, заведённый как соло: слаг посчитан по личности владельца."""
    channel_user_id = kw.pop("channel_user_id", None) or _identity()
    slug = _solo_tenant_slug(CHANNEL, channel_user_id)
    return _tenant_with_owner(slug, channel_user_id=channel_user_id, **kw), channel_user_id


class TestA1ProvenSoloIsListed:
    def test_a_recomputed_slug_that_matches_is_printed(self) -> None:
        tenant, _ = _solo_tenant()

        out, _err = _run()

        assert _slugs(out) == [tenant.slug]


class TestA2SalonIsNotListed:
    def test_a_salon_tenant_is_counted_but_not_listed(self) -> None:
        solo, _ = _solo_tenant()
        _tenant_with_owner("krasota-na-lenina", channel_user_id=_identity())

        out, err = _run()

        assert solo.slug in _slugs(out)  # наличие: доказанный в списке есть
        assert "krasota-na-lenina" not in out
        assert "не сошёлся" in err


class TestA3PrefixIsNotProof:
    def test_a_solo_looking_slug_with_another_identity_is_refused(self) -> None:
        """Слаг чужой личности: выглядит как соло, пересчёт не сходится."""
        stranger = _solo_tenant_slug(CHANNEL, _identity())
        _tenant_with_owner(stranger, channel_user_id=_identity())

        out, _err = _run()

        assert _slugs(out) == []


class TestA4TenantWithoutOwner:
    def test_it_is_skipped_and_named_by_count(self) -> None:
        solo, _ = _solo_tenant()
        Tenant.all_objects.create(slug="bez-vladelca", name="Без владельца")

        out, err = _run()

        assert _slugs(out) == [solo.slug]
        assert "без владельца: 1" in err


class TestA5TheListIsPipeableAndCarriesNoPeople:
    def test_stdout_is_only_slugs_and_the_report_goes_to_stderr(self) -> None:
        tenant, channel_user_id = _solo_tenant()

        out, err = _run()

        assert _slugs(out) == [tenant.slug]  # наличие: слаг напечатан
        assert "Ольга" not in out and "Ольга" not in err
        assert channel_user_id not in out and channel_user_id not in err
        assert str(tenant.id) not in out and str(tenant.id) not in err
        assert "доказано пересчётом: 1" in err


class TestA6InactiveTenantsAreNotListed:
    def test_an_inactive_tenant_is_named_but_not_listed(self) -> None:
        solo, _ = _solo_tenant()
        sleeping, _ = _solo_tenant(is_active=False)

        out, err = _run()

        assert _slugs(out) == [solo.slug]
        assert sleeping.slug not in out
        assert "неактивные, не в списке: 1" in err


class TestA7TheCommandDoesNotWrite:
    def test_the_snapshot_is_the_same_before_and_after(self) -> None:
        tenant, _ = _solo_tenant()
        before = (
            Tenant.all_objects.count(),
            BotUser.all_tenants.count(),
            TenantStaff.all_tenants.count(),
            Tenant.all_objects.get(pk=tenant.pk).slug,
        )

        _run()

        assert (
            Tenant.all_objects.count(),
            BotUser.all_tenants.count(),
            TenantStaff.all_tenants.count(),
            Tenant.all_objects.get(pk=tenant.pk).slug,
        ) == before
