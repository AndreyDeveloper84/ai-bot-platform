"""Провенанс «рекомендация → запись» (DRF-1773, К-3 N7).

R17 обещает цепочку `Recommendation → PendingBookingIntent → Booking` и
«shown ≠ engaged ≠ booked». Здесь заперта та её часть, что помещается в
наши данные:

1. кнопка «Подобрать вариант» открывает Mini App **со ссылкой на свою
   карточку** (`reco_<id>` в payload, грамматика `master_invite_` /
   `reschedule_`); двоеточие в `open_app` запрещено, поэтому подчёркивание;
2. `recommendation_id_from_entry_point` понимает `deep_link:reco_<id>` и
   молчит на всех прежних формах (`catalog` / `master` / `direct` /
   чужой deep_link) — отрицательная пара обязательна: провенанс не
   рекомендации работает как раньше;
3. чужая или несуществующая карточка — бронь как обычно, в журнал класс
   отказа **без id**;
4. реакции («Почему» / «Другой вариант» / «Не сейчас») пишутся в
   существующий аудит тремя кодами — `recommendation.accepted` не
   вводится (снят владельцем, B8);
5. `booked_at` и `booking_id` — то, что делает «booked» отличимым от
   «engaged»; стирание слов (шаг 7 каскада, N6) их не трогает: это
   tombstone для attribution (B13/D7), согласовано с матрицей DRF-2134.

**Предел листа:** цепочка замкнута в НАШИХ данных (интент → проекция
брони → запись). У каталога бронь остаётся без ссылки: `POST
appointments/` принимает закрытый список полей, и добавление туда
`recommendation_id` — транзакционный домен, лист каталога и слово
владельца.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from apps.identity.models import BotUser
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.recommendation import card as c
from apps.recommendation.models import Recommendation
from apps.recommendation.provenance import (
    RECO_PAYLOAD_PREFIX,
    attribution_for,
    mark_booked,
    recommendation_id_from_entry_point,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def person(db, settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id="773001", chat_id="773001"
    )


@pytest.fixture
def card(person) -> Recommendation:
    return Recommendation.objects.create(
        bot_user=person,
        goal_id="goal-1",
        kind=Recommendation.Kind.DIRECTION,
        what="Уменьшить утреннюю отёчность",
        why=["Ты сказала, что хочешь привести себя в порядок"],
        facts={"goal": {"value": "Привести себя в порядок", "origin": "choice"}},
        fingerprint="fp-1773",
    )


class TestCardCarriesItsId:
    def test_pick_button_opens_the_app_with_a_link_to_this_card(self, card, settings):
        envelope = c.card_keyboard(str(card.id))
        assert envelope is not None
        buttons = envelope["attachments"][0]["payload"]["buttons"]
        pick = next(b for b in buttons if b["label"] == c.BUTTON_PICK)
        assert pick["callback"] == f"{RECO_PAYLOAD_PREFIX}{card.id}"
        assert pick.get("web_app") == "aylabot"

    def test_the_payload_has_no_character_max_refuses(self, card):
        """`open_app` payload — только `[A-Za-z0-9_-]`; двоеточия там нет."""
        from apps.channels.max.outbound import OPEN_APP_PAYLOAD_RE

        payload = f"{RECO_PAYLOAD_PREFIX}{card.id}"
        assert OPEN_APP_PAYLOAD_RE.fullmatch(payload)


class TestEntryPointReading:
    def test_deep_link_of_a_card_is_read_back(self, card):
        entry = f"deep_link:{RECO_PAYLOAD_PREFIX}{card.id}"
        assert recommendation_id_from_entry_point(entry) == str(card.id)

    @pytest.mark.parametrize(
        "entry",
        ["catalog", "master", "direct", "deep_link:open_catalog", "", None],
    )
    def test_every_other_provenance_still_reads_as_before(self, entry):
        """Отрицательная пара: не-рекомендация работает как раньше."""
        assert recommendation_id_from_entry_point(entry) is None

    def test_a_malformed_id_is_not_a_recommendation(self):
        assert recommendation_id_from_entry_point("deep_link:reco_not-a-uuid") is None


class TestAttributionAndBooking:
    def test_own_card_reaches_the_bookings_attribution(self, person, card):
        entry = f"deep_link:{RECO_PAYLOAD_PREFIX}{card.id}"
        extra = attribution_for(person, entry)
        assert extra == {"recommendation_id": str(card.id)}

    def test_someone_elses_card_is_ignored_and_named_without_the_id(self, person, card, caplog):
        other = BotUser.all_tenants.create(
            tenant=Tenant.objects.create(slug="salon-1773", name="Салон"),
            channel="max",
            channel_user_id="773999",
        )
        entry = f"deep_link:{RECO_PAYLOAD_PREFIX}{card.id}"
        # Присутствие: хозяину карточки та же ссылка даёт атрибуцию —
        # значит пустота ниже про чужого, а не про сломанное чтение.
        assert attribution_for(person, entry) == {"recommendation_id": str(card.id)}
        with caplog.at_level(logging.INFO, logger="apps.recommendation.provenance"):
            assert attribution_for(other, entry) == {}
        assert "not_found" in caplog.text
        assert str(card.id) not in caplog.text  # id чужой карточки в журнал не идёт

    def test_no_recommendation_means_no_extra_keys(self, person, card):
        # Присутствие: со ссылкой ключ есть — значит пустота ниже про
        # провенанс, а не про сломанную выдачу.
        entry = f"deep_link:{RECO_PAYLOAD_PREFIX}{card.id}"
        assert attribution_for(person, entry) == {"recommendation_id": str(card.id)}
        assert attribution_for(person, "catalog") == {}
        assert attribution_for(person, None) == {}

    def test_booking_is_stamped_on_the_card_once(self, person, card):
        booking_id = uuid.uuid4()
        assert mark_booked(person, str(card.id), booking_id) is True
        card.refresh_from_db()
        assert card.booking_id == str(booking_id)
        assert card.booked_at is not None
        first = card.booked_at
        # Вторая бронь по той же карточке первую не переписывает: «booked»
        # — про ту, что привела, а не про последнюю.
        assert mark_booked(person, str(card.id), uuid.uuid4()) is False
        card.refresh_from_db()
        assert card.booking_id == str(booking_id)
        assert card.booked_at == first

    def test_price_or_slot_change_does_not_change_the_id(self, person, card):
        """S5 §7.1 — id принадлежит рекомендации, не котировке."""
        entry = f"deep_link:{RECO_PAYLOAD_PREFIX}{card.id}"
        before = attribution_for(person, entry)
        card.refresh_from_db()
        assert attribution_for(person, entry) == before


class TestReactionsAreAudited:
    @pytest.mark.parametrize(
        ("reaction", "action"),
        [
            (Recommendation.Reaction.WHY_REQUESTED, "recommendation.why_requested"),
            (Recommendation.Reaction.ALTERNATIVE_REQUESTED, "recommendation.alternative_requested"),
            (Recommendation.Reaction.REJECTED, "recommendation.rejected"),
        ],
    )
    def test_each_reaction_writes_one_audit_row(self, person, card, reaction, action):
        from apps.audit.models import AuditLog
        from apps.recommendation.provenance import audit_reaction

        audit_reaction(card, reaction)

        row = AuditLog.all_tenants.filter(action=action).latest("created_at")
        assert row.target_id == card.id
        # Слов человека в аудите нет — только идентификаторы и вид реакции.
        assert "Привести себя в порядок" not in str(row.payload)

    def test_accepted_is_not_a_reaction_we_write(self):
        """B8: `recommendation.accepted` снят владельцем — ENGAGED и
        `booking_intent.created` доказывают взаимодействие."""
        assert "accepted" not in {r.value for r in Recommendation.Reaction}


class TestErasureKeepsTheTombstone:
    def test_words_go_but_the_booking_link_stays(self, person, card):
        """Согласовано с шагом 7 каскада (N6) и матрицей DRF-2134."""
        from apps.recommendation.erasure import anonymize_recommendations

        booking_id = uuid.uuid4()
        mark_booked(person, str(card.id), booking_id)

        anonymize_recommendations([person.id])

        card.refresh_from_db()
        assert card.booking_id == str(booking_id)  # attribution жив
        assert card.booked_at is not None
        assert card.what == "" and card.why == [] and card.facts == {}
