"""Tests for /customer/recommendations Ayla proxy.

Covers:
- View: happy pass-through, Ayla 5xx graceful, invalid body, Ayla 4xx
  forwarding, config error.
- Client: HTTP layer error mapping (timeout / 5xx / 4xx / malformed JSON).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla import recommendations_client as rc
from apps.integrations.ayla.recommendation_resolver_client import ResolveOutcome
from apps.integrations.ayla.recommendations_client import (
    RecommendationsBadRequest,
    RecommendationsConfigError,
    RecommendationsUnavailable,
    fetch_recommendations,
    reset_recommendations_circuit,
)
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-recommendations"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _reset_rec_circuit():
    """Isolate the module-level recommendations breaker between cases (#1048)."""
    reset_recommendations_circuit()
    yield
    reset_recommendations_circuit()


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="rec-test",
        name="Recommendations Test",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "rec-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="88888",
        display_name="Ольга",
    )


def _decision_body(*ayla_ids: str) -> dict:
    """Решение резолвера в форме, которую отдаёт Ayla."""
    return {
        "data": {
            "decision_id": "d-1",
            "request_id": "r-1",
            "resolver_spec_version": "1.0.0",
            "ordered": [
                {
                    "candidate": {"kind": "PROVIDER", "id": ayla_id},
                    "rank": i + 1,
                    "tier": 1,
                    "reason_codes": ["MATCH_SERVICE_EXACT"],
                    "evidence": [],
                }
                for i, ayla_id in enumerate(ayla_ids)
            ],
        }
    }


# ─── TestViewIntegration ────────────────────────────────────────────────


class TestRecommendationsView:
    """Ручка ходит на ГРАНИЦУ (§9.4), а не на легаси-полку (DRF-1626).

    Дефект был не в форме ответа, а в проводе: существуют две ручки Ayla,
    транзит ходил на `internal/me/catalog/recommendations/` (три слоя), а
    полка мини-приложения написана против
    `internal/recommendation/resolve/` (`ordered[]`). Валидатор отвергал
    ответ целиком, `picks` оставался пустым — при том, что 55 вызовов из
    56 отвечали `200`. Ломалось не то, что отвечало.

    Тесты этого класса раньше закрепляли проводку на легаси-клиент, то
    есть **держали дефект**. Они не удалены, а переписаны: каждое
    утверждение, у которого предмет остался, сохранено (перевод ключей,
    непотерянный кандидат, своя авария своим именем), а те, чей предмет
    исчез вместе с чтением тела запроса, названы поимённо ниже.
    """

    def _url(self) -> str:
        return reverse("miniapp_api:customer_recommendations")

    def _post(self, client: Client, bot_user: BotUser, **extra):
        return client.post(
            self._url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            **extra,
        )

    @staticmethod
    def _resolver(outcome):
        return patch(
            "apps.integrations.ayla.recommendation_resolver_client.resolve_recommendation",
            return_value=outcome,
        )

    def test_the_request_is_built_here_and_never_taken_from_the_caller(
        self, client: Client, bot_user: BotUser
    ):
        """Тело запроса границы собирает сервер (§4.1).

        Заменяет прежние `test_invalid_input_non_object_body` и
        `test_empty_body_defaults_to_empty_dict`. У обоих предмет исчез:
        полка шлёт `POST` БЕЗ тела, а `subject_ref` в запросе границы
        отсутствует намеренно — кого спрашивают, определяет
        аутентификация. Приняв часть запроса от клиента, мы позволили бы
        ему получить решение за другого человека.

        Проверяется именно это: даже присланное тело не доезжает до
        границы, и обязательное `safety_state` уходит заполненным.
        """
        captured: dict = {}

        def _fake(*, external_user_id: str, payload: dict):
            captured["external_user_id"] = external_user_id
            captured["payload"] = payload
            return ResolveOutcome("ok", decision=_decision_body()["data"])

        with patch(
            "apps.integrations.ayla.recommendation_resolver_client.resolve_recommendation",
            side_effect=_fake,
        ):
            resp = self._post(
                client,
                bot_user,
                data=json.dumps({"lat": 999, "goal": "подсунутая цель"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        assert captured["external_user_id"] == f"bot:max:{bot_user.channel_user_id}"
        sent = captured["payload"]
        assert "lat" not in sent, "тело клиента доехало до границы"
        assert sent["safety_state"] == "NOT_APPLICABLE", (
            "обязательное поле без умолчания уехало не заполненным"
        )
        assert sent["surface"] == "MINIAPP_HOME"
        assert sent["request_id"], "нет ключа воспроизводимости (§9.4)"

    def test_the_decision_reaches_the_shelf_in_its_envelope(
        self, client: Client, bot_user: BotUser
    ):
        """Ответ уходит полке в конверте `{"data": …}` (§9.4).

        Наследник `test_happy_path_pass_through`. Предмет сменился с
        «тело Ayla возвращается дословно» на «решение границы возвращается
        в объявленной форме»: дословность больше не свойство, потому что
        транзит теперь обязан валидировать (§2.1 C3).
        """
        with self._resolver(ResolveOutcome("ok", decision=_decision_body()["data"])):
            resp = self._post(client, bot_user)

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body, "конверт §9.4 потерян — потребитель отвергнет ответ целиком"
        assert body["data"]["resolver_spec_version"] == "1.0.0"
        assert body["data"]["decision_id"] == "d-1"

    def test_provider_keys_are_translated_to_mirror_ids(
        self, client: Client, bot_user: BotUser, tenant: Tenant
    ):
        """Ключ Ayla заменяется ключом зеркала — иначе полка не узнает никого.

        Полка живёт в ключах зеркала и ключа Ayla не знает: поля у неё
        нет. Перевести может только этот слой (DRF-1598, OD §81).
        """
        from django.utils import timezone

        from apps.catalog.models import CatalogMaster

        ayla_id = uuid.uuid4()
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_updated_at=timezone.now(),
            external_id=970001,
            name="Мастер зеркала",
            specialization="Парикмахер",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            ayla_user_id=ayla_id,
        )

        with self._resolver(ResolveOutcome("ok", decision=_decision_body(str(ayla_id))["data"])):
            resp = self._post(client, bot_user)

        assert resp.status_code == 200
        ordered = resp.json()["data"]["ordered"]
        assert ordered[0]["candidate"]["id"] == str(master.id)
        assert ordered[0]["candidate"]["kind"] == "PROVIDER"

    def test_an_untranslatable_candidate_is_not_dropped(
        self, client: Client, bot_user: BotUser, tenant: Tenant
    ):
        """Непустой `ordered` не превращается в пустой молча.

        Отбрось мы непереводимого здесь — полка получила бы пустой подбор
        при НЕПУСТОМ решении и назвала бы это состояние `OK`: её
        классификатор смотрит на `ordered`, а он бы уже опустел.

        Поэтому кандидат доезжает как есть, с ключом Ayla, и полка сама
        поднимает `UNRENDERABLE_CANDIDATES` — имя у состояния уже есть,
        второго классификатора мы не заводим.
        """
        stranger = str(uuid.uuid4())

        with self._resolver(ResolveOutcome("ok", decision=_decision_body(stranger)["data"])):
            resp = self._post(client, bot_user)

        assert resp.status_code == 200
        ordered = resp.json()["data"]["ordered"]
        assert len(ordered) == 1, "кандидат обязан доехать, а не исчезнуть"
        assert ordered[0]["candidate"]["id"] == stranger

    def test_mirror_failure_is_unavailability_not_a_silent_pass_through(
        self, client: Client, bot_user: BotUser
    ):
        """Своя авария называется своим именем.

        Пропусти мы кандидатов непереведёнными при упавшем чтении зеркала,
        полка сказала бы «нам прислали то, чего мы не умеем» — то есть
        обвинила бы Ayla в НАШЕЙ аварии, и чинить пошли бы не там.
        """
        from django.db import DatabaseError

        decision = _decision_body(str(uuid.uuid4()))["data"]
        with (
            self._resolver(ResolveOutcome("ok", decision=decision)),
            patch(
                "apps.marketplace.resolver_keys.translate_provider_keys",
                side_effect=DatabaseError("mirror is down"),
            ),
        ):
            resp = self._post(client, bot_user)

        assert resp.status_code == 503
        assert resp.json()["error"] == "mirror_unavailable"

    def test_unavailable_is_quiet_and_named(self, client: Client, bot_user: BotUser):
        """Граница не ответила — 502 `ayla_unavailable`.

        Наследник `test_ayla_5xx_graceful` и `test_config_error_returns_503`.
        Второй сменил число намеренно: клиент границы относит ошибку
        конфигурации к недоступности («подбора нет, врать нельзя, шуметь
        незачем»), поэтому отдельного 503 `not_configured` на этом пути
        больше не существует. Это изменение поведения, и оно названо.
        """
        with self._resolver(ResolveOutcome("unavailable", detail="server: HTTP 503")):
            resp = self._post(client, bot_user)

        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"

    def test_contract_violation_is_loud_and_told_apart_from_silence(
        self, client: Client, bot_user: BotUser
    ):
        """Третий исход отличим от второго — и это половина задачи.

        Наследник `test_ayla_4xx_forwarded`, у которого предмет сменился
        целиком: 4xx на запрос границы больше не «Ayla отвергла тело
        клиента» (клиент тела не шлёт), а «мы и они разошлись в том, о чём
        договорились».

        До DRF-1626 несовместимость давала ПУСТУЮ ПОЛКУ, неотличимую от
        «ничего не нашлось»: человек и дежурный видели одно и то же в двух
        совершенно разных случаях.
        """
        with self._resolver(ResolveOutcome("contract_violation", detail="ordered отсутствует")):
            resp = self._post(client, bot_user)

        assert resp.status_code == 502
        assert resp.json()["error"] == "contract_violation"
        assert resp.json()["error"] != "ayla_unavailable"

    def test_the_two_bad_outcomes_are_counted_apart(self, client: Client, bot_user: BotUser):
        """Считаемый след, а не строка в логе (§9.4).

        «Отдельно в метрику» значит, что вопрос «сколько раз за неделю»
        отвечается запросом. Строка лога на него не отвечает без парсера,
        которого никто не напишет.

        Проверяется РАЗЛИЧИМОСТЬ: два исхода обязаны дать два разных
        действия в аудите. Совпадение означало бы, что посчитать
        расхождение контракта отдельно нечем.
        """
        from apps.audit.models import AuditLog

        with self._resolver(ResolveOutcome("contract_violation", detail="ordered отсутствует")):
            self._post(client, bot_user)
        with self._resolver(ResolveOutcome("unavailable", detail="network")):
            self._post(client, bot_user)

        actions = {
            row.action for row in AuditLog.all_tenants.filter(target="RecommendationBoundary")
        }
        assert actions, "исход границы не оставил следа — считать нечего"
        assert actions == {
            "recommendation.boundary.contract_violation",
            "recommendation.boundary.unavailable",
        }, f"два исхода записаны как {actions}"

    def test_the_controlled_empty_state_is_not_counted_as_a_failure(
        self, client: Client, bot_user: BotUser
    ):
        """Три вещи, которые выглядят одной пустой полкой, считаются порознь.

        §10.5.1: ноль подтверждённых связей — ШТАТНЫЙ результат, а не
        ошибка, и «пустая полка перестаёт быть дефектом и становится
        состоянием с именем и числом». Попади оно в счётчик поломок, мы
        стали бы чинить работающее.

        Контракт требует писать это событие с количествами, поэтому
        проверяется и число: имя без числа отвечает «что-то пусто», но не
        «чего именно не хватает».
        """
        from apps.audit.models import AuditLog

        empty = _decision_body()["data"]
        empty["excluded"] = [
            {
                "candidate": {"kind": "PROVIDER", "id": str(uuid.uuid4())},
                "stage": "S1",
                "reason_code": "ELIG_EXCLUDED_NOT_RECOMMENDABLE",
            }
        ]
        with self._resolver(ResolveOutcome("ok", decision=empty)):
            resp = self._post(client, bot_user)
        with self._resolver(ResolveOutcome("contract_violation", detail="x")):
            self._post(client, bot_user)
        with self._resolver(ResolveOutcome("unavailable", detail="y")):
            self._post(client, bot_user)

        assert resp.status_code == 200, "штатный ноль подтверждённых — не ошибка"

        rows = list(AuditLog.all_tenants.filter(target="RecommendationBoundary"))
        actions = {r.action for r in rows}
        assert actions == {
            "recommendation.boundary.no_verified_candidates",
            "recommendation.boundary.contract_violation",
            "recommendation.boundary.unavailable",
        }, f"три состояния записаны как {actions}"

        controlled = next(
            r for r in rows if r.action == "recommendation.boundary.no_verified_candidates"
        )
        assert controlled.payload["excluded_total"] == 1, "имя есть, числа нет"
        assert controlled.payload["excluded_by_reason"] == {"ELIG_EXCLUDED_NOT_RECOMMENDABLE": 1}


# ─── TestClient (HTTP layer) ────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _FakeHttpxClient:
    def __init__(self, *, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.last_call: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url: str, *, headers: dict, json: dict):
        self.last_call = {"url": url, "headers": headers, "json": json}
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


class TestFetchRecommendations:
    def test_happy_path_returns_body(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload={"recommendations": []})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            out = fetch_recommendations(
                external_user_id="bot:max:1",
                payload={"goal": "relax"},
            )
        assert out == {"recommendations": []}
        assert fake.last_call["url"] == (
            "https://ayla.test/api/v1/internal/me/catalog/recommendations/"
        )
        assert fake.last_call["headers"]["Authorization"] == "Bearer tok"
        assert fake.last_call["headers"]["X-External-User-ID"] == "bot:max:1"
        assert fake.last_call["json"] == {"goal": "relax"}

    def test_config_error_when_token_missing(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = ""
        with pytest.raises(RecommendationsConfigError):
            fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_5xx_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="server"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_timeout_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(raise_exc=httpx.ConnectTimeout("slow"))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="network"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_4xx_raises_bad_request_with_body(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=422, payload={"detail": "lat/lon out of range"})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsBadRequest) as exc_info:
                fetch_recommendations(external_user_id="bot:max:1", payload={})
        assert exc_info.value.status_code == 422
        assert exc_info.value.body == {"detail": "lat/lon out of range"}

    def test_malformed_json_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload=ValueError("bad json"))
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="malformed_json"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_non_dict_top_level_raises(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=200, payload=["a", "list"]))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="not an object"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_breaker_opens_after_threshold_5xx(self, settings, monkeypatch):
        """5 consecutive 5xx → breaker opens; next call short-circuits (#1048)."""
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        # Silence the Telegram alert path — the breaker is the unit under test.
        monkeypatch.setattr(rc, "_fire_breaker_alert", lambda transition, failures: None)
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            for _ in range(rc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(RecommendationsUnavailable, match="server"):
                    fetch_recommendations(external_user_id="bot:max:1", payload={})
            # Threshold reached — the next call short-circuits BEFORE the
            # transport, so the message is ``circuit_open`` not ``server``.
            with pytest.raises(RecommendationsUnavailable, match="circuit_open"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_4xx_does_not_trip_breaker(self, settings):
        """A run of 4xx (we sent garbage, Ayla is healthy) must NOT open the breaker."""
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        bad = _FakeHttpxClient(response=_FakeResponse(status_code=422, payload={"detail": "x"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=bad,
        ):
            for _ in range(rc.CIRCUIT_FAILURE_THRESHOLD + 1):
                with pytest.raises(RecommendationsBadRequest):
                    fetch_recommendations(external_user_id="bot:max:1", payload={})
        # Breaker is still closed: a subsequent healthy call goes through.
        ok = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload={"recommendations": []})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=ok,
        ):
            out = fetch_recommendations(external_user_id="bot:max:1", payload={})
        assert out == {"recommendations": []}
