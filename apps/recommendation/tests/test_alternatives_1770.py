"""C04.2 «другие подходы» и сторож по предмету (DRF-1770, К-3 N4).

Что заперто:

1. основной вариант + **до двух** других подходов из той же курируемой
   таблицы (R05); третьего не бывает ни при каких данных, и строка,
   нарушившая границу, из других подходов выпадает;
2. кадр C04.2 по макету DRF-1270 дословно: «Есть несколько подходящих
   направлений. Что для тебя важнее?», «Основной вариант (рекомендую):»,
   «Другие подходы (тоже подойдут):»; кнопки «Показать больше подходов»
   нет — больше двух не бывает по построению, и обещать продолжение
   значило бы соврать;
3. выбор подхода — НОВАЯ карточка с той же целью и теми же причинами,
   прежнее направление встаёт в «другие подходы»; запись immutable (B13).
   Чужой индекс — `stale`, без второй записи; тот же выбор дважды — та же
   запись;
4. **сторож сужен по предмету, а не ослаблен** (решение главного окна):
   «рекоменду…» запрещено рядом с услугой/мастером/салоном/ценой (§60
   «Ayla рекомендует услугу X»), а не как слово; заголовок кадра про
   НАПРАВЛЕНИЕ законен и стоит в allow-list одной константой. Родовое имя
   («услугу», «мастера») ловится словарём и не зависит от базы; конкретное
   имя («массаж») знает только каталог — тем же сторожем, что стережёт
   опции C02;
5. **новая стража словаря отладки**: `ELIG_`/`MATCH_`/score/«уверенность»/
   «балл» в тексте карточки → карточки нет (R06). Раньше правило
   держалось договорённостью — grep по репозиторию давал ноль проверок;
6. других подходов нет — прежняя честная заглушка N6; направления нет
   вовсе — прежний C04.4.

**Предел:** «Показать больше причин» (C04.3) остаётся отсутствием —
дополнительных причин нет до `evidence_refs` (D11), тот же предел, что
назван в N5.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.recommendation import card as c
from apps.recommendation import taps
from apps.recommendation.dispatch import maybe_send_card
from apps.recommendation.models import Recommendation

_PRIMARY = "Уменьшить утреннюю отёчность"
_SECOND = "Вернуть лёгкость"
_THIRD = "Общий уход"


def _goal(directions=None, **over):
    goal = {
        "id": "goal-1",
        "goal_key": "self_care",
        "goal_text": None,
        "label": "Привести себя в порядок",
        "direction": {"what": _PRIMARY, "subline": "Сфокусируемся на этом.", "area_key": "face"},
        "directions": [
            {"what": _PRIMARY, "subline": "Сфокусируемся на этом.", "area_key": "face"},
            {"what": _SECOND, "subline": "Про тело.", "area_key": "body"},
            {"what": _THIRD, "subline": "", "area_key": None},
        ]
        if directions is None
        else directions,
        "answers": [
            {"step": "area", "label": "Лицо и кожа", "option_key": "face", "unknown": False}
        ],
    }
    goal.update(over)
    return goal


def _doc(goal=None):
    return {
        "version": 2,
        "known": {"goal": _goal() if goal is None else goal, "anketa": []},
        "missing": [],
        "suggestions": [],
        "intents": [],
        "next": {"id": "return_to_chat", "label": "Вернуться в чат"},
    }


class TestAlternativesInTheDraft:
    def test_primary_and_two_others(self):
        draft = c.build_card(_doc())
        assert draft is not None
        assert draft.what == _PRIMARY
        assert [a["what"] for a in draft.alternatives] == [_SECOND, _THIRD]

    def test_never_a_third_alternative(self):
        goal = _goal(
            directions=[
                {"what": f"Направление {i}", "subline": "", "area_key": None} for i in range(6)
            ]
        )
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert len(draft.alternatives) == c.MAX_ALTERNATIVES == 2

    def test_a_single_direction_has_no_alternatives(self):
        goal = _goal(
            directions=[{"what": "Одно", "subline": "", "area_key": None}],
            direction={"what": "Одно", "subline": "", "area_key": None},
        )
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert draft.what == "Одно"
        assert draft.alternatives == []

    def test_an_old_document_without_the_field_still_builds(self):
        """Документ каталога до выкладки N4: `directions` нет вовсе."""
        goal = _goal()
        del goal["directions"]
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert draft.what == _PRIMARY
        assert draft.alternatives == []

    def test_an_alternative_that_breaks_the_boundary_is_dropped(self):
        goal = _goal(
            directions=[
                {"what": _PRIMARY, "subline": "Сфокусируемся на этом.", "area_key": "face"},
                {"what": "Курс за 5000 ₽", "subline": "", "area_key": None},
                {"what": _SECOND, "subline": "", "area_key": None},
            ]
        )
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert [a["what"] for a in draft.alternatives] == [_SECOND]


class TestC042Frame:
    def test_frame_follows_the_mock(self):
        draft = c.build_card(_doc())
        assert draft is not None
        text = c.render_alternatives_text(draft)
        lines = [line for line in text.split("\n") if line]
        assert lines[0] == "Есть несколько подходящих направлений. Что для тебя важнее?"
        assert lines[1] == "Основной вариант (рекомендую):"
        assert lines[2] == draft.what
        assert "Другие подходы (тоже подойдут):" in lines
        assert _SECOND in text
        assert _THIRD in text

    def test_a_button_per_approach_and_not_one_more(self):
        draft = c.build_card(_doc())
        assert draft is not None
        envelope = c.alternatives_keyboard("11111111-1111-1111-1111-111111111111", draft)
        assert envelope is not None
        buttons = envelope["attachments"][0]["payload"]["buttons"]
        assert [b["label"] for b in buttons] == [_SECOND, _THIRD]
        assert [b["callback"] for b in buttons] == [
            "cb:reco:pick:11111111-1111-1111-1111-111111111111:0",
            "cb:reco:pick:11111111-1111-1111-1111-111111111111:1",
        ]
        # «Показать больше подходов» не рисуем: больше двух не бывает.
        assert not any("больше" in b["label"].lower() for b in buttons)


class TestGuardsNarrowedBySubject:
    def test_the_mock_heading_is_allowed(self):
        """«Основной вариант (рекомендую):» — про направление, не про услугу."""
        assert c.boundary_violation(c.ALT_PRIMARY_HEAD) is None

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("Ayla рекомендует услугу «Массаж»", "recommends_service"),
            ("рекомендую мастера", "recommends_master"),
            ("рекомендую салон рядом", "recommends_salon"),
            ("рекомендую курс за 5000 ₽", "recommends_price"),
        ],
    )
    def test_recommending_a_bookable_is_still_refused(self, text, reason):
        """§60: запрещён ПРЕДМЕТ рекомендации, и он назван своим именем."""
        assert c.boundary_violation(text) == reason

    @pytest.mark.parametrize(
        "text",
        ["Рекомендую начать с малого", "Рекомендую начать с лица"],
    )
    def test_a_bare_word_about_a_direction_is_not_a_violation(self, text):
        assert c.boundary_violation(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "ELIG_EXCLUDED_SAFETY",
            "MATCH_SERVICE_EXACT",
            "score 0.87",
            "уверенность 90%",
            "балл соответствия высокий",
        ],
    )
    def test_the_code_dictionary_never_reaches_the_person(self, text):
        """Новая стража: раньше правило держалось договорённостью."""
        assert c.boundary_violation(text) == "debug_vocabulary"
        assert c.build_card(_doc(_goal(direction={"what": text, "subline": ""}))) is None


@pytest.mark.django_db
@pytest.mark.skipif(
    "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
    reason="поиск по имени услуги — кириллический ILIKE, только Postgres",
)
class TestRecommendingACatalogServiceByName:
    """Конкретное имя услуги знает только каталог — и сторож его спрашивает."""

    def test_recommending_a_service_by_its_catalog_name_is_refused(self):
        from apps.catalog.models import CatalogMaster, CatalogService, MasterService
        from apps.tenancy.models import Tenant

        salon = Tenant.objects.create(slug="salon-alt", name="Салон", city="Пенза")
        ts = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        svc = CatalogService.all_tenants.create(
            tenant=salon,
            slug="svc-" + hashlib.sha256(b"massage-alt").hexdigest()[:12],
            name="Массаж",
            is_active=True,
            external_updated_at=ts,
        )
        master = CatalogMaster.all_tenants.create(
            tenant=salon,
            external_updated_at=ts,
            name="Анна",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            ayla_user_id=uuid4(),
        )
        MasterService.all_tenants.create(tenant=salon, master=master, service=svc)

        assert c.boundary_violation("рекомендую массаж") == "recommends_service"


@pytest.mark.django_db
class TestTappingAnotherApproach:
    @pytest.fixture
    def global_user(self, settings):
        settings.MAX_BOT_WEB_APP = "aylabot"
        return resolve_or_create_global_bot_user(
            channel="max", channel_user_id="777011", chat_id="777011"
        )

    @pytest.fixture
    def sent(self, monkeypatch):
        calls: list[dict] = []

        def fake_send(
            *, chat_id=None, user_id=None, text, attachments=None, timeout=10.0, bot=None
        ):
            calls.append({"text": text})
            return {"ok": True}

        monkeypatch.setattr("apps.channels.max.outbound.send_message", fake_send)
        return calls

    @pytest.fixture
    def shown(self, global_user, sent) -> Recommendation:
        record = maybe_send_card(global_user, _doc())
        assert record is not None
        assert [a["what"] for a in record.alternatives] == [_SECOND, _THIRD]
        return record

    def test_alt_shows_the_c043_frame(self, global_user, shown):
        reply = taps.route_recommendation_callback(
            global_bot_user=global_user, callback_text=f"{c.RECO_ALT_PREFIX}{shown.id}"
        )
        assert c.ALT_HEAD in reply.text
        assert c.ALT_PRIMARY_HEAD in reply.text
        assert _SECOND in reply.text
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["label"] for b in buttons] == [_SECOND, _THIRD]

    def test_without_approaches_the_honest_stub_stays(self, global_user, sent):
        goal = _goal(
            directions=[{"what": "Одно", "subline": "", "area_key": None}],
            direction={"what": "Одно", "subline": "", "area_key": None},
        )
        record = maybe_send_card(global_user, _doc(goal))
        assert record is not None
        reply = taps.route_recommendation_callback(
            global_bot_user=global_user, callback_text=f"{c.RECO_ALT_PREFIX}{record.id}"
        )
        assert reply.text == taps.ALT_STUB_TEXT
        assert reply.action_data is None

    def test_picking_an_approach_makes_a_new_card_and_keeps_the_old_one(self, global_user, shown):
        reply = taps.route_recommendation_callback(
            global_bot_user=global_user, callback_text=f"{c.RECO_PICK_PREFIX}{shown.id}:0"
        )
        assert _SECOND in reply.text
        picked = Recommendation.objects.exclude(id=shown.id).get(
            bot_user=global_user, kind=Recommendation.Kind.DIRECTION
        )
        assert picked.what == _SECOND
        # Прежнее направление никуда не делось — ни из базы, ни из кадра.
        shown.refresh_from_db()
        assert shown.what == _PRIMARY
        assert shown.reaction == Recommendation.Reaction.ALTERNATIVE_REQUESTED
        assert [a["what"] for a in picked.alternatives] == [_PRIMARY, _THIRD]
        # Кнопки новой карточки ведут на неё, а не на прежнюю.
        assert reply.action_data is not None
        callbacks = [
            b.get("callback", "") for b in reply.action_data["attachments"][0]["payload"]["buttons"]
        ]
        assert any(str(picked.id) in cb for cb in callbacks)

    def test_the_same_pick_twice_is_the_same_record(self, global_user, shown):
        for _ in range(2):
            taps.route_recommendation_callback(
                global_bot_user=global_user, callback_text=f"{c.RECO_PICK_PREFIX}{shown.id}:0"
            )
        assert Recommendation.objects.filter(bot_user=global_user).count() == 2

    def test_an_index_that_was_never_shown_is_stale(self, global_user, shown):
        reply = taps.route_recommendation_callback(
            global_bot_user=global_user, callback_text=f"{c.RECO_PICK_PREFIX}{shown.id}:7"
        )
        assert reply.text == taps.STALE_TEXT
        assert Recommendation.objects.filter(bot_user=global_user).count() == 1


class TestStillHolds:
    def test_no_direction_is_still_the_absence_frame(self):
        goal = _goal(direction=None, directions=[])
        assert c.build_card(_doc(goal)) is None
