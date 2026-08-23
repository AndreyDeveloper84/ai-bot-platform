"""The service menu behind a master tap is the menu of the REQUEST (DRF-1324).

The live Controlled-Pilot turn, 23.08 17:50 — the first booking ever made
through the bot, reconstructed from `conversations_message`:

    владелец: запиши на лимфодренаж?
    бот:      Вот мастера, которые могут подойти:
              • Архипкин Денис · Пенза
              • Сазонова Инна · Пенза
              • Татьяна Паламарчук · Пенза
    [тап по Сазоновой]
    бот:      Выберите услугу мастера Сазонова Инна:
              • Биоэнергетический массаж
              • Биоэнергетический массаж детский      ← tapped
              • Глубокая проработка проблемной зоны (60 минут)
              • Классический массаж
              • Классический массаж задней поверхности тела
              • Лимфодренажный массаж всего тела (60 минут)
              • Лимфодренажный массаж — снятие отёков (экспресс 30 минут)
              • Массаж в 4 руки
              • Массаж ног — глубокое восстановление и лёгкость (60 минут)
              • Массаж ног — глубокое расслабление и лимфодренаж (45 минут)
              Показаны первые 10 услуг — …
    бот:      Готово! Записала. Услуга: Биоэнергетический массаж детский.

The master search was RIGHT: all three masters really do perform lymphatic
drainage, and the two salons that do not were correctly absent. What was
wrong is everything after the tap — nineteen services truncated to ten in
alphabetical order, the two the person asked for sixth and seventh, and the
children's massage second.

The request died at the callback boundary: ``cb:discover:book:{T}:{M}``
carries who, sometimes what, and never why this master was on the list.

Two things are pinned here — the codec that carries the request across that
boundary, and the menu that is narrowed by it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings as dj_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.services import resolve_or_create_global_bot_user
from apps.marketplace.discovery import parse_query, parse_stems, query_stems
from apps.marketplace.dto import MasterCard
from apps.orchestrator.discovery import (
    CALLBACK_DISCOVER_BOOK_PREFIX,
    DiscoveryReply,
    _render_master_cards,
    decode_query_ref,
    encode_query_ref,
)
from apps.orchestrator.handoff import handoff_to_booking
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(dj_settings.DATABASES["default"]["ENGINE"]),
        reason="jsonb containment and Cyrillic ILIKE folding require Postgres.",
    ),
]

_TS = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)

# Сазонова's real roster, in the order the mirror stores it. Nineteen rows,
# and the alphabetical cut at ten is what buried the answer.
_ROSTER = [
    "Биоэнергетический массаж",
    "Биоэнергетический массаж детский",
    "Глубокая проработка проблемной зоны (60 минут)",
    "Классический массаж",
    "Классический массаж задней поверхности тела",
    "Лимфодренажный массаж всего тела (60 минут)",
    "Лимфодренажный массаж — снятие отёков (экспресс 30 минут)",
    "Массаж в 4 руки",
    "Массаж ног — глубокое восстановление и лёгкость (60 минут)",
    "Массаж ног — глубокое расслабление и лимфодренаж (45 минут)",
    "Массаж ног — снятие усталости и отёков (30 минут)",
    "Массаж спины премиум — максимальная проработка (60 минут)",
    "Массаж спины — глубокая проработка (45 минут)",
    "Массаж спины — снятие боли и зажимов (30 минут)",
    "Массаж стоп",
    "Массаж шейно-воротниковой зоны",
    "Парный массаж",
    "Спина без боли - комплекс массажа",
    "Спортивный массаж",
]

_RELAX = [{"key": "relax", "label": "Расслабиться и снять стресс"}]


@pytest.fixture
def sazonova(settings) -> tuple[Tenant, CatalogMaster]:
    settings.BOOKING_VIA_AYLA_REST = True
    tenant = Tenant.objects.create(
        slug="salon-penza", name="Salon Penza", timezone="Europe/Moscow", city="Пенза"
    )
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_TS,
        name="Сазонова Инна",
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    for index, name in enumerate(_ROSTER):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"svc-{index}",
            name=name,
            is_active=True,
            goals=_RELAX if name == "Классический массаж" else [],
            ayla_service_id=uuid4(),
            external_updated_at=_TS,
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return tenant, master


def _lines(reply: DiscoveryReply) -> list[str]:
    return [line[2:] for line in reply.text.splitlines() if line.startswith("• ")]


def _buttons(reply: DiscoveryReply) -> list[dict[str, str]]:
    for attachment in (reply.action_data or {}).get("attachments", []):
        if attachment.get("type") == "inline_keyboard":
            return list(attachment["payload"]["buttons"])
    return []


class TestQueryRefCodec:
    """The callback has to carry the request across a wire of unknown rules."""

    def test_round_trips_the_stems(self) -> None:
        assert decode_query_ref(encode_query_ref("запиши на лимфодренаж?")) == ["лимфод"]

    def test_encoding_reads_no_catalog(self, django_assert_num_queries) -> None:
        """The renderer that calls this is a pure function of its DTOs — three
        suites render cards with no database at all. A querying encoder would
        break every one of them, and that would be a layering error, not a
        test problem."""
        with django_assert_num_queries(0):
            assert encode_query_ref("хочу расслабиться в пензе")

    def test_the_ref_is_ascii_and_colon_free(self) -> None:
        """Every existing ``cb:`` payload is hex. A Cyrillic one would be the
        first on the wire and is not something to discover on a live pilot —
        and a ``:`` inside it would break the handler's colon split."""
        ref = encode_query_ref("хочу лимфодренаж и массаж спины")
        assert ref
        assert ref.isascii() and ":" not in ref and "=" not in ref

    def test_nothing_to_carry_encodes_to_nothing(self) -> None:
        assert encode_query_ref("") == ""
        assert encode_query_ref("   ") == ""

    @pytest.mark.parametrize("ref", ["", "   ", "!!!!", "____", "x" * 200])
    def test_garbage_decodes_to_do_not_narrow(self, ref: str) -> None:
        """A forged, truncated or stale ref must degrade to the full menu —
        never to an empty one, and never to somebody else's service."""
        assert decode_query_ref(ref) == []

    def test_a_hand_written_payload_is_bounded(self) -> None:
        """The tokenizer caps stems at five; a decoder must cap what it
        ACCEPTS on its own, because a payload is not obliged to have come from
        the encoder."""
        import base64

        forged = base64.urlsafe_b64encode(b",".join([b"aaaaaa"] * 40)).decode().rstrip("=")
        assert len(decode_query_ref(forged)) <= 5


class TestTheCatalogHalfRunsAtTheTap:
    """`query_stems` is pure; `parse_stems` is where the catalog gets a say."""

    def test_stems_alone_carry_no_city_knowledge(self) -> None:
        assert query_stems("хочу расслабиться в пензе") == ["рассла", "пензе"]

    def test_the_tap_splits_the_city_and_recognises_the_goal(self, sazonova: tuple) -> None:
        parsed = parse_stems(query_stems("хочу расслабиться в пензе"))
        assert parsed.goals == ["relax"]
        assert parsed.cities == ["Пенза"]
        assert parsed.stems == []

    def test_recognition_off_a_stem_equals_recognition_off_the_word(self, sazonova: tuple) -> None:
        """Stems are cut to the same width the goal comparison cuts to, so a
        request read off a button must mean what it meant when it produced the
        card."""
        for raw in ["хочу расслабиться", "запиши на лимфодренаж?", "снятие отёков"]:
            assert parse_stems(query_stems(raw)) == parse_query(raw)


class TestTheCardCarriesTheRequest:
    def test_a_serviceless_card_sends_an_empty_service_segment(self, sazonova: tuple) -> None:
        """«…:{master}::{ref}», not «…:{master}:{ref}» — the service segment is
        positional, and the handler would otherwise read the ref as a
        malformed service id and lose both."""
        tenant, master = sazonova
        reply = _render_master_cards(
            [
                MasterCard(
                    tenant_id=tenant.id,
                    master_id=master.id,
                    name=master.name,
                    specialization="",
                    rating=None,
                    photo_url="",
                    city="Пенза",
                )
            ],
            specialization="запиши на лимфодренаж?",
        )
        payload = _buttons(reply)[0]["callback"]
        parts = payload[len(CALLBACK_DISCOVER_BOOK_PREFIX) :].split(":")
        assert len(parts) == 4
        assert parts[2] == ""
        assert decode_query_ref(parts[3]) == ["лимфод"]

    def test_a_card_without_a_query_keeps_the_old_shape(self, sazonova: tuple) -> None:
        tenant, master = sazonova
        reply = _render_master_cards(
            [
                MasterCard(
                    tenant_id=tenant.id,
                    master_id=master.id,
                    name=master.name,
                    specialization="",
                    rating=None,
                    photo_url="",
                    city="Пенза",
                )
            ]
        )
        payload = _buttons(reply)[0]["callback"]
        assert payload == f"{CALLBACK_DISCOVER_BOOK_PREFIX}{tenant.id}:{master.id}"


class TestTheMenuIsTheMenuOfTheRequest:
    def _tap(self, tenant, master, monkeypatch, *, ref: str) -> DiscoveryReply:
        gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="1324")
        monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)
        return handoff_to_booking(
            global_bot_user=gbu, tenant_id=tenant.id, master_id=master.id, query_ref=ref
        )

    def test_the_live_turn_now_offers_lymphatic_drainage(
        self, sazonova: tuple, monkeypatch
    ) -> None:
        """The owner's proof, verbatim: лимфодренаж present, детский absent."""
        tenant, master = sazonova
        ref = encode_query_ref("запиши на лимфодренаж?")
        reply = self._tap(tenant, master, monkeypatch, ref=ref)

        assert _lines(reply) == [
            "Лимфодренажный массаж всего тела (60 минут)",
            "Лимфодренажный массаж — снятие отёков (экспресс 30 минут)",
            "Массаж ног — глубокое расслабление и лимфодренаж (45 минут)",
        ]
        assert "Биоэнергетический массаж детский" not in reply.text
        # The buttons must agree with the text, or the keyboard is a different
        # menu from the list — the DRF-1070 loop with a tap instead of a typo.
        assert [b["label"] for b in _buttons(reply)] == _lines(reply)

    def test_the_filtered_note_replaces_the_truncation_note(
        self, sazonova: tuple, monkeypatch
    ) -> None:
        """«Показаны первые N услуг» under a FILTERED list would claim the
        master has N services. Two different statements; only one is true."""
        tenant, master = sazonova
        ref = encode_query_ref("запиши на лимфодренаж?")
        reply = self._tap(tenant, master, monkeypatch, ref=ref)
        assert "Показаны услуги по вашему запросу" in reply.text
        assert "Показаны первые" not in reply.text

    def test_a_goal_request_narrows_the_menu_too(self, sazonova: tuple, monkeypatch) -> None:
        tenant, master = sazonova
        ref = encode_query_ref("хочу расслабиться")
        reply = self._tap(tenant, master, monkeypatch, ref=ref)
        assert _lines(reply) == ["Классический массаж"]

    def test_without_a_ref_the_whole_roster_is_still_offered(
        self, sazonova: tuple, monkeypatch
    ) -> None:
        """The pre-DRF-1324 answer stays reachable — a card rendered before
        this ticket must not break, and its tap must not come back empty."""
        tenant, master = sazonova
        reply = self._tap(tenant, master, monkeypatch, ref="")
        assert _lines(reply) == sorted(_ROSTER)[:10]
        assert "Показаны первые 10 услуг" in reply.text

    def test_a_request_this_master_no_longer_matches_falls_back(
        self, sazonova: tuple, monkeypatch
    ) -> None:
        """A stale ref — the service went inactive, the mirror moved — must not
        strand the tap on an empty menu. The full roster is a worse answer than
        the narrowed one and a far better answer than none."""
        tenant, master = sazonova
        ref = encode_query_ref("маникюр")
        reply = self._tap(tenant, master, monkeypatch, ref=ref)
        assert _lines(reply) == sorted(_ROSTER)[:10]
        assert "Показаны услуги по вашему запросу" not in reply.text

    def test_the_narrowed_buttons_ground_a_real_service(self, sazonova: tuple, monkeypatch) -> None:
        """Each button re-enters the handoff with a resolved service id, so the
        next tap dispatches booking instead of landing back on this reply."""
        tenant, master = sazonova
        ref = encode_query_ref("запиши на лимфодренаж?")
        reply = self._tap(tenant, master, monkeypatch, ref=ref)
        for button in _buttons(reply):
            prefix, _, rest = button["callback"].partition(CALLBACK_DISCOVER_BOOK_PREFIX)
            service_pk = rest.split(":")[2]
            assert CatalogService.all_tenants.filter(id=service_pk, name=button["label"]).exists()
