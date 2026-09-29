"""DRF-2309 — стирание идёт по актуальному ключу, а не по ключу прокси до привязки.

Бот хранит в ``BotUser.ayla_user_id`` ключ, которым каталог ответил на
``bot:max:N``: у непривязанного человека — ключ прокси. После привязки
(оператор или OTP в приложении) каталог отвечает ключом аккаунта, а заголовок
``bot:max:N`` разрешает в аккаунт — и запрос с ключом прокси в URL получает
403 (сторож — beautygo_backend, DRF-2309). Бот узнаёт новый ключ только при
следующем «зависимом действии» (DRF-1790), и до этого:

* «забудь всё» в чате слал C5.2 со старым ключом — отказ, каталожная половина
  не стёрта;
* задание повтора DRF-1950 хранило ключ со времени создания — каждая попытка
  403, выздоровления нет.

Правка: перед стиранием ключ прокси переспрашивается (``ensure_ayla_link``),
задание повтора перед попыткой следует перепривязке. Личность НЕ создаётся:
переспрос — только при уже существующем ключе прокси.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.identity.models import AylaErasureJob, BotUser
from apps.identity.services.personal_context import GateStatus, erase_declared_prefs
from apps.identity.services.tests.test_ayla_erasure_retry import CONFIRMED, _Ayla
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ingress_streams_empty")]

RESOLVE = "apps.integrations.ayla.identity_client.resolve_identity"
PROXY = uuid.UUID("00000000-0000-4000-8000-000000002309")
ACCOUNT = uuid.UUID("00000000-0000-4000-8000-00000000a309")


@pytest.fixture
def enabled(settings):
    settings.AYLA_ERASURE_RETRY_ENABLED = True
    return settings


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rebind-2309", name="Rebind 2309")


def _bot_user(tenant, *, key=PROXY, is_proxy=True, cid="230901") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=cid,
        chat_id=cid,
        ayla_user_id=key,
        ayla_user_id_is_proxy=is_proxy,
    )


def _addressed(ayla: _Ayla) -> set[str]:
    return {c[1] for c in ayla.calls}


class TestChatForgetFollowsTheBinding:
    def test_a_stale_proxy_key_is_re_asked_and_the_account_is_addressed(
        self, enabled, tenant
    ) -> None:
        bu = _bot_user(tenant)
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE, return_value=ResolvedIdentity(ayla_user_id=ACCOUNT, is_proxy=False)):
            result = erase_declared_prefs(bu, client=ayla, retry_source="chat_forget")  # type: ignore[arg-type]

        assert result.status is GateStatus.OK
        assert _addressed(ayla) == {str(ACCOUNT)}  # не ключ прокси
        bu.refresh_from_db()
        assert bu.ayla_user_id == ACCOUNT and bu.ayla_user_id_is_proxy is False

    def test_no_key_creates_no_identity(self, enabled, tenant) -> None:
        """«Забудь всё» у человека без ключа не создаёт личность в каталоге."""
        bu = _bot_user(tenant, key=None, is_proxy=None, cid="230902")
        ayla = _Ayla()
        with patch(RESOLVE) as resolve:
            result = erase_declared_prefs(bu, client=ayla, retry_source="chat_forget")  # type: ignore[arg-type]

        assert result.status is GateStatus.BLOCKED_CONSENT
        resolve.assert_not_called()
        assert ayla.calls == []  # empty-assert-ok: исход выше — не связан

    def test_a_real_key_is_used_without_asking(self, enabled, tenant) -> None:
        bu = _bot_user(tenant, key=ACCOUNT, is_proxy=False, cid="230903")
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE) as resolve:
            result = erase_declared_prefs(bu, client=ayla, retry_source="chat_forget")  # type: ignore[arg-type]

        assert result.status is GateStatus.OK
        resolve.assert_not_called()
        assert _addressed(ayla) == {str(ACCOUNT)}

    def test_an_unlinked_proxy_stays_itself(self, enabled, tenant) -> None:
        bu = _bot_user(tenant, cid="230904")
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE, return_value=ResolvedIdentity(ayla_user_id=PROXY, is_proxy=True)):
            erase_declared_prefs(bu, client=ayla, retry_source="chat_forget")  # type: ignore[arg-type]

        assert _addressed(ayla) == {str(PROXY)}

    def test_a_failed_re_ask_keeps_the_stored_key(self, enabled, tenant) -> None:
        """Сбой переспроса — прежнее поведение, не отказ стирать."""
        bu = _bot_user(tenant, cid="230905")
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE, side_effect=IdentityResolveError("network: ReadTimeout")):
            erase_declared_prefs(bu, client=ayla, retry_source="chat_forget")  # type: ignore[arg-type]

        assert _addressed(ayla) == {str(PROXY)}


def _job(bu: BotUser, *, key: uuid.UUID) -> AylaErasureJob:
    return AylaErasureJob.objects.create(
        bot_user=bu,
        ayla_user_id=key,
        external_user_id=f"bot:max:{bu.channel_user_id}",
        source="chat_forget",
        status=AylaErasureJob.Status.PENDING,
        attempts=1,
        next_attempt_at=timezone.now() - timedelta(seconds=1),
    )


class TestTheRetryJobFollowsTheBinding:
    def test_a_stale_job_recovers_on_the_next_attempt(self, enabled, tenant) -> None:
        """Главный узел: задание со старым ключом прокси выздоравливает."""
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        bu = _bot_user(tenant, cid="230911")
        job = _job(bu, key=PROXY)
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE, return_value=ResolvedIdentity(ayla_user_id=ACCOUNT, is_proxy=False)):
            summary = sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        job.refresh_from_db()
        assert summary["completed"] == 1
        assert job.status == AylaErasureJob.Status.COMPLETED
        assert job.ayla_user_id == ACCOUNT
        assert _addressed(ayla) == {str(ACCOUNT)}

    def test_a_shell_already_rebound_elsewhere_needs_no_network(self, enabled, tenant) -> None:
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        bu = _bot_user(tenant, key=ACCOUNT, is_proxy=False, cid="230912")
        job = _job(bu, key=PROXY)  # задание открыто до перепривязки
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE) as resolve:
            sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        resolve.assert_not_called()
        job.refresh_from_db()
        assert job.ayla_user_id == ACCOUNT
        assert _addressed(ayla) == {str(ACCOUNT)}

    def test_a_job_on_a_real_key_is_left_alone(self, enabled, tenant) -> None:
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        bu = _bot_user(tenant, key=ACCOUNT, is_proxy=False, cid="230913")
        job = _job(bu, key=ACCOUNT)
        ayla = _Ayla(statuses=[CONFIRMED])
        with patch(RESOLVE) as resolve:
            sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        resolve.assert_not_called()
        job.refresh_from_db()
        assert job.ayla_user_id == ACCOUNT
        assert job.status == AylaErasureJob.Status.COMPLETED

    def test_an_open_job_for_the_new_key_is_not_broken(self, enabled, tenant) -> None:
        """Предел: для нового ключа уже есть открытое задание — старое не
        перевешивается (одно открытое на ключ), обход не падает."""
        from apps.identity.services.ayla_erasure import sweep_due_jobs

        bu = _bot_user(tenant, key=ACCOUNT, is_proxy=False, cid="230914")
        stale = _job(bu, key=PROXY)
        fresh = _job(bu, key=ACCOUNT)
        ayla = _Ayla(statuses=[CONFIRMED])
        sweep_due_jobs(client=ayla)  # type: ignore[arg-type]

        stale.refresh_from_db()
        fresh.refresh_from_db()
        assert fresh.status == AylaErasureJob.Status.COMPLETED
        assert stale.ayla_user_id == PROXY
