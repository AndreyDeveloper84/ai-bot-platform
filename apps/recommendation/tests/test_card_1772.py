"""Карточка C04 «направление + почему» — сборка и рендер (DRF-1772, К-3).

Что заперто:

1. WHAT — только из `known.goal.direction` (таблица владельца); нет строки →
   карточки нет;
2. WHY — только пересказ фактов документа (подпись цели, ответы шагов);
   «Не знаю» — не причина; ≤3; ноль причин → карточки нет (OD_C04 §2);
3. сторож R11/§60: цена, услуга каталога, «рекоменду…» в тексте → карточки нет;
4. рендер C04.1 — заголовки и кнопки дословно с макета DRF-1270;
5. C04.4 — текст владельца 12.09 и два действия — паритет с
   `apps/miniapp/src/lib/recommendation-absence.ts` (общий словарь).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings

from apps.recommendation import card as c

REPO_ROOT = Path(__file__).resolve().parents[3]


def _goal(**over):
    goal = {
        "id": "goal-1",
        "goal_key": "self_care",
        "goal_text": None,
        "label": "Привести себя в порядок",
        "direction": {
            "what": "Уменьшить утреннюю отёчность и вернуть более свежий вид",
            "subline": "Сфокусируемся на этом — это даст тебе максимальный эффект сейчас.",
            "area_key": "face",
        },
        "answers": [
            {"step": "area", "label": "Лицо и кожа", "option_key": "face", "unknown": False},
            {
                "step": "feeling",
                "label": "Отдохнувшей, Спокойнее",
                "option_keys": ["rested", "calmer"],
                "unknown": False,
            },
        ],
    }
    goal.update(over)
    return goal


def _doc(goal=None, **over):
    doc = {
        "version": 2,
        "known": {"goal": _goal() if goal is None else goal, "anketa": []},
        "missing": [],
        "suggestions": [],
        "intents": [],
        "next": {"id": "return_to_chat", "label": "Вернуться в чат"},
    }
    doc.update(over)
    return doc


class TestBuild:
    def test_card_from_direction_and_facts(self):
        draft = c.build_card(_doc())
        assert draft is not None
        assert draft.what == "Уменьшить утреннюю отёчность и вернуть более свежий вид"
        assert draft.why == (
            "Ты сказала, что хочешь привести себя в порядок",
            "Ты выбрала: лицо и кожа",
            "Хочешь чувствовать себя: отдохнувшей, Спокойнее",
        )
        # Provenance: из чего собрано — рядом.
        # DRF-1771 — у каждого факта своё происхождение: тут всё выбрано
        # из предложенного, слов человека нет.
        assert draft.facts == {
            "goal": {"value": "Привести себя в порядок", "origin": c.ORIGIN_CHOICE},
            "area": {"value": "Лицо и кожа", "origin": c.ORIGIN_CHOICE},
            "feeling": {"value": "Отдохнувшей, Спокойнее", "origin": c.ORIGIN_CHOICE},
        }
        assert draft.goal_id == "goal-1"

    def test_no_direction_means_no_card(self):
        assert c.build_card(_doc(_goal(direction=None))) is None

    def test_empty_what_means_no_card(self):
        assert c.build_card(_doc(_goal(direction={"what": "  ", "subline": ""}))) is None

    def test_no_goal_means_no_card(self):
        assert c.build_card(_doc(goal=None, known={"goal": None, "anketa": []})) is None

    def test_dont_know_is_not_a_reason(self):
        goal = _goal(
            answers=[
                {"step": "area", "label": "Не знаю", "option_key": "unknown", "unknown": True},
            ]
        )
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert draft.why == ("Ты сказала, что хочешь привести себя в порядок",)

    def test_zero_grounded_reasons_means_no_card(self):
        """OD_C04 §2 — правило кода: без причин карточки нет, даже с направлением."""
        goal = _goal(label="", goal_text=None, answers=[])
        assert c.build_card(_doc(goal)) is None

    def test_unknown_step_makes_no_reason(self):
        goal = _goal(answers=[{"step": "deadline", "label": "К лету", "unknown": False}])
        draft = c.build_card(_doc(goal))
        assert draft is not None
        assert draft.why == ("Ты сказала, что хочешь привести себя в порядок",)

    def test_at_most_three_reasons(self):
        assert c.MAX_REASONS == 3
        draft = c.build_card(_doc())
        assert draft is not None
        assert len(draft.why) <= 3

    def test_fingerprint_is_stable_for_the_same_context(self):
        a, b = c.build_card(_doc()), c.build_card(_doc())
        assert a is not None and b is not None
        assert a.fingerprint == b.fingerprint
        other = c.build_card(_doc(_goal(direction={"what": "Другое", "subline": ""})))
        assert other is not None and other.fingerprint != a.fingerprint


class TestBoundary:
    @pytest.mark.parametrize(
        "what",
        [
            # §60 дословно: рекомендация УСЛУГИ. Родовое имя ловится
            # словарём и не зависит от того, доступен ли каталог.
            "Ayla рекомендует услугу «Массаж»",
            "Ayla рекомендует мастера",
            "Курс за 1500 ₽",
            "Стоимость по запросу",
        ],
    )
    def test_recommends_or_price_in_what_refuses_the_card(self, what):
        assert c.build_card(_doc(_goal(direction={"what": what, "subline": ""}))) is None

    def test_the_word_alone_is_not_a_violation_any_more(self):
        """Сторож сужен по ПРЕДМЕТУ (решение главного окна, К-3 N4).

        До N4 запрещено было слово «рекоменду…» само по себе. Оно стоит в
        заголовке макета C04.3 «Основной вариант (рекомендую):» — и это
        про НАПРАВЛЕНИЕ, а §60 запрещает «Ayla рекомендует услугу X».
        Правило не ослаблено: предмет проверяется тем же сторожем, что и
        опции C02, — см. `test_alternatives_1770.py`.
        """
        draft = c.build_card(
            _doc(_goal(direction={"what": "Рекомендую начать с лица", "subline": ""}))
        )
        assert draft is not None
        assert draft.what == "Рекомендую начать с лица"

    def test_price_in_subline_refuses_the_card(self):
        goal = _goal(direction={"what": "Свежий вид", "subline": "от 2000 руб"})
        assert c.build_card(_doc(goal)) is None

    @pytest.mark.django_db
    @pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="поиск по имени услуги — кириллический ILIKE, только Postgres",
    )
    def test_service_name_as_direction_refuses_the_card(self):
        """R11: направление — не услуга. Та же проверка, что стережёт C02."""
        from apps.catalog.models import CatalogMaster, CatalogService, MasterService
        from apps.tenancy.models import Tenant

        salon = Tenant.objects.create(slug="salon-reco", name="Салон", city="Пенза")
        ts = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        svc = CatalogService.all_tenants.create(
            tenant=salon,
            slug="svc-" + hashlib.sha256(b"massage-reco").hexdigest()[:12],
            name="Классический массаж",
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

        assert (
            c.build_card(_doc(_goal(direction={"what": "Классический массаж", "subline": ""})))
            is None
        )


class TestRender:
    def test_card_text_follows_the_mock(self):
        draft = c.build_card(_doc())
        assert draft is not None
        text = c.render_card_text(draft)
        lines = text.split("\n")
        assert lines[0] == "Моё лучшее направление для тебя:"
        assert lines[1] == draft.what
        assert lines[2] == draft.subline
        assert lines[3] == ""
        assert lines[4] == "Почему это подходит тебе"
        assert lines[5:] == [f"✓ {r}" for r in draft.why]
        # §60: «Ayla рекомендует» не звучит; R11: ни услуги, ни цены.
        assert not re.search(r"рекоменду", text, re.IGNORECASE)
        assert not c.boundary_violation(text)

    def test_why_more_uses_the_c04_2_heading(self):
        draft = c.build_card(_doc())
        assert draft is not None
        text = c.render_why_more_text(draft)
        assert text.startswith("Почему это важно для тебя\n")
        assert text.count("✓") == len(draft.why)

    def test_buttons_are_verbatim_and_carry_the_id(self, settings):
        settings.MAX_BOT_WEB_APP = "aylabot"
        rid = "11111111-1111-1111-1111-111111111111"
        envelope = c.card_keyboard(rid)
        assert envelope is not None
        buttons = [
            b
            for row in envelope["attachments"][0]["payload"]["buttons"]
            for b in (row if isinstance(row, list) else [row])
        ]
        labels = [b["label"] for b in buttons]
        assert labels == ["Подобрать вариант", "Почему", "Другой вариант", "Не сейчас"]
        by_label = {b["label"]: b for b in buttons}
        assert by_label["Почему"]["callback"] == f"cb:reco:why:{rid}"
        assert by_label["Другой вариант"]["callback"] == f"cb:reco:alt:{rid}"
        assert by_label["Не сейчас"]["callback"] == f"cb:reco:skip:{rid}"
        # До К-4 «Подобрать вариант» открывает Mini App (каталог) — отступление.
        assert by_label["Подобрать вариант"].get("web_app") == "aylabot"

    def test_absence_keyboard_has_the_two_owner_actions(self, settings):
        settings.MAX_BOT_WEB_APP = "aylabot"
        envelope = c.absence_keyboard()
        assert envelope is not None
        buttons = [
            b
            for row in envelope["attachments"][0]["payload"]["buttons"]
            for b in (row if isinstance(row, list) else [row])
        ]
        assert [b["label"] for b in buttons] == ["Посмотреть услуги", "Уточнить запрос"]


class TestParityWithMiniApp:
    def test_absence_copy_matches_recommendation_absence_ts(self):
        ts = (
            REPO_ROOT / "apps" / "miniapp" / "src" / "lib" / "recommendation-absence.ts"
        ).read_text(encoding="utf-8")
        m = re.search(r'NO_VERIFIED_EVIDENCE_TEXT =\s*((?:\s*"[^"]*"\s*\+?)+);', ts)
        assert m, "NO_VERIFIED_EVIDENCE_TEXT not found"
        joined = "".join(re.findall(r'"([^"]*)"', m.group(1)))
        assert joined == c.NO_VERIFIED_EVIDENCE_TEXT
        assert f'ACTION_SHOW_SERVICES = "{c.ACTION_SHOW_SERVICES}"' in ts
        assert f'ACTION_CLARIFY_REQUEST = "{c.ACTION_CLARIFY_REQUEST}"' in ts
