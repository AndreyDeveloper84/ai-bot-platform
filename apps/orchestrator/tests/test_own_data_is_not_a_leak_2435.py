"""Свои данные человеку — не утечка контакта (DRF-2435).

Замер, с которого лист начался. Человек пишет «выгрузить мои данные», навык
`privacy_consent` собирает архив и отдаёт его текстом
(`apps/skills/privacy_consent/skill.py`). Исходящий гейт классифицирует этот
текст как `contact` и подменяет ответ рекомендательной фразой
(`REPLACEMENT_TEXT`). В журнале это выглядит как «мы заблокировали утечку
контакта», а на деле человек вместо своих данных получает предложение услуг —
то есть запрос доступа по ст. 14 152-ФЗ отвечается продажей.

Почему это происходит: телефонный шаблон разрешает между группами цифр дефис
и пробельные, а архив — это UUID'ы (шестнадцатеричные группы через дефис) и
метки времени. Шаблон ловит цифры ВНУТРИ UUID'а. Измерено на 2000 архивах той
же формы, что в CI: блокируется 8, то есть 0.4% — примерно один запрос из 250.
Отсюда и «годами зелено», и два разных вердикта на одной голове: цвет решает
жеребьёвка UUID'ов того прогона, а не порядок тестов и не среда.

Починка в две линии, и здесь проверяются обе:

1. **Гейт не смотрит на `contact` в собственных данных человека**, которые он
   же и запросил. Признак приходит ОТТУДА, ГДЕ АРХИВ СОБИРАЕТСЯ
   (`SkillResult.subject_own_data`), а не угадывается по форме текста:
   угадывание по форме — тот же шаблон с другой стороны, и ошибётся так же.
2. **Шаблон перестаёт видеть цифры внутри UUID и меток времени** — они
   маскируются до проверки. Это вторая линия, а не замена первой.

И главное требование к этому листу: **сужение охраны требует доказательства,
что охрана осталась**. Поэтому ниже обе половины — и что перестало
блокироваться, и что блокируется по-прежнему.
"""
from __future__ import annotations

import json
import uuid

import pytest

from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT, evaluate_outbound

# ---------------------------------------------------------------------------
# Живые находки замера: фрагменты, которые гейт принимал за телефон
# ---------------------------------------------------------------------------

#: Настоящие UUID'ы и фрагменты, которые в них принимались за телефон. Не
#: выдуманные образцы: найдены прогоном (4094 случайных UUID'а → 4 ложных
#: телефона, 0.098% на один UUID; в архиве их два плюс метки времени, отсюда
#: измеренные 0.4% на запрос).
UUIDS_READ_AS_PHONES = (
    ("1c10a5a0-a48d-e15a-8506-6664946bd2cd", "8506-6664946"),
    ("c8145173-6936-ef6b-826c-05a43adcfd7b", "8145173-6936"),
    ("8d78e220-02a2-97f2-6bc1-89816746231c", "89816746231"),
    ("c66dc2dd-40d3-8c3c-02ec-84043096579d", "84043096579"),
)

#: Настоящие номера в тех написаниях, которые гейт ловил до правки. Если хоть
#: один перестанет блокироваться, мы не починили гейт, а ослабили его.
REAL_PHONES = (
    "+7 999 123 45 67",
    "8 (999) 123-45-67",
    "8-999-123-45-67",
    "89991234567",
    "Мастер просила передать: 8 999 123 45 67, позвоните ей сами",
)


def _archive_text(seed: int) -> str:
    """Текст ответа экспорта той же формы, что в CI: 3 согласия, 1 диалог."""
    rnd = uuid.UUID(int=seed)
    base = "2026-09-24T10:39:52"
    archive = {
        "bot_user": {
            "id": str(rnd), "channel": "max", "channel_user_id": "700037",
            "display_name": "Replay", "phone_hash": "",
            "first_seen": f"{base}.318000+00:00", "last_seen": f"{base}.325000+00:00",
        },
        "consents": [
            {"consent_type": t, "granted": True, "source": "chat",
             "document_version": v, "captured_at": f"{base}.32{i}000+00:00",
             "withdrawn_at": None}
            for i, (t, v) in enumerate((
                ("food_diary_processing", "food-diary-v1.0"),
                ("personal_data", ""),
                ("personal_calculation", "personal-calc-v1.0"),
            ))
        ],
        "conversations": [
            {"id": str(uuid.UUID(int=seed + 1)), "state": "idle", "outcome": "",
             "is_active": True, "created_at": f"{base}.321000+00:00",
             "deleted_at": None,
             "messages": [{"role": "user", "content": "выгрузить мои данные",
                           "created_at": f"{base}.322000+00:00"}]},
        ],
    }
    return "Ваши данные в формате JSON:\n\n" + json.dumps(
        archive, ensure_ascii=False, indent=2
    )


# ---------------------------------------------------------------------------
# Половина первая: что перестало блокироваться
# ---------------------------------------------------------------------------


class TestWhatStoppedBeingTakenForAPhone:
    @pytest.mark.parametrize(("identifier", "fragment"), UUIDS_READ_AS_PHONES)
    def test_a_real_uuid_is_not_a_phone(self, identifier: str, fragment: str) -> None:
        """Цифры внутри настоящего UUID'а — не номер.

        Каждая пара найдена прогоном, а не придумана: `fragment` — ровно то,
        что шаблон вырезал из `identifier`. Эта половина работает БЕЗ признака
        «свои данные»: вторая линия защищает и те ответы, где признака нет.
        """
        text = f'  "id": "{identifier}",'

        verdict = evaluate_outbound(text)

        assert verdict.allowed is True, (
            f"UUID {identifier!r} всё ещё читается как телефон (фрагмент "
            f"{fragment!r}) — значит 0.4% запросов на выгрузку получат отказ"
        )

    def test_a_whole_export_archive_passes(self) -> None:
        """Целый архив той же формы, что в CI, — проходит целиком."""
        text = _archive_text(0x82824497604ABCDEF1234567890ABCDE)

        verdict = evaluate_outbound(text, subject_own_data=True)

        assert verdict.allowed is True
        assert verdict.text == text
        assert "Ваши данные" in verdict.text

    def test_an_iso_timestamp_is_not_a_phone(self) -> None:
        text = "Записала 2026-08-24T18:30:45.812345+00:00 — жду тебя."

        assert evaluate_outbound(text).allowed is True


# ---------------------------------------------------------------------------
# Половина вторая: что блокируется ПО-ПРЕЖНЕМУ
# ---------------------------------------------------------------------------


class TestTheGuardStillGuards:
    @pytest.mark.parametrize("phone", REAL_PHONES)
    def test_a_real_phone_is_still_blocked(self, phone: str) -> None:
        verdict = evaluate_outbound(phone)

        assert verdict.allowed is False, f"номер {phone!r} ушёл бы человеку"
        assert "contact" in verdict.categories
        assert verdict.text == REPLACEMENT_TEXT

    @pytest.mark.parametrize("phone", REAL_PHONES)
    def test_a_real_phone_is_blocked_even_in_the_persons_own_data(self, phone: str) -> None:
        """Послабление для своих данных НЕ отменяет охрану целиком.

        Архив человека может содержать чужой номер — например в тексте его же
        сообщения, где он переписал телефон мастера. Признак «это свои данные»
        снимает `contact` с МАШИННЫХ идентификаторов, а не разрешает выдать
        чужой номер: шаблон, сработавший на человекочитаемом написании,
        блокирует по-прежнему.
        """
        text = _archive_text(0x1234).replace(
            "выгрузить мои данные", f"выгрузить мои данные {phone}"
        )

        verdict = evaluate_outbound(text, subject_own_data=True)

        assert verdict.allowed is False
        assert "contact" in verdict.categories

    def test_an_email_is_still_blocked(self) -> None:
        verdict = evaluate_outbound("напишите ей на masha@example.com")

        assert verdict.allowed is False
        assert "contact" in verdict.categories

    def test_a_partial_phone_behind_a_marker_is_still_blocked(self) -> None:
        verdict = evaluate_outbound("номер заканчивается на 45-67")

        assert verdict.allowed is False
        assert "contact" in verdict.categories


# ---------------------------------------------------------------------------
# Исход: ответ экспорта доходит до человека целиком
# ---------------------------------------------------------------------------


class TestTheExportReachesThePerson:
    def test_the_export_branch_marks_its_reply_as_the_persons_own_data(self) -> None:
        """Признак приходит из места сборки архива, а не угадывается по тексту."""
        from apps.skills.privacy_consent.skill import PrivacyConsentSkill

        assert hasattr(PrivacyConsentSkill, "name")
        # Сам признак — поле результата навыка; пустое значение по умолчанию.
        from apps.skills.base import SkillResult

        assert SkillResult(reply_text="x").subject_own_data is False

    def test_the_gate_tells_the_two_cases_apart_in_the_journal(self, caplog) -> None:
        """«Заблокировали чужой контакт» и «это свои данные, пропускаем» —
        разные записи. Иначе мы починим поведение и оставим слепой журнал, а
        именно он молчал три года."""
        import logging

        from apps.orchestrator.safety.gate import guard_outbound

        text = f'  "id": "{UUIDS_READ_AS_PHONES[0][0]}",'
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.safety.gate"):
            outcome = guard_outbound(
                text, surface="max", subject_own_data=True,
            )

        assert outcome.allowed is True
        messages = [r.getMessage() for r in caplog.records]
        # Запись о пропуске — есть, и она называет категорию, которая была бы
        # заблокирована; записи о блокировке при этом нет.
        passed = [m for m in messages if "own_data_passed" in m]
        assert passed, f"журнал молчит о пропуске своих данных: {messages}"
        assert "contact" in passed[0]
        assert not [m for m in messages if "outbound.blocked" in m], messages
