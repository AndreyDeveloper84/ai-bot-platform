"""The half-answered request, end to end (DRF-1312).

The Controlled-Pilot turn of 23.08:

    владелец: «давай будет несколько: массаж классика, и маникюр»
    бот:      «Вот мастера, которые могут подойти:» + пять карточек

Маникюра нет ни у одного салона в контуре. Бот ответил на половину запроса и
про вторую половину промолчал — человек ушёл в уверенности, что нашёл мастера
и на массаж, и на маникюр.

Three outcomes have to be distinguishable, not one:

* both services offered  → the ordinary card list, no extra sentence;
* one offered, one not   → the cards PLUS the missing one, named out loud;
* neither offered        → the existing zero-result path (DRF-1283), which
  already names what was understood and says it is not there.

The salon fixture is synthetic. Only the LLM is faked — tool dispatch, the
cross-tenant carve-out, the coverage check and the renderer are all real,
because the sentence «маникюра нет» is a claim about the CATALOG and a test
that mocked the catalog would prove nothing about it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from django.conf import settings as django_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.marketplace.dto import MasterCard
from apps.orchestrator import concierge as concierge_mod
from apps.orchestrator import discovery as discovery_mod
from apps.orchestrator.discovery import (
    SHOW_MASTERS_TOOL_SPEC,
    _render_master_cards,
    render_missing_services,
    requested_services,
)
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(django_settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; «маникюра нет» would "
        "pass vacuously on SQLite, where nothing matches at all.",
    ),
]

TRACE_ID = str(uuid.uuid4())

# The exact words the owner typed.
LIVE_TURN = "давай будет несколько: массаж классика, и маникюр"


def _ts() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza_contour() -> Tenant:
    """The pilot contour in miniature: massage exists, nails exist nowhere."""
    tenant = Tenant.objects.create(slug="salon-penza-1312", name="SPAtrium", city="Пенза")
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Архипкин Денис",
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
    )
    for name, slug in (("Классический массаж", "klass"), ("Спортивный массаж", "sport")):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=slug,
            name=name,
            is_active=True,
            ayla_service_id=uuid4(),
            external_updated_at=_ts(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return tenant


@pytest.fixture
def penza_with_nails(penza_contour: Tenant) -> Tenant:
    """Same contour, plus a nail master — the «both offered» outcome."""
    master = CatalogMaster.all_tenants.create(
        tenant=penza_contour,
        name="Сазонова Инна",
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
    )
    service = CatalogService.all_tenants.create(
        tenant=penza_contour,
        slug="mani",
        name="Маникюр классический",
        is_active=True,
        ayla_service_id=uuid4(),
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=penza_contour, master=master, service=service)
    return penza_contour


# --------------------------------------------------------------------------
# The tool contract: the model splits, the platform rules.
# --------------------------------------------------------------------------


class TestToolSpec:
    def test_show_masters_takes_a_services_array(self) -> None:
        prop = SHOW_MASTERS_TOOL_SPEC["parameters"]["properties"]["services"]
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}

    def test_the_model_is_told_not_to_judge_availability(self) -> None:
        """OD-9 / AYLA-DEC-0045 stated where the model can read it.

        A model that silently dropped «маникюр» because it assumed we don't do
        nails would make itself the authority on the catalog — and would take
        the fix's input away before the catalog ever got asked.
        """
        description = SHOW_MASTERS_TOOL_SPEC["parameters"]["properties"]["services"]["description"]
        assert "Do NOT judge availability" in description


class TestRequestedServices:
    def test_prefers_the_models_own_split(self) -> None:
        assert requested_services(
            {"services": ["массаж классика", "маникюр"]}, "массаж классика, маникюр"
        ) == ["массаж классика", "маникюр"]

    def test_falls_back_to_splitting_the_specialization(self, penza_contour: Tenant) -> None:
        """A model that filled only the substring still gets checked per part."""
        assert requested_services({}, "массаж классика, маникюр") == [
            "массаж классика",
            "маникюр",
        ]

    def test_a_single_service_asks_nothing(self, penza_contour: Tenant) -> None:
        """Nothing can be half-answered, so no EXISTS is spent on it."""
        assert requested_services({"services": ["массаж"]}, "массаж") == []
        assert requested_services({}, "спортивный массаж") == []

    def test_junk_services_argument_is_ignored(self, penza_contour: Tenant) -> None:
        assert requested_services({"services": "маникюр"}, "спортивный массаж") == []
        assert requested_services({"services": [None, "  "]}, "спортивный массаж") == []


# --------------------------------------------------------------------------
# The wording.
# --------------------------------------------------------------------------


class TestRenderMissingServices:
    def test_names_the_service_in_the_users_own_words(self) -> None:
        line = render_missing_services(["маникюр"])
        assert "«маникюр»" in line
        assert "нет" in line

    def test_plural(self) -> None:
        line = render_missing_services(["маникюр", "педикюр"])
        assert "«маникюр», «педикюр»" in line
        assert "таких услуг" in line

    def test_city_scoped_claim_says_the_city(self) -> None:
        """A city-filtered search can only deny the service IN that city."""
        assert "в городе Пенза" in render_missing_services(["маникюр"], "Пенза")

    def test_nothing_missing_is_no_sentence(self) -> None:
        assert render_missing_services([]) == ""


class TestRenderMasterCards:
    def _card(self) -> MasterCard:
        """The real DTO, not a stand-in.

        The renderer's whole job is to project THIS shape, so a hand-rolled
        namespace here would let a field rename pass the test and break the
        reply.
        """
        return MasterCard(
            tenant_id=uuid4(),
            master_id=uuid4(),
            name="Архипкин Денис",
            specialization="",
            rating=None,
            photo_url="",
            city="Пенза",
        )

    def test_unchanged_when_everything_was_found(self) -> None:
        reply = _render_master_cards([self._card()])
        assert reply.text.startswith("Вот мастера, которые могут подойти:")

    def test_the_refusal_comes_before_the_list(self) -> None:
        """Two reasons, both load-bearing (see the renderer's docstring).

        The reply is clipped from the END, so the one line that must never be
        lost cannot sit at the bottom; and a list read BEFORE the refusal reads
        as an answer to the whole request, which is the bug.
        """
        reply = _render_master_cards(
            [self._card()],
            available_services=["массаж классика"],
            missing_services=["маникюр"],
        )
        lines = reply.text.splitlines()
        assert "«маникюр»" in lines[0]
        assert lines[0].index("нет") < reply.text.index("Архипкин")

    def test_the_header_binds_the_list_to_what_was_found(self) -> None:
        """«Вот мастера, которые могут подойти» would still overclaim here."""
        reply = _render_master_cards(
            [self._card()],
            available_services=["массаж классика"],
            missing_services=["маникюр"],
        )
        assert "«массаж классика»" in reply.text
        assert "Вот мастера, которые могут подойти:" not in reply.text

    def test_buttons_are_unaffected(self) -> None:
        reply = _render_master_cards([self._card()], missing_services=["маникюр"])
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert len(buttons) == 1
        assert buttons[0]["callback"].startswith("cb:discover:book:")


# --------------------------------------------------------------------------
# The live path: the concierge.
# --------------------------------------------------------------------------


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id="drf1312-uid",
        chat_id="drf1312-chat",
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _show_masters(args: dict) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="show_masters", arguments=args)],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _run_concierge(monkeypatch, results: list[CompletionResult], turn: str = LIVE_TURN):
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=results)
    provider.default_completion_model = "gpt-4o-mini"
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge_mod, "get_router", lambda: router)
    bot_user, conversation = _bot_user_and_conversation()
    reply = concierge_mod.generate_concierge_reply(
        turn,
        bot_user=bot_user,
        conversation=conversation,
        trace_id=TRACE_ID,
    )
    return reply, provider


# ``transaction=True``, like the DRF-1266 multipass suite: the concierge runs
# its LLM turn under ``asyncio.run`` and reaches the DB through
# ``sync_to_async``, which deadlocks against the wrapping atomic block a plain
# ``django_db`` mark installs.
@pytest.mark.django_db(transaction=True)
class TestConciergePartialCoverage:
    """The three outcomes, through the path the live turn actually takes."""

    def test_one_offered_one_not_says_so(self, settings, monkeypatch, penza_contour) -> None:
        settings.BOOKING_VIA_AYLA_REST = True
        reply, provider = _run_concierge(
            monkeypatch,
            [
                _show_masters(
                    {
                        "city": "Пенза",
                        "specialization": "массаж классика, маникюр",
                        "services": ["массаж классика", "маникюр"],
                    }
                ),
                _text("не должно понадобиться"),
            ],
        )

        # The sentence the ticket exists for.
        assert "«маникюр»" in reply.text
        assert "нет" in reply.text
        # And the half we CAN serve is still answered.
        assert "Архипкин Денис" in reply.text
        assert reply.action_data is not None

    def test_the_missing_half_is_not_left_to_the_model(
        self, settings, monkeypatch, penza_contour
    ) -> None:
        """Rendered deterministically — asking a model to include a sentence is
        a request, not a guarantee, and this is the sentence that must hold."""
        settings.BOOKING_VIA_AYLA_REST = True
        reply, provider = _run_concierge(
            monkeypatch,
            [
                _show_masters(
                    {
                        "specialization": "массаж классика, маникюр",
                        "services": ["массаж", "маникюр"],
                    }
                )
            ],
        )

        assert provider.complete.await_count == 1  # no follow-up pass spent
        assert "«маникюр»" in reply.text
        assert reply.persisted is True

    def test_both_offered_is_the_ordinary_reply(
        self, settings, monkeypatch, penza_with_nails
    ) -> None:
        """No refusal sentence, and the model still gets its phrasing pass."""
        settings.BOOKING_VIA_AYLA_REST = True
        reply, provider = _run_concierge(
            monkeypatch,
            [
                _show_masters(
                    {
                        "city": "Пенза",
                        "specialization": "массаж, маникюр",
                        "services": ["массаж", "маникюр"],
                    }
                ),
                _text("Вот кто может подойти: Архипкин Денис и Сазонова Инна."),
            ],
            turn="массаж и маникюр",
        )

        assert "такой услуги" not in reply.text
        assert "таких услуг" not in reply.text
        assert provider.complete.await_count == 2

    def test_neither_offered_keeps_the_zero_result_path(
        self, settings, monkeypatch, penza_contour
    ) -> None:
        """Nothing to show → the model phrases the refusal (DRF-1283), but it is
        handed the per-part verdict so it can name BOTH."""
        settings.BOOKING_VIA_AYLA_REST = True
        reply, provider = _run_concierge(
            monkeypatch,
            [
                _show_masters(
                    {
                        "city": "Пенза",
                        "specialization": "маникюр, педикюр",
                        "services": ["маникюр", "педикюр"],
                    }
                ),
                _text("Ни маникюра, ни педикюра у наших мастеров сейчас нет."),
            ],
            turn="маникюр и педикюр",
        )

        assert provider.complete.await_count == 2
        second_pass_prompt = provider.complete.await_args_list[1].args[0][-1]["content"]
        assert "«маникюр»" in second_pass_prompt
        assert "«педикюр»" in second_pass_prompt
        assert "ни у одного мастера" in second_pass_prompt

    def test_services_without_specialization_still_searches(
        self, settings, monkeypatch, penza_contour
    ) -> None:
        """A model that filled only `services` has named criteria.

        Treating that as «no criteria» would answer a perfectly clear composite
        request with «какая услуга нужна?».
        """
        settings.BOOKING_VIA_AYLA_REST = True
        reply, _provider = _run_concierge(
            monkeypatch,
            [_show_masters({"services": ["массаж классика", "маникюр"]})],
        )

        assert "Архипкин Денис" in reply.text
        assert "«маникюр»" in reply.text


# --------------------------------------------------------------------------
# The deterministic branch: it declines instead of guessing.
# --------------------------------------------------------------------------


class TestDirectBranchDeclinesComposite:
    def test_the_live_turn_is_handed_to_the_model(self, penza_contour: Tenant) -> None:
        """`None` → the handler falls through to the concierge.

        This branch forwards the RAW turn as one substring, so it cannot split
        «массаж классика, и маникюр» into names it would be willing to quote
        back — only the model can. Answering here is what produced the silent
        half-answer on 23.08.
        """
        assert (
            concierge_mod.generate_direct_show_masters_reply(LIVE_TURN, trace_id=TRACE_ID) is None
        )

    def test_a_single_service_turn_still_answers_without_a_model(
        self, settings, penza_contour: Tenant
    ) -> None:
        """The cheap path is not sacrificed to the fix."""
        settings.BOOKING_VIA_AYLA_REST = True
        reply = concierge_mod.generate_direct_show_masters_reply(
            "запиши меня на массаж", trace_id=TRACE_ID
        )

        assert reply is not None
        assert "Архипкин Денис" in reply.text

    def test_naming_a_city_is_not_a_composite_request(
        self, settings, penza_contour: Tenant
    ) -> None:
        """«массаж, пенза» is one service in a place — it must not cost an LLM call."""
        settings.BOOKING_VIA_AYLA_REST = True
        reply = concierge_mod.generate_direct_show_masters_reply("массаж, пенза", trace_id=TRACE_ID)

        assert reply is not None
        assert "Архипкин Денис" in reply.text


# --------------------------------------------------------------------------
# The legacy generator keeps parity.
# --------------------------------------------------------------------------


class _Provider:
    default_completion_model = "m"

    def __init__(self, result: CompletionResult) -> None:
        self._result = result

    async def complete(self, messages, model: str = "", tools=None):  # noqa: ANN001
        return self._result


class _Router:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    def get_provider(self, tenant=None, *, skill: str = "", op: str = "complete"):  # noqa: ANN001
        return self._provider


def test_legacy_generator_reports_the_missing_half(
    settings, monkeypatch, penza_contour: Tenant
) -> None:
    """The hand-rolled fallback generator must not disagree with the concierge."""
    settings.BOOKING_VIA_AYLA_REST = True
    result = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="t1",
                name="show_masters",
                arguments={
                    "city": "Пенза",
                    "specialization": "массаж классика, маникюр",
                    "services": ["массаж классика", "маникюр"],
                },
            )
        ],
    )
    monkeypatch.setattr(discovery_mod, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery_mod.generate_discovery_reply(LIVE_TURN)

    assert "«маникюр»" in reply.text
    assert "Архипкин Денис" in reply.text
