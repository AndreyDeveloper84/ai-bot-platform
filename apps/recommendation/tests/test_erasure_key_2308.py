"""DRF-2308 — удаление аккаунта стирает и ключ цели, и отпечаток карточки.

Шаг 7 каскада C5 (``anonymize_recommendations``) обнулял слова карточки, но
оставлял два поля, которые держат знание о цели человека:

* ``goal_id`` — ключ цели каталога; «забудь всё» (#1980) его стирает, а
  удаление аккаунта — нет, то есть удаление ⊉ «забудь всё»;
* ``fingerprint`` — у карточки «нет рекомендации» это ``absence:<goal_id>``
  открытым текстом, у карточки-направления — несолёный sha256 от цели, WHAT и
  причин: словарь мал (курируемая таблица, шаблоны причин), перебором
  восстанавливается. Тот же класс, что хеш как связь (DRF-2242).

``fingerprint`` уникален вместе с человеком (дедуп «показать один раз»),
поэтому стирается не в пустоту, а в ``erased:<id>`` — уникально и без
содержания. Строка остаётся tombstone для атрибуции (B13): реакция, бронь.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.recommendation.erasure import anonymize_recommendations
from apps.recommendation.models import Recommendation
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def person() -> BotUser:
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id="230801", chat_id="230801"
    )


def _direction(bot_user: BotUser, fingerprint: str = "a" * 64) -> Recommendation:
    return Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="goal-weight",
        kind=Recommendation.Kind.DIRECTION,
        what="Уменьшить утреннюю отёчность",
        why=["Ты сказала, что хочешь привести себя в порядок"],
        facts={"goal": "Привести себя в порядок"},
        fingerprint=fingerprint,
        reaction=Recommendation.Reaction.WHY_REQUESTED,
        booking_id=str(uuid.uuid4()),
    )


def _absence(bot_user: BotUser) -> Recommendation:
    return Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="goal-weight",
        kind=Recommendation.Kind.ABSENCE,
        fingerprint="absence:goal-weight",
    )


class TestTheGoalKeyGoes:
    def test_goal_id_and_fingerprint_are_erased_the_tombstone_stays(self, person) -> None:
        card = _direction(person)
        booking = card.booking_id

        anonymize_recommendations([person.id])

        card.refresh_from_db()
        assert card.goal_id == ""
        assert card.fingerprint == f"erased:{card.id}"
        assert card.what == "" and card.why == [] and card.facts == {}
        assert card.reaction == Recommendation.Reaction.WHY_REQUESTED  # tombstone жив
        assert card.booking_id == booking

    def test_the_absence_card_loses_the_goal_in_clear_text(self, person) -> None:
        card = _absence(person)
        assert "goal-weight" in card.fingerprint  # присутствие: ключ открытым текстом

        anonymize_recommendations([person.id])

        card.refresh_from_db()
        assert "goal-weight" not in card.fingerprint
        assert card.fingerprint == f"erased:{card.id}"

    def test_several_cards_stay_unique(self, person) -> None:
        """``(bot_user, fingerprint)`` уникален — пустой отпечаток у двух карточек упал бы."""
        first = _direction(person, "a" * 64)
        second = _absence(person)

        anonymize_recommendations([person.id])

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.fingerprint != second.fingerprint
        assert {first.fingerprint, second.fingerprint} == {
            f"erased:{first.id}",
            f"erased:{second.id}",
        }

    def test_repeat_is_harmless(self, person) -> None:
        card = _direction(person)
        anonymize_recommendations([person.id])
        anonymize_recommendations([person.id])
        card.refresh_from_db()
        assert card.fingerprint == f"erased:{card.id}"
        assert card.goal_id == ""

    def test_a_neighbour_keeps_everything(self, person) -> None:
        other = BotUser.all_tenants.create(
            tenant=Tenant.objects.create(slug="reco-2308", name="S"),
            channel="max",
            channel_user_id="230899",
        )
        mine = _direction(person)
        theirs = _direction(other, "b" * 64)

        anonymize_recommendations([person.id])

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.goal_id == ""  # положительно: своё стёрто
        assert theirs.goal_id == "goal-weight"
        assert theirs.fingerprint == "b" * 64
