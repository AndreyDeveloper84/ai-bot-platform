"""Согласие на сканирование еды доезжает до гейта навыка (DRF-1564).

Предмет строки: колонка ``BotUser.food_scanner_consent_at`` существует с
миграции ``0013`` и её читает гейт (``apps/skills/food_scanner/skill.py``),
а **писателей у неё не было ни одного**. Согласие человека оседало в
``localStorage`` мини-приложения: экран его принимал и пропускал дальше, а
бот на то же самое согласие отвечал «открой Mini App и дай согласие».

Петля, из которой человек не выходит своими силами. Тесты держат обе её
половины: запись доезжает до колонки, и гейт после записи открывается.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-scanner"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="scanner-test", name="Scanner Test", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "scanner-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="91777",
        display_name="Анна",
        client_name="Анна К.",
    )


def _url() -> str:
    return reverse("miniapp_api:food_scanner_consent")


@pytest.mark.django_db
class TestFoodScannerConsentEndpoint:
    def test_grant_writes_the_column_the_skill_gate_reads(self, client: Client, bot_user: BotUser):
        # Присутствие ВПЕРЕДИ: до запроса колонка пуста — иначе тест
        # зеленел бы на строке, где согласие уже стояло.
        bot_user.refresh_from_db()
        assert bot_user.food_scanner_consent_at is None

        resp = client.post(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["granted_at"]

        bot_user.refresh_from_db()
        # Та самая колонка, и настоящий datetime — гейт требует именно его.
        assert bot_user.food_scanner_consent_at is not None

    def test_the_skill_gate_opens_after_the_grant(
        self, client: Client, bot_user: BotUser, settings
    ):
        """Половина, ради которой ручка написана: петля разомкнулась.

        Гейт спрашивает три вещи по порядку: рубильник питания, гейт
        фото и согласие. Первые два здесь подняты намеренно — иначе
        тест зеленел бы на отказе «питание выключено» и ничего не
        говорил бы про согласие.
        """
        settings.NUTRITION_ENABLED = True

        from unittest.mock import Mock

        from apps.skills.base import SkillContext
        from apps.skills.food_scanner.skill import _check_gates

        def _ctx(bu: BotUser) -> SkillContext:
            """Настоящий `SkillContext`, а не самодельная заглушка.

            Способ подсмотрен у соседей (`apps/skills/food_scanner/tests/
            test_skill.py`), а не выдуман: гейту нужен объявленный тип, и
            подсовывать ему свой лёгкий класс значит проверять не то, что
            зовёт живой код. Разговор здесь `Mock` — гейт трогает у него
            только `id`, и заводить строку в базе ради `logger.info`
            было бы платой ни за что.
            """
            return SkillContext(
                conversation=Mock(id="conv-consent"),
                bot_user=bu,
                message_text="",
            )

        bot_user.refresh_from_db()
        # ДО: гейт отказывает и просит открыть мини-приложение.
        before = _check_gates(_ctx(bot_user), require_photo_scan=False, kind="callback")
        assert before is not None
        assert before.meta["reply_kind"] == "food_scanner_consent_required"

        client.post(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        bot_user.refresh_from_db()

        # ПОСЛЕ: отказа нет. Это и есть предмет DRF-1564.
        assert _check_gates(_ctx(bot_user), require_photo_scan=False, kind="callback") is None

    def test_withdraw_clears_it_and_the_gate_closes_again(self, client: Client, bot_user: BotUser):
        hdr = _init_data_header(bot_user.channel_user_id)
        client.post(_url(), HTTP_AUTHORIZATION=hdr)
        bot_user.refresh_from_db()
        # Положительная стража впереди: согласие действительно стояло.
        assert bot_user.food_scanner_consent_at is not None

        resp = client.delete(_url(), HTTP_AUTHORIZATION=hdr)
        assert resp.status_code == 200
        assert resp.json()["granted"] is False

        bot_user.refresh_from_db()
        assert bot_user.food_scanner_consent_at is None

    def test_grant_is_idempotent(self, client: Client, bot_user: BotUser):
        hdr = _init_data_header(bot_user.channel_user_id)
        first = client.post(_url(), HTTP_AUTHORIZATION=hdr).json()
        second = client.post(_url(), HTTP_AUTHORIZATION=hdr).json()
        assert first["granted"] is True
        assert second["granted"] is True
        # Момент обновляется — согласие даётся заново, и это честно:
        # человек нажал кнопку второй раз, значит подтвердил второй раз.
        assert second["granted_at"] >= first["granted_at"]

    def test_get_reports_the_state_without_changing_it(self, client: Client, bot_user: BotUser):
        hdr = _init_data_header(bot_user.channel_user_id)
        assert client.get(_url(), HTTP_AUTHORIZATION=hdr).json()["granted"] is False
        bot_user.refresh_from_db()
        # Чтение не выдаёт согласия — иначе «посмотреть» значило бы «дать».
        assert bot_user.food_scanner_consent_at is None

    def test_the_profile_carries_the_same_value(self, client: Client, bot_user: BotUser):
        """Один вызов, один источник: экран не спрашивает согласие отдельно."""
        hdr = _init_data_header(bot_user.channel_user_id)
        granted_at = client.post(_url(), HTTP_AUTHORIZATION=hdr).json()["granted_at"]

        me = client.get(reverse("miniapp_api:me"), HTTP_AUTHORIZATION=hdr).json()
        assert me["food_scanner_consent_at"] == granted_at

    def test_no_consent_reaches_the_profile_as_null_not_as_a_missing_key(
        self, client: Client, bot_user: BotUser
    ):
        hdr = _init_data_header(bot_user.channel_user_id)
        me = client.get(reverse("miniapp_api:me"), HTTP_AUTHORIZATION=hdr).json()
        # Присутствие впереди: ответ профиля пришёл и он не пуст.
        assert me["bot_user_id"]
        # Ключ ЕСТЬ и он `null` — «спросили, согласия нет». Отсутствие
        # ключа означало бы «не спросили», и экран читает его так же
        # (fail-closed), но состояния это разные.
        assert "food_scanner_consent_at" in me
        assert me["food_scanner_consent_at"] is None


@pytest.mark.django_db
class TestFoodScannerConsentAcrossShells:
    """Согласие даётся человеком, а не строкой в таблице.

    Чат и мини-приложение — разные ``BotUser`` одного человека. Согласие,
    записанное в одну строку, не открыло бы гейт, читающий другую, и
    человек давал бы его заново при каждой смене поверхности.
    """

    def test_the_grant_reaches_every_shell_of_the_person(self, client: Client, bot_user: BotUser):
        from apps.consent.customer import _person_shells

        shells = _person_shells(bot_user)
        # Положительная стража впереди: оболочек хотя бы одна, и ни у
        # одной согласия нет.
        assert len(shells) >= 1
        assert all(s.food_scanner_consent_at is None for s in shells)

        client.post(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))

        refreshed = BotUser.all_tenants.filter(id__in=[s.id for s in shells])
        assert refreshed.count() == len(shells)
        assert all(s.food_scanner_consent_at is not None for s in refreshed)

    def test_the_withdrawal_reaches_every_shell_too(self, client: Client, bot_user: BotUser):
        from apps.consent.customer import _person_shells

        hdr = _init_data_header(bot_user.channel_user_id)
        client.post(_url(), HTTP_AUTHORIZATION=hdr)
        shells = _person_shells(bot_user)
        refreshed = BotUser.all_tenants.filter(id__in=[s.id for s in shells])
        assert all(s.food_scanner_consent_at is not None for s in refreshed)

        client.delete(_url(), HTTP_AUTHORIZATION=hdr)

        refreshed = BotUser.all_tenants.filter(id__in=[s.id for s in shells])
        assert all(s.food_scanner_consent_at is None for s in refreshed)


@pytest.mark.django_db
class TestFoodScannerConsentAudit:
    def test_the_grant_leaves_a_trace(self, client: Client, bot_user: BotUser):
        """Согласие — юридический факт: «кто и когда» обязано остаться."""
        from apps.audit.models import AuditLog

        # `all_tenants`, а не `objects`: обычный менеджер тенант-скоупится
        # и вне запроса возвращает пусто. С `objects` этот тест зеленел бы
        # НА НУЛЕ — то есть доказывал бы отсутствие строки вместо её
        # наличия, а сам аудит при этом работал.
        before = AuditLog.all_tenants.filter(action="consent.food_scanner_changed").count()
        client.post(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        rows = AuditLog.all_tenants.filter(action="consent.food_scanner_changed")
        assert rows.count() == before + 1
        row = rows.last()
        # Сужение явное, а не `# type: ignore`: строку выше доказывает
        # СРАВНЕНИЕ количеств, но проверяющему типов об этом неизвестно,
        # и `ignore` похоронил бы доказательство под отметкой вместо
        # того, чтобы его использовать.
        assert row is not None
        assert row.payload["granted"] is True
