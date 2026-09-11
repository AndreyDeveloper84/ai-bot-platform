"""Integration tests for /api/v1/master/onboarding/{claim,accept,reject}."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.auth import decode_master_session_token
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant


def _post_json(client: Client, name: str, *, body: dict, header: str | None):
    if header:
        return client.post(
            reverse(name),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )
    return client.post(
        reverse(name),
        data=json.dumps(body),
        content_type="application/json",
    )


# --- /onboarding/claim ----------------------------------------------------


class TestOnboardingClaim:
    def test_happy_path_returns_profile(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master"]["id"] == str(pending_master.id)
        assert data["master"]["name"] == pending_master.name
        assert data["salon"]["tenant_id"] == str(pending_master.tenant_id)
        assert data["max_user"]["phone_masked"].endswith("67")  # last two digits of fixture phone

    def test_idempotent(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        body = {"token": str(pending_master.invite_token)}
        resp1 = _post_json(
            client, "master_api:onboarding_claim", body=body, header=init_data_header("12345")
        )
        resp2 = _post_json(
            client, "master_api:onboarding_claim", body=body, header=init_data_header("12345")
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        pending_master.refresh_from_db()
        # State unchanged — claim is read-only.
        assert pending_master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert pending_master.invite_token is not None

    def test_invalid_token(self, client: Client, bot_user: BotUser) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(uuid.uuid4())},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "invalid_invite_token"

    def test_expired_token(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(tenant, expires_in_days=-1)
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 410
        assert resp.json()["error"] == "invite_expired"

    def test_already_accepted(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=uuid.uuid4(),
        )
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invite_already_used"

    def test_cross_tenant_404(
        self,
        client: Client,
        bot_user: BotUser,
        other_tenant: Tenant,
    ) -> None:
        """Token belongs to a different tenant — must NOT leak existence."""

        foreign = make_master(other_tenant)
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(foreign.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "invalid_invite_token"

    def test_missing_token_field(self, client: Client, bot_user: BotUser) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_started",
            target_id=pending_master.id,
        ).first()
        assert row is not None
        assert row.payload["bot_user_id"] == str(bot_user.id)


# --- /onboarding/accept ---------------------------------------------------


class TestOnboardingAccept:
    def test_happy_path_links_bot_user_and_returns_session(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master_id"] == str(pending_master.id)
        assert data["session_token"]
        assert data["expires_at"]

        # State mutated.
        pending_master.refresh_from_db()
        assert pending_master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert pending_master.linked_bot_user_id == bot_user.id
        assert pending_master.invite_token is None
        assert pending_master.mode == CatalogMaster.Mode.INVITE

        # Session token round-trips.
        payload = decode_master_session_token(data["session_token"])
        assert payload.master_id == pending_master.id
        assert payload.bot_user_id == bot_user.id

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_accepted",
            target_id=pending_master.id,
        ).first()
        assert row is not None

    def test_idempotent_second_accept(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        """Spec: second accept from the SAME BotUser returns same shape."""

        token = str(pending_master.invite_token)
        resp1 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )
        # Same token won't validate anymore (cleared), but bot_user is
        # linked → idempotency path returns 200.
        resp2 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200, resp2.content
        assert resp1.json()["master_id"] == resp2.json()["master_id"]

    def test_inactive_invited_master_becomes_active(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """DRF-1080 — production rows arrive at ``is_active=False``.

        ``master_invite_create`` writes the row inactive on purpose
        (apps/admin_api/views_invite.py:499) and nothing flipped it back,
        so the accepted master hit 403 ``master_inactive`` on every
        master endpoint. The fixture default is ``is_active=True``, which
        is exactly why the suite never saw this.
        """

        master = make_master(tenant, is_active=False)
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content

        master.refresh_from_db()
        assert master.is_active is True
        assert master.linked_bot_user_id == bot_user.id

    def test_gate_lets_the_freshly_accepted_master_in(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """The point of the fix: the next master call is no longer 403.

        Asserting on the column alone would keep passing if the gate
        ever grew a second condition, so the test walks the actual door
        the invited master walks through.
        """

        master = make_master(tenant, is_active=False)
        accept = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert accept.status_code == 200, accept.content

        me = client.get(
            reverse("master_api:me"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert me.status_code == 200, me.content

    def test_archived_master_is_not_reactivated(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Deactivation writes ``is_active=False`` AND ``archived_at``.

        Flipping the flag back for an archived row would return a master
        the salon took out of service to ``_MasterManager.bookable()``.
        The accept still succeeds — the token was valid — but the row
        stays inactive and the gate keeps answering 403.
        """

        now = datetime.now(tz=dt_timezone.utc)
        master = make_master(tenant, is_active=False, archived_at=now)
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content

        master.refresh_from_db()
        assert master.is_active is False
        assert master.archived_at is not None

        me = client.get(
            reverse("master_api:me"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert me.status_code == 403
        assert me.json()["error"] == "master_inactive"

    def test_retry_repairs_a_master_stuck_from_before_the_fix(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Anyone who accepted before this fix has no second first-accept.

        Their token is consumed and the row is already ACCEPTED, so the
        idempotency branch is the only one they can ever reach again.
        Without the repair there they stay at 403 for good.
        """

        master = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_user,
            is_active=False,
        )
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(uuid.uuid4())},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content

        master.refresh_from_db()
        assert master.is_active is True

    def test_wrong_recipient_blocked(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        other_bot_user: BotUser,
    ) -> None:
        """Master already linked to OTHER bot_user → 403 wrong_recipient."""

        master = make_master(
            tenant,
            linked_bot_user=other_bot_user,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),  # bot_user (not other_bot_user)
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "wrong_recipient"

    def test_foreign_token_from_a_linked_user_is_refused(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        """DRF-1507 — приём смотрит на предъявленный токен, а не только на связку.

        Человек уже привязан к строке А. Он предъявляет валидный токен
        строки Б того же салона. До правки это отвечало 200 и выдавало
        сессию А, а строка Б оставалась PENDING навсегда — фантом в ростере,
        о котором не узнавал ни он, ни владелец салона.
        """

        mine = make_master(
            tenant,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            linked_bot_user=bot_user,
        )
        hers = make_master(tenant, name="Ирина Смирнова")

        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(hers.invite_token)},
            header=init_data_header("12345"),
        )

        assert resp.status_code == 403, resp.content
        assert resp.json()["error"] == "wrong_recipient"
        hers.refresh_from_db()
        assert hers.invite_status == CatalogMaster.InviteStatus.PENDING
        assert hers.invite_token is not None
        assert hers.linked_bot_user_id is None
        mine.refresh_from_db()
        assert mine.linked_bot_user_id == bot_user.id

    def test_own_token_stays_idempotent_on_the_same_data(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        """Положительная стража к предыдущему на тех же данных.

        Тот же связанный человек, тот же салон, в котором лежит чужое
        приглашение, — но предъявлен собственный токен. Ответ прежний: 200 и
        своя сессия. Без этой пары отрицание выше проходило бы и от кода,
        который просто перестал принимать что бы то ни было.
        """

        mine = make_master(tenant, name="Анна Петрова")
        make_master(tenant, name="Ирина Смирнова")
        token = str(mine.invite_token)

        first = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )
        retry = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )

        assert first.status_code == 200, first.content
        assert retry.status_code == 200, retry.content
        assert first.json()["master_id"] == retry.json()["master_id"] == str(mine.id)

    def test_expired_token(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(tenant, expires_in_days=-1)
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 410


# --- /onboarding/accept — склейка со строкой синхронизации (DRF-1507) -----


def _synced_master(
    tenant: Tenant,
    *,
    ayla_user_id: uuid.UUID,
    external_id: int,
    name: str = "Анна Петрова",
) -> CatalogMaster:
    """Строка, какой её оставляет синхронизация каталога.

    Ключевые отличия от инвайт-строки: ``ayla_user_id`` заполнен,
    ``max_handle`` пуст, ``linked_bot_user`` пуст, ``invite_status``
    по умолчанию ACCEPTED (синхронизированный мастер обязан быть
    записываемым — см. ``CatalogMaster.invite_status.help_text``).
    """

    now = datetime.now(tz=dt_timezone.utc)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        specialization="Маникюр",
        is_active=True,
        ayla_user_id=ayla_user_id,
        max_handle="",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invite_token=None,
    )


class TestOnboardingAcceptGlue:
    """DRF-1507, пункт 4 — приземление склеивается, а не создаёт конфликт.

    Почему эти тесты существуют. DRF-1510 привела пять салонов
    синхронизацией: их мастера уже лежат строками с заполненным
    ``ayla_user_id``. PR #1401 поставил на этот столбец частичное
    ограничение ``uq_catalog_master_tenant_ayla_user_id``. Приём
    приглашения, который просто записывает ``ayla_user_id`` в СВОЮ
    инвайт-строку, на таких данных даёт **500 вместо приземления** — и
    только на них: на чистой локальной базе второй строки нет и всё
    зелено. Поэтому проверка ставит обе строки явно.
    """

    def test_lands_on_the_existing_synced_row(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id = ayla_user_id
        # DRF-1649: сорт объявлен явно — эти сценарии про человека со
        # СВЯЗАННЫМ НАСТОЯЩИМ аккаунтом. Без объявления столбец остаётся
        # NULL, потребитель честно отказывается, и тест проверял бы отказ
        # по неизвестности вместо того, ради чего написан.
        bot_user.ayla_user_id_is_proxy = False
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        synced = _synced_master(tenant, ayla_user_id=ayla_user_id, external_id=7)
        invited = make_master(tenant, external_id=1_000_000)

        before = CatalogMaster.all_tenants.filter(tenant=tenant).count()
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(invited.invite_token)},
            header=init_data_header("12345"),
        )

        # Не 500 — приземление состоялось.
        assert resp.status_code == 200, resp.content
        # И состоялось В СУЩЕСТВУЮЩУЮ строку, а не в инвайт-строку.
        assert resp.json()["master_id"] == str(synced.id)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == before

        synced.refresh_from_db()
        assert synced.linked_bot_user_id == bot_user.id
        assert synced.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert synced.accepted_at is not None
        # ``max_handle`` синхронизация не пишет — приземление доносит его.
        assert synced.max_handle == invited.max_handle

        # Инвайт-строка погашена, а не осиротена: PENDING навсегда — это
        # фантом в ростере (разрыв Р5).
        invited.refresh_from_db()
        assert invited.invite_status == CatalogMaster.InviteStatus.CANCELLED
        assert invited.invite_token is None
        assert invited.raw["superseded_by_master_id"] == str(synced.id)
        assert invited.linked_bot_user_id is None

        # Сессия выписана на ту строку, в которую приземлились.
        payload = decode_master_session_token(resp.json()["session_token"])
        assert payload.master_id == synced.id

    def test_stranger_still_lands_in_their_own_new_row(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Положительная стража (DRF-1411).

        Мастера, которого в салоне не было, склеивать не с чем — он обязан
        приземлиться в свою инвайт-строку. Без этой проверки «склейка»
        могла бы означать «все приземляются в первую попавшуюся строку».
        """

        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id_is_proxy = False  # DRF-1649, связанный настоящий
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        # Строка ДРУГОГО человека того же салона — склейка не должна её взять.
        other = _synced_master(
            tenant,
            ayla_user_id=uuid.uuid4(),
            external_id=7,
            name="Мария Иванова",
        )
        invited = make_master(tenant, external_id=1_000_000)

        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(invited.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["master_id"] == str(invited.id)

        invited.refresh_from_db()
        assert invited.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert invited.linked_bot_user_id == bot_user.id
        # И столбец заполнен — ровно то, чего пункт 4 требует.
        assert str(invited.ayla_user_id) == str(bot_user.ayla_user_id)

        other.refresh_from_db()
        assert other.linked_bot_user_id is None

    def test_two_people_in_one_salon_keep_two_rows(
        self,
        client: Client,
        bot_user: BotUser,
        other_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Положительная стража: разные люди — разные строки."""

        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id_is_proxy = False  # DRF-1649, связанный настоящий
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        other_bot_user.ayla_user_id = uuid.uuid4()
        other_bot_user.ayla_user_id_is_proxy = False
        other_bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])

        first = make_master(tenant, external_id=1_000_000)
        second = make_master(
            tenant,
            name="Мария Иванова",
            external_id=1_000_001,
        )
        second.max_handle = "@maria_nails"
        second.save(update_fields=["max_handle"])

        resp1 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(first.invite_token)},
            header=init_data_header("12345"),
        )
        resp2 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(second.invite_token)},
            header=init_data_header("99999", first_name="Мария"),
        )
        assert resp1.status_code == 200, resp1.content
        assert resp2.status_code == 200, resp2.content
        assert resp1.json()["master_id"] != resp2.json()["master_id"]
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_no_ayla_bridge_yet_lands_normally(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """``BotUser.ayla_user_id`` пуст — склеивать не по чему, и это норма.

        Мост с Ayla ставят два писателя — ``ayla_link.py`` и
        ``apps/identity/services/resolver.py:192`` (DRF-1649 поправила и
        докстринг ``_glue_target``, который утверждал, что писатель один).
        Пока он пуст, приземление обязано работать как раньше и НЕ писать
        в столбец ничего.
        """

        assert bot_user.ayla_user_id is None
        _synced_master(tenant, ayla_user_id=uuid.uuid4(), external_id=7)
        invited = make_master(tenant, external_id=1_000_000)

        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(invited.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["master_id"] == str(invited.id)
        invited.refresh_from_db()
        assert invited.ayla_user_id is None

    def test_glue_row_linked_to_someone_else_is_refused(
        self,
        client: Client,
        bot_user: BotUser,
        other_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Строку, уже связанную с другим MAX-аккаунтом, склейка не забирает."""

        ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id = ayla_user_id
        # DRF-1649: сорт объявлен явно — эти сценарии про человека со
        # СВЯЗАННЫМ НАСТОЯЩИМ аккаунтом. Без объявления столбец остаётся
        # NULL, потребитель честно отказывается, и тест проверял бы отказ
        # по неизвестности вместо того, ради чего написан.
        bot_user.ayla_user_id_is_proxy = False
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        taken = _synced_master(tenant, ayla_user_id=ayla_user_id, external_id=7)
        taken.linked_bot_user = other_bot_user
        taken.save(update_fields=["linked_bot_user"])
        invited = make_master(tenant, external_id=1_000_000)

        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(invited.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "wrong_recipient"
        invited.refresh_from_db()
        assert invited.invite_status == CatalogMaster.InviteStatus.PENDING


class TestProxyKeyNeverReachesCatalogMaster:
    """DRF-1649 — ключ прокси-аккаунта не едет в ``CatalogMaster``.

    ``BotUser.ayla_user_id`` может держать **изолированный прокси** Ayla, и
    на ``BotUser`` это верное значение: бронированию он нужен, и
    ``ensure_ayla_link`` права, что его пишет. Слоем выше он запрещён —
    ``apps/catalog/master_state.py:464-471``: он «занял бы ключ значением,
    по которому совпадения не будет никогда».

    Проверяется НЕ «сработала ли проверка», а **что ключ не записан**:
    сторож, утверждающий срабатывание, зеленел бы и на коде, который
    просто перестал писать всегда. Поэтому рядом стоит положительный
    контроль на ``is_proxy=False``.

    Каналов два и затвор один: ``person_ayla_user_id`` питает и поиск
    склейки (``views.py:669``), и запись (``views.py:715``). Оба здесь и
    проверены — чинить половину механизма значит перенести дефект, а не
    убрать.
    """

    def _accept(self, client: Client, invited: CatalogMaster):
        return _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(invited.invite_token)},
            header=init_data_header("12345"),
        )

    def test_proxy_key_is_not_written_into_the_row(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """``is_proxy=True`` — приземление проходит, столбец остаётся пуст."""

        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id_is_proxy = True
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        invited = make_master(tenant, external_id=1_000_000)

        resp = self._accept(client, invited)

        # Отказ касается ТОЛЬКО ключа: человек обязан приземлиться.
        # Пустой ``CatalogMaster.ayla_user_id`` — это названное состояние
        # (``ayla_unlinked``), а не поломка.
        assert resp.status_code == 200, resp.content
        invited.refresh_from_db()
        assert invited.linked_bot_user_id == bot_user.id
        assert invited.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        # И вот предмет: ключ прокси не записан.
        assert invited.ayla_user_id is None

    def test_real_key_still_is_written(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Положительный контроль: ``is_proxy=False`` записывается.

        Без него предыдущий тест зеленел бы на коде, который не пишет
        ``ayla_user_id`` никогда — то есть на сломанном.
        """

        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.ayla_user_id_is_proxy = False
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        invited = make_master(tenant, external_id=1_000_000)

        resp = self._accept(client, invited)

        assert resp.status_code == 200, resp.content
        invited.refresh_from_db()
        assert invited.ayla_user_id is not None
        assert str(invited.ayla_user_id) == str(bot_user.ayla_user_id)

    def test_unknown_sort_is_refused_exactly_like_a_proxy(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """``NULL`` — тоже отказ. «Не знаем» не есть «можно».

        Так выглядят строки, связанные ДО появления столбца, и всё, что
        пишет ``resolver.py:192``: он получает идентификатор от
        вызывающего и сорта знать не может. Мягкое чтение ``NULL``
        обратило бы столбец в свою противоположность — дало бы разрешение
        ровно там, где до него был честный пробел.
        """

        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.save(update_fields=["ayla_user_id"])
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is not None
        assert bot_user.ayla_user_id_is_proxy is None

        invited = make_master(tenant, external_id=1_000_000)
        resp = self._accept(client, invited)

        assert resp.status_code == 200, resp.content
        invited.refresh_from_db()
        assert invited.linked_bot_user_id == bot_user.id
        assert invited.ayla_user_id is None

    def test_proxy_key_does_not_glue_onto_a_synced_row(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Второй канал: по прокси-ключу склейка не ищется.

        Строка синхронизации с ТЕМ ЖЕ значением в ``ayla_user_id`` стоит
        рядом — и если бы поиск шёл по прокси, человек приземлился бы в
        неё. Чинить только запись значило бы оставить ту же ошибку вторым
        входом.
        """

        shared = uuid.uuid4()
        bot_user.ayla_user_id = shared
        bot_user.ayla_user_id_is_proxy = True
        bot_user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        synced = _synced_master(tenant, ayla_user_id=shared, external_id=7)
        invited = make_master(tenant, external_id=1_000_000)

        resp = self._accept(client, invited)

        assert resp.status_code == 200, resp.content
        # Приземлились в СВОЮ строку, а не в найденную по прокси-ключу.
        assert resp.json()["master_id"] == str(invited.id)
        synced.refresh_from_db()
        assert synced.ayla_user_id is not None
        assert synced.linked_bot_user_id is None


class TestReinviteAfterExpiryReachesTheCabinet:
    """DRF-1507 — повторное приглашение после протухшего токена работает.

    Сквозная проверка того самого пользовательского поведения, ради
    сохранения которого ``test_expired_invite_creates_new_row`` был
    переписан, а не удалён: владелец выписывает приглашение заново,
    мастер по свежей ссылке доходит до кабинета — и второй строки при
    этом не появляется.
    """

    def test_reissued_token_lands_the_master(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        stale = make_master(tenant, expires_in_days=-1, external_id=1_000_000)
        # Перевыпуск, как его делает admin_api (свежий токен на той же строке).
        stale.invite_token = uuid.uuid4()
        stale.invite_expires_at = datetime.now(tz=dt_timezone.utc) + timedelta(days=7)
        stale.invite_status = CatalogMaster.InviteStatus.PENDING
        stale.save(update_fields=["invite_token", "invite_expires_at", "invite_status"])

        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(stale.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["master_id"] == str(stale.id)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1
        payload = decode_master_session_token(resp.json()["session_token"])
        assert payload.master_id == stale.id


# --- /onboarding/reject ---------------------------------------------------


class TestOnboardingReject:
    def test_happy_path_cancels(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_reject",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 204
        # No body — spec says no master leak.
        assert resp.content == b""

        pending_master.refresh_from_db()
        assert pending_master.invite_status == CatalogMaster.InviteStatus.CANCELLED
        # No BotUser linkage on reject.
        assert pending_master.linked_bot_user_id is None

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_reject",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_rejected",
            target_id=pending_master.id,
        ).first()
        assert row is not None
        assert row.payload["bot_user_id"] == str(bot_user.id)

    def test_second_reject_blocked(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        body = {"token": str(pending_master.invite_token)}
        first = _post_json(
            client, "master_api:onboarding_reject", body=body, header=init_data_header("12345")
        )
        second = _post_json(
            client, "master_api:onboarding_reject", body=body, header=init_data_header("12345")
        )
        assert first.status_code == 204
        assert second.status_code == 409
        assert second.json()["error"] == "invite_already_used"
