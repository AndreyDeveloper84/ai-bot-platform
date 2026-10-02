"""DRF-2712: правило обращения стоит в каждом промпте, который отвечает клиенту.

Решение владельца 01.10.2026: Ayla обращается к клиенту на «ты». Готовые тексты
переведены отдельными PR; этот узел держит вторую половину — строку, которая
говорит то же МОДЕЛИ. До неё правила об обращении не было ни в одном промпте, и
модель выбирала форму сама.

ПРЕДЕЛ, тот же, что у запрета внутренних терминов
(``test_no_slot_word_for_people_2593.py``): узел охраняет ОБЕЩАНИЕ от удаления,
но не его ИСПОЛНЕНИЕ. Модель может ослушаться, а в CI модели нет; голос модели
проверяется прогоном на стенде.

Носители перечислены по месту, где системное сообщение уходит модели. Два
промпта-классификатора (``intent_router``, ``intent_resolution``) возвращают
JSON, клиенту не пишут и правила не получают.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from apps.persona.voice import CLIENT_ADDRESS_RULE


class TestTheRuleItself:
    def test_it_names_the_form_literally(self):
        """Решённое слово — литералом: узел из одной константы её смены не поймал бы."""
        assert "на «ты»" in CLIENT_ADDRESS_RULE
        assert "даже если он сам пишет на «вы»" in CLIENT_ADDRESS_RULE


class TestEveryClientPromptCarriesIt:
    def test_concierge(self):
        from apps.orchestrator.concierge import build_concierge_system_prompt

        assert CLIENT_ADDRESS_RULE in build_concierge_system_prompt()

    def test_discovery(self):
        from apps.orchestrator.discovery import build_discovery_prompt

        messages = build_discovery_prompt("хочу маникюр")

        assert messages[0]["role"] == "system"
        assert CLIENT_ADDRESS_RULE in messages[0]["content"]

    def test_booking(self):
        from apps.skills.booking.prompts import BrandVoiceConfig, build_booking_prompt

        messages = build_booking_prompt(
            brand_voice=BrandVoiceConfig(persona="Алина"), query="запишите меня на массаж"
        )

        assert CLIENT_ADDRESS_RULE in messages[0]["content"]

    def test_faq_before_and_after_retrieval(self):
        """У FAQ два вызова на ход — правило нужно в обоих."""
        from apps.skills.faq.prompts import BrandVoiceConfig, build_faq_prompt

        voice = BrandVoiceConfig(persona="Алина, администратор салона")
        first = build_faq_prompt(brand_voice=voice, query="где вы находитесь?")
        grounded = build_faq_prompt(
            brand_voice=voice,
            retrieved_chunks=[{"text": "Адрес: ул. Ленина, 1."}],
            query="где вы находитесь?",
        )

        assert CLIENT_ADDRESS_RULE in first[0]["content"]
        assert CLIENT_ADDRESS_RULE in grounded[0]["content"]


class TestStaffAssistantsDoNotGetIt:
    """На мастера и администратора решение не распространяется.

    Узел не про стиль: строка «к клиенту обращайся на „ты“» в промпте, где
    собеседник — сотрудник, заставила бы модель читать сотрудника как клиента.
    """

    def test_master_assistant(self):
        from apps.master_api.services.assistant import _system_prompt

        prompt = _system_prompt(
            SimpleNamespace(name="Анна"), today=date(2026, 10, 1), tz_label="UTC+3"
        )

        assert "это сотрудник, а не клиент" in prompt
        assert CLIENT_ADDRESS_RULE not in prompt

    def test_admin_assistant(self):
        from apps.admin_api.services.assistant import AdminSubject

        subject = AdminSubject(
            tenant=SimpleNamespace(name="Формула тела", slug="formula"),
            bot_user=SimpleNamespace(id=1, display_name="Ольга"),
            role_ctx=None,
        )
        prompt = subject.system_prompt(today=date(2026, 10, 1), tz_label="UTC+3")

        assert "это сотрудник, а не клиент" in prompt
        assert CLIENT_ADDRESS_RULE not in prompt
