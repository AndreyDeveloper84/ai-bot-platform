"""DRF-2729 — теневой читатель знания: лицензия на ход и неизменность ответа.

Читатель ТЕНЕВОЙ: он зовёт ручку каталога и собирает лицензию для затвора
утверждений. Модель знания не видит, ответ человеку не меняется — это и есть
главное свойство листа, и проверяется оно матрицей ``TestTurnIsUnchanged``:
один и тот же ход при пяти состояниях читателя.

Ответы каталога — из фикстуры, снятой с живой ручки
(``apps/integrations/ayla/tests/fixtures/knowledge_procedures_live.json``).

* r1 — флаг: три «выключено» и два «включено»;
* r2 — выключен → ``None``, ни запроса в каталог, ни чтения зеркала;
* r3 — включён, разрешённой услуги нет → лицензия без предметов (не ``None``);
* r4 — предмет = услуга карточки → id услуги салона в каталоге;
* r5 — без повторов и не больше трёх предметов;
* r6 — строка зеркала без id каталога пропускается, а не угадывается;
* r7 — момент чтения — по часам бота, до первого вызова;
* r8 — бюджет хода: что не успело — ``UNAVAILABLE`` без вызова;
* r9 — упавший читатель не стоит хода;
* r10 — свежесть: просроченное утверждение видно затвору;
* матрица — ход целиком.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import CatalogService
from apps.integrations.ayla import knowledge_client as kc
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, knowledge_reader
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.decision_readiness.shadow import FlagSource
from apps.orchestrator.knowledge_licence import (
    KnowledgeLicence,
    LicensedSubject,
    SubjectState,
)
from apps.orchestrator.knowledge_reader import licence_for_cards, read_flag
from apps.orchestrator.safety.claim_gate import assess
from apps.tenancy.models import Tenant

_LIVE = json.loads(
    (
        Path(__file__).parents[2]
        / "integrations"
        / "ayla"
        / "tests"
        / "fixtures"
        / "knowledge_procedures_live.json"
    ).read_text(encoding="utf-8")
)["responses"]
KNOWN_BODY: dict[str, Any] = _LIVE["known_by_salon_service"]["body"]
UNKNOWN_BODY: dict[str, Any] = _LIVE["unknown_by_salon_service"]["body"]

VERIFIED_SERVICE = "d98cfcc4-ba87-4270-bef5-c459f69f0f2e"
REVIEW_SERVICE = "85533e18-94a5-4b5d-9dd0-c5bb6080fa7a"
CAPABILITY_CLAIM = "d698ee16-a81f-4aef-b9ea-85ba27e6eb8c"
GOAL_LINK_CLAIM = "e961f272-9fed-4217-b796-d5f700078782"

Handler = Callable[[httpx.Request], httpx.Response]


def _by_subject(request: httpx.Request) -> httpx.Response:
    """Каталог из живой фикстуры: отвечает по предмету запроса."""
    asked = request.url.params.get("salon_service_id")
    if asked == VERIFIED_SERVICE:
        return httpx.Response(200, json=KNOWN_BODY)
    if asked == REVIEW_SERVICE:
        return httpx.Response(200, json=UNKNOWN_BODY)
    return httpx.Response(404, json=_LIVE["not_found"]["body"])


@pytest.fixture
def catalog(settings: Any) -> Generator[Callable[[Handler], list[str]], None, None]:
    """Настоящий клиент знания поверх подменённого транспорта.

    Возвращает установщик; тот отдаёт журнал — по какому предмету спрашивали.
    """
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "reader-token-sentinel"  # noqa: S105
    kc.close_knowledge_client()
    kc.reset_knowledge_circuit()

    def install(handler: Handler = _by_subject) -> list[str]:
        asked: list[str] = []

        def recording(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params.get("salon_service_id"))
            return handler(request)

        kc.close_knowledge_client()
        kc._http = httpx.Client(transport=httpx.MockTransport(recording))
        return asked

    yield install
    kc.close_knowledge_client()
    kc.reset_knowledge_circuit()


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="salon-2729", name="Salon 2729", city="Пенза")


def _mirror(
    tenant: Tenant, slug: str, *, ayla_service_id: str | None, pk: str | None = None
) -> CatalogService:
    return CatalogService.all_tenants.create(
        **({} if pk is None else {"id": uuid.UUID(pk)}),
        tenant=tenant,
        slug=slug,
        name=f"Услуга {slug}",
        is_active=True,
        ayla_service_id=ayla_service_id,
        external_updated_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )


def _card(service_id: Any = None, *, master_id: str = "m1") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="t1",
        master_id=master_id,
        name="Анна",
        specialization="Косметология",
        rating=4.9,
        city="Пенза",
        service_id=service_id,
        service_name="" if service_id is None else "Пилинг",
    )


# ─── r1: флаг ────────────────────────────────────────────────────────────────


class TestFlag:
    def test_r1_no_key_is_off_by_read_default(self, settings: Any) -> None:
        # Что ключа в настройках нет, говорит сам источник значения ниже:
        # ``READ_DEFAULT`` бывает только при отсутствующем ключе.
        reading = read_flag()

        assert reading.value is False
        assert reading.source is FlagSource.READ_DEFAULT

    @pytest.mark.parametrize(
        ("raw", "value", "source"),
        [
            ("true", True, FlagSource.SETTINGS),
            (" ON ", True, FlagSource.SETTINGS),
            (True, True, FlagSource.SETTINGS),
            ("false", False, FlagSource.SETTINGS),
            ("", False, FlagSource.SETTINGS),
            (False, False, FlagSource.SETTINGS),
            # ``bool("да")`` и ``bool("disabled")`` — True; флаг, который нельзя
            # прочитать, не должен ничего включать.
            ("да", False, FlagSource.MALFORMED),
            ("disabled", False, FlagSource.MALFORMED),
            (1, False, FlagSource.MALFORMED),
        ],
    )
    def test_r1_values(self, settings: Any, raw: Any, value: bool, source: FlagSource) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = raw

        reading = read_flag()

        assert reading.value is value
        assert reading.source is source

    def test_r1_the_claim_gate_flag_does_not_switch_the_reader_on(self, settings: Any) -> None:
        """Два флага раздельны: включённый затвор чтения не включает."""
        settings.CLAIM_GATE_SHADOW_ENABLED = "true"

        assert read_flag().value is False

    def test_r1_setting_name_is_the_decided_one(self) -> None:
        assert knowledge_reader.READ_SETTING == "KNOWLEDGE_READ_SHADOW_ENABLED"


# ─── r2–r9: лицензия ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLicence:
    def test_r2_flag_off_means_no_reader_no_request_no_mirror_read(
        self, catalog: Any, salon: Tenant
    ) -> None:
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        with CaptureQueriesContext(connection) as queries:
            licence = licence_for_cards([_card(row.pk)])

        assert licence is None
        assert asked == []
        assert len(queries) == 0

    def test_r2_twin_flag_on_reads_the_same_cards(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        licence = licence_for_cards([_card(row.pk)])

        assert asked == [VERIFIED_SERVICE]
        assert licence is not None
        assert [s.state for s in licence.subjects] == [SubjectState.KNOWN]
        assert [c.claim_id for c in licence.claims()] == [CAPABILITY_CLAIM, GOAL_LINK_CLAIM]

    def test_r3_no_resolved_service_is_an_empty_licence_not_none(
        self, catalog: Any, settings: Any
    ) -> None:
        """«Читатель был, читать было не по чему» — не то же, что «читателя не было»."""
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()

        licence = licence_for_cards([_card(None), _card(None, master_id="m2")])

        assert isinstance(licence, KnowledgeLicence)
        assert licence.subjects == ()
        assert asked == []

    def test_r3_no_cards_at_all_is_an_empty_licence(self, catalog: Any, settings: Any) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()

        licence = licence_for_cards([])

        assert isinstance(licence, KnowledgeLicence)
        assert licence.subjects == ()
        assert asked == []

    def test_r4_each_subject_carries_its_own_state(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        known = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)
        unknown = _mirror(salon, "mask", ayla_service_id=REVIEW_SERVICE)
        absent = _mirror(salon, "wrap", ayla_service_id="00000000-0000-4000-8000-000000002729")

        licence = licence_for_cards(
            [_card(known.pk), _card(unknown.pk, master_id="m2"), _card(absent.pk, master_id="m3")]
        )

        assert licence is not None
        assert asked == [
            VERIFIED_SERVICE,
            REVIEW_SERVICE,
            "00000000-0000-4000-8000-000000002729",
        ]
        assert [(s.salon_service_id, s.state) for s in licence.subjects] == [
            (VERIFIED_SERVICE, SubjectState.KNOWN),
            (REVIEW_SERVICE, SubjectState.UNKNOWN),
            ("00000000-0000-4000-8000-000000002729", SubjectState.UNAVAILABLE),
        ]

    def test_r5_same_service_on_several_cards_is_read_once(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        licence = licence_for_cards([_card(row.pk, master_id=f"m{i}") for i in range(5)])

        assert asked == [VERIFIED_SERVICE]
        assert licence is not None
        assert len(licence.subjects) == 1

    def test_r5_no_more_than_three_subjects_per_turn(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        ids = [f"00000000-0000-4000-8000-00000000000{i}" for i in range(5)]
        rows = [_mirror(salon, f"svc-{i}", ayla_service_id=ids[i]) for i in range(5)]

        licence = licence_for_cards([_card(r.pk, master_id=f"m{i}") for i, r in enumerate(rows)])

        # Первые три — в порядке карточек; четвёртая и пятая не читаются.
        assert asked == ids[:3]
        assert licence is not None
        assert len(licence.subjects) == 3

    def test_r6_mirror_row_without_catalog_id_is_skipped_not_guessed(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        legacy = _mirror(salon, "legacy", ayla_service_id=None)
        linked = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        licence = licence_for_cards([_card(legacy.pk), _card(linked.pk, master_id="m2")])

        assert asked == [VERIFIED_SERVICE]
        assert licence is not None
        assert [s.salon_service_id for s in licence.subjects] == [VERIFIED_SERVICE]

    def test_r7_read_at_is_the_bots_clock_before_the_first_call(
        self, catalog: Any, salon: Tenant, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        bot_now = datetime(2026, 12, 24, 18, 30, tzinfo=UTC)
        calls_when_clock_was_read: list[int] = []
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        def clock() -> datetime:
            calls_when_clock_was_read.append(len(asked))
            return bot_now

        monkeypatch.setattr(knowledge_reader.timezone, "now", clock)

        licence = licence_for_cards([_card(row.pk)])

        assert licence is not None
        # Не ``as_of`` каталога (в фикстуре — 02.10.2026), а часы бота.
        assert licence.read_at == datetime(2026, 12, 24, 18, 30, tzinfo=UTC)
        assert KNOWN_BODY["data"]["as_of"].startswith("2026-10-02")
        assert calls_when_clock_was_read == [0]  # часы прочитаны до первого запроса
        assert asked == [VERIFIED_SERVICE]

    def test_r8_out_of_budget_subjects_are_unavailable_without_a_call(
        self, catalog: Any, salon: Tenant, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        first = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)
        second = _mirror(salon, "mask", ayla_service_id=REVIEW_SERVICE)
        # Подменяется имя ``time`` в модуле читателя, а не общий ``time.monotonic``: иначе
        # те же отсчёты съел бы предохранитель клиента.
        # Часы хода: старт, проверка перед 1-м предметом (0 с), перед 2-м (5 с —
        # ровно бюджет), далее — для строки журнала.
        ticks = iter([100.0, 100.0, 105.0, 105.0])
        monkeypatch.setattr(
            knowledge_reader, "time", SimpleNamespace(monotonic=lambda: next(ticks))
        )

        licence = licence_for_cards([_card(first.pk), _card(second.pk, master_id="m2")])

        assert asked == [VERIFIED_SERVICE]
        assert licence is not None
        assert [(s.salon_service_id, s.state) for s in licence.subjects] == [
            (VERIFIED_SERVICE, SubjectState.KNOWN),
            (REVIEW_SERVICE, SubjectState.UNAVAILABLE),
        ]

    def test_r8_twin_inside_the_budget_both_are_read(
        self, catalog: Any, salon: Tenant, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        first = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)
        second = _mirror(salon, "mask", ayla_service_id=REVIEW_SERVICE)
        ticks = iter([100.0, 100.0, 104.9, 104.9])
        monkeypatch.setattr(
            knowledge_reader, "time", SimpleNamespace(monotonic=lambda: next(ticks))
        )

        licence = licence_for_cards([_card(first.pk), _card(second.pk, master_id="m2")])

        assert asked == [VERIFIED_SERVICE, REVIEW_SERVICE]
        assert licence is not None
        assert [s.state for s in licence.subjects] == [SubjectState.KNOWN, SubjectState.UNKNOWN]

    def test_r8_budget_and_cap_are_the_decided_numbers(self) -> None:
        assert knowledge_reader.TURN_BUDGET_S == 5.0
        assert knowledge_reader.MAX_SUBJECTS == 3

    def test_r9_a_crashing_reader_returns_none_and_does_not_raise(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        def boom(**kwargs: Any) -> LicensedSubject:
            raise RuntimeError("reader bug")

        monkeypatch.setattr(kc, "read_subject", boom)

        licence = licence_for_cards([_card(row.pk)])

        assert licence is None
        assert "orchestrator.knowledge_reader.failed" in caplog.text

    def test_r9_catalog_down_is_a_licence_with_an_unavailable_subject(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        """Каталог лежит — это не «читатель упал»: лицензия есть, предмет недоступен."""
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"

        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        asked = catalog(down)
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        licence = licence_for_cards([_card(row.pk)])

        assert asked == [VERIFIED_SERVICE]
        assert licence is not None
        assert [(s.salon_service_id, s.state) for s in licence.subjects] == [
            (VERIFIED_SERVICE, SubjectState.UNAVAILABLE)
        ]

    def test_r10_an_expired_claim_is_visible_to_the_gate(
        self, catalog: Any, salon: Tenant, settings: Any
    ) -> None:
        """Свежесть: срок из ответа каталога доезжает до затвора.

        У возможности в живом ответе срок 01.10.2027 09:00 UTC; у связи с
        целью срока нет. В сам момент срока утверждение уже не говорится.
        """
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)
        licence = licence_for_cards([_card(row.pk)])
        assert licence is not None
        deadline = datetime(2027, 10, 1, 9, 0, tzinfo=UTC)

        before = assess(licence, now=deadline - timedelta(seconds=1))
        at = assess(licence, now=deadline)

        assert before.stale_claim_ids == ()
        assert at.stale_claim_ids == (CAPABILITY_CLAIM,)
        assert at.claims == 2

    def test_log_line_carries_counts_and_no_wording(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        caplog.set_level(logging.DEBUG)
        catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        licence = licence_for_cards([_card(row.pk)], trace_id="trace-2729")

        assert (
            "orchestrator.knowledge_reader.read resolved=1 read=1 known=1 unknown=0 "
            "unavailable=0 claims=2" in caplog.text
        )
        assert "trace=trace-2729" in caplog.text
        assert "SYNTHETIC" not in caplog.text
        assert licence is not None
        assert [claim.claim_id for claim in licence.claims()] == [CAPABILITY_CLAIM, GOAL_LINK_CLAIM]
        assert "SYNTHETIC" not in repr(licence)


# ─── матрица: ход целиком ────────────────────────────────────────────────────

MODEL_PROSE = "У Анны есть окна в четверг — показать время?"


def _tool_call() -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="show_masters", arguments={"city": "Пенза"})],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _prose(text: str = MODEL_PROSE) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


@pytest.mark.django_db(transaction=True)
class TestTurnIsUnchanged:
    """Один и тот же ход консьержа при разных состояниях читателя."""

    def _turn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        uid: str,
        service_pk: Any,
        results: list[CompletionResult] | None = None,
    ) -> tuple[Any, list[Any]]:
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        provider = AsyncMock()
        provider.complete.side_effect = results or [_tool_call(), _prose()]
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card(service_pk)])
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=uid, chat_id=f"{uid}-chat"
        )
        conversation = resolve_active_global_conversation(bot_user)

        reply = generate_concierge_reply(
            "к кому записаться на пилинг в четверг",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=str(uuid.uuid4()),
        )
        sent = [call.args[0] for call in provider.complete.call_args_list]
        return reply, sent

    def _visible(self, reply: Any) -> dict[str, Any]:
        """Всё, что от хода доходит до человека и до следующих шагов, кроме лицензии."""
        return {
            "text": reply.text,
            "action_data": reply.action_data,
            "persisted": reply.persisted,
            "outage": reply.outage,
            "tool_trace": reply.tool_trace,
        }

    def test_matrix_reply_and_prompts_are_identical_across_reader_states(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Одна и та же услуга во всех шести ходах: меняется только читатель и каталог.
        known = _mirror(
            salon,
            "peel",
            ayla_service_id=VERIFIED_SERVICE,
            pk="11111111-0000-4000-8000-000000002729",
        )

        # Журнал запросов в каталог — один на весь узел: «в этом ходу не
        # спрашивали» читается как «журнал не вырос», а не как пустой список,
        # который был бы пуст и у сломанной подмены.
        answer: dict[str, Handler] = {"with": _by_subject}
        asked = catalog(lambda request: answer["with"](request))

        # 1. Включён, каталог знает. Идёт первым: доказывает, что подмена
        #    каталога вообще видит запросы этого хода.
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        on_known, known_sent = self._turn(monkeypatch, uid="2729-known", service_pk=known.pk)
        assert asked == [VERIFIED_SERVICE]
        assert on_known.knowledge_licence.subjects_in(SubjectState.KNOWN) == 1

        # 2. Флага нет вовсе — сегодняшнее поведение, эталон. Журнал не вырос.
        del settings.KNOWLEDGE_READ_SHADOW_ENABLED
        baseline, baseline_sent = self._turn(monkeypatch, uid="2729-off", service_pk=known.pk)
        assert asked == [VERIFIED_SERVICE]
        assert baseline.knowledge_licence is None

        # 3. Флаг выключен словом. Журнал не вырос.
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "false"
        off, off_sent = self._turn(monkeypatch, uid="2729-false", service_pk=known.pk)
        assert asked == [VERIFIED_SERVICE]
        assert off.knowledge_licence is None

        # 4. Включён, каталог не знает.
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        answer["with"] = lambda request: httpx.Response(200, json=UNKNOWN_BODY)
        on_unknown, unknown_sent = self._turn(monkeypatch, uid="2729-unknown", service_pk=known.pk)
        assert asked == [VERIFIED_SERVICE, VERIFIED_SERVICE]
        assert on_unknown.knowledge_licence.subjects_in(SubjectState.UNKNOWN) == 1

        # 5. Включён, каталог лежит.
        answer["with"] = _down
        on_down, down_sent = self._turn(monkeypatch, uid="2729-down", service_pk=known.pk)
        assert asked == [VERIFIED_SERVICE, VERIFIED_SERVICE, VERIFIED_SERVICE]
        assert on_down.knowledge_licence.subjects_in(SubjectState.UNAVAILABLE) == 1

        # 6. Включён, читатель падает.
        monkeypatch.setattr(kc, "read_subject", Mock(side_effect=RuntimeError("reader bug")))
        crashed, crashed_sent = self._turn(monkeypatch, uid="2729-crash", service_pk=known.pk)
        assert crashed.knowledge_licence is None

        # Эталон — литералом: прозу написала модель, клавиатура ведёт к мастеру.
        assert baseline.text == (
            "У Анны есть окна в четверг — показать время?\n\nИскала по твоим словам: пилинг"
        )
        assert (
            baseline.action_data["attachments"][0]["payload"]["buttons"][0]["callback"]
            == "cb:discover:book:t1:m1:11111111-0000-4000-8000-000000002729"
        )
        assert baseline.persisted is True
        assert len(baseline_sent) == 2

        for name, reply, sent in (
            ("flag=false", off, off_sent),
            ("on+known", on_known, known_sent),
            ("on+unknown", on_unknown, unknown_sent),
            ("on+catalog-down", on_down, down_sent),
            ("on+reader-crash", crashed, crashed_sent),
        ):
            assert self._visible(reply) == self._visible(baseline), name
            # Модель получила РОВНО то же: знание в промпт не попало.
            assert sent == baseline_sent, name

    def test_matrix_no_claim_wording_reaches_the_model_or_the_person(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        reply, sent = self._turn(monkeypatch, uid="2729-words", service_pk=row.pk)

        # Положительный контроль: каталог слова отдал, читатель их получил.
        assert asked == [VERIFIED_SERVICE]
        assert "SYNTHETIC" in json.dumps(KNOWN_BODY)
        assert "SYNTHETIC" not in json.dumps(sent, ensure_ascii=False, default=str)
        assert "SYNTHETIC" not in reply.text
        assert "SYNTHETIC" not in json.dumps(reply.action_data, ensure_ascii=False, default=str)
        licence = reply.knowledge_licence
        assert licence is not None
        assert [claim.claim_id for claim in licence.claims()] == [CAPABILITY_CLAIM, GOAL_LINK_CLAIM]
        assert "SYNTHETIC" not in repr(licence)

    def test_the_shadow_gate_now_sees_real_coverage(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Ради чего лист: затвор оценивает настоящее покрытие, а не ``absent``."""
        settings.CLAIM_GATE_SHADOW_ENABLED = "true"
        caplog.set_level(logging.INFO)
        catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        self._turn(monkeypatch, uid="2729-gate-off", service_pk=row.pk)
        without_reader = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("safety.claim_gate.shadow ")
        ]
        caplog.clear()

        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        catalog()
        self._turn(monkeypatch, uid="2729-gate-on", service_pk=row.pk)
        with_reader = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("safety.claim_gate.shadow ")
        ]

        assert without_reader and all("licence=absent" in line for line in without_reader)
        assert with_reader and all(
            "licence=present subjects_known=1 subjects_unknown=0 subjects_unavailable=0 "
            "claims=2 stale=0" in line
            for line in with_reader
        )

    def test_a_text_only_turn_has_no_reader_even_with_the_flag_on(
        self,
        catalog: Any,
        salon: Tenant,
        settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Спекулятивного чтения нет: ход без разрешённой услуги в каталог не ходит."""
        settings.KNOWLEDGE_READ_SHADOW_ENABLED = "true"
        asked = catalog()
        row = _mirror(salon, "peel", ayla_service_id=VERIFIED_SERVICE)

        reply, _sent = self._turn(
            monkeypatch,
            uid="2729-text",
            service_pk=row.pk,
            results=[_prose("Пилинг выравнивает тон кожи.")],
        )

        assert reply.text == "Пилинг выравнивает тон кожи."
        assert asked == []
        assert reply.knowledge_licence is None
