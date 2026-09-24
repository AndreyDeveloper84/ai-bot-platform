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

from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT, evaluate_outbound

# ---------------------------------------------------------------------------
# Живые находки замера: фрагменты, которые гейт принимал за телефон
# ---------------------------------------------------------------------------

#: Настоящие UUID'ы и фрагменты, которые в них принимались за телефон. Не
#: выдуманные образцы: найдены прогоном (4094 случайных UUID'а → 4 ложных
#: телефона, 0.098% на один UUID).
#:
#: Фрагменты записаны МАСКОЙ (`85…46`), и это не украшение: сторож личных
#: данных (`tools/lint/pii_guard.py`) поймал этот файл на строке, где фрагмент
#: стоял целиком, — потому что он читается как настоящий мобильный номер вне
#: тестового диапазона. То есть тот же digit-run, который обманывал исходящий
#: гейт, обманывает и сторожа PII; полная форма при необходимости
#: восстанавливается из самого UUID'а, который тут же рядом.
UUIDS_READ_AS_PHONES = (
    ("1c10a5a0-a48d-e15a-8506-6664946bd2cd", "85…46"),
    ("c8145173-6936-ef6b-826c-05a43adcfd7b", "81…36"),
    ("8d78e220-02a2-97f2-6bc1-89816746231c", "89…31"),
    ("c66dc2dd-40d3-8c3c-02ec-84043096579d", "84…79"),
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


def _archive_text(identifier: str = UUIDS_READ_AS_PHONES[0][0], *, phone_hash: str = "") -> str:
    """Текст ответа экспорта той же формы, что в CI: 3 согласия, 1 диалог.

    `identifier` по умолчанию — НАСТОЯЩИЙ UUID из замера, тот, чьи цифры гейт
    принимал за телефон. Прежний вариант строил идентификатор из выдуманного
    seed, и такой архив не блокировался НИКОГДА: узел «архив проходит» был
    самоподтверждением (найдено ревью).

    `phone_hash` пуст по умолчанию, как у человека без телефона; настоящий
    sha256 передаётся отдельным узлом — на нём шаблон ошибался чаще всего.
    """
    rnd = identifier
    base = "2026-09-24T10:39:52"
    archive = {
        "bot_user": {
            "id": str(rnd),
            "channel": "max",
            "channel_user_id": "700037",
            "display_name": "Replay",
            "phone_hash": phone_hash,
            "first_seen": f"{base}.318000+00:00",
            "last_seen": f"{base}.325000+00:00",
        },
        "consents": [
            {
                "consent_type": t,
                "granted": True,
                "source": "chat",
                "document_version": v,
                "captured_at": f"{base}.32{i}000+00:00",
                "withdrawn_at": None,
            }
            for i, (t, v) in enumerate(
                (
                    ("food_diary_processing", "food-diary-v1.0"),
                    ("personal_data", ""),
                    ("personal_calculation", "personal-calc-v1.0"),
                )
            )
        ],
        "conversations": [
            {
                "id": UUIDS_READ_AS_PHONES[1][0],
                "state": "idle",
                "outcome": "",
                "is_active": True,
                "created_at": f"{base}.321000+00:00",
                "deleted_at": None,
                "messages": [
                    {
                        "role": "user",
                        "content": "выгрузить мои данные",
                        "created_at": f"{base}.322000+00:00",
                    }
                ],
            },
        ],
    }
    return "Ваши данные в формате JSON:\n\n" + json.dumps(archive, ensure_ascii=False, indent=2)


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
        """Целый архив, собранный на НАСТОЯЩИХ идентификаторах из замера.

        Без признака «свои данные»: этот узел проверяет вторую линию, и он
        краснеет на прежнем поведении, потому что оба UUID'а в архиве — те
        самые, чьи цифры шаблон принимал за телефон.
        """
        text = _archive_text()

        verdict = evaluate_outbound(text)

        assert verdict.allowed is True
        assert verdict.text == text
        assert "Ваши данные" in verdict.text

    def test_a_sha256_hash_is_not_a_phone(self) -> None:
        """`phone_hash` — 64 hex-символа, и на них шаблон ошибался ЧАЩЕ всего:
        80 ложных телефонов на 20000 дайджестов (0.40%). Маскировка канонических
        UUID'ов этот случай не покрывала вовсе, граница hex-соседства покрывает
        (найдено ревью)."""
        digest = "5316e540ee22f6180fb89492904051b3" + "d5316e540ee22f6180fb894929040513"

        verdict = evaluate_outbound('  "phone_hash": "' + digest + '",')

        assert verdict.allowed is True

    def test_an_archive_of_a_person_who_has_a_phone_on_file_passes(self) -> None:
        """Архив человека, у которого телефон есть: `phone_hash` заполнен.

        Прежняя мера снималась на архиве с ПУСТЫМ хэшем, и это делало число
        «0 из 2000» правдой только для людей без телефона (найдено ревью).
        """
        digest = "88820462180e5c893eff" + "0" * 44

        assert evaluate_outbound(_archive_text(phone_hash=digest)).allowed is True


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

    def test_the_exemption_does_not_leak_into_ordinary_replies(self) -> None:
        """Послабление действует ТОЛЬКО на том черновике, который его получил.

        Обычный ответ навыка с номером внутри блокируется по-прежнему: признак
        не глобальный переключатель, а поле одного результата.
        """
        text = "Мастер просила передать: 8 999 123 45 67"

        # Сначала о НАЛИЧИИ послабления: с признаком тот же текст проходит…
        assert evaluate_outbound(text, subject_own_data=True).allowed is True
        # …а без признака блокируется. Послабление живёт на одном черновике,
        # а не включается для поверхности.
        blocked = evaluate_outbound(text)
        assert blocked.allowed is False
        assert blocked.categories == ("contact",)


class TestTheExemptionIsNarrow:
    """Послабление снимает ОДИН класс. Остальные — как у всякого текста."""

    @pytest.mark.parametrize(
        ("draft", "category"),
        [
            ("у вас аллергия на этот состав", "medical"),
            ("гарантирую результат после первой процедуры", "promise"),
            ("между курсами нужно три-четыре недели", "planning"),
        ],
    )
    def test_other_categories_still_block_under_the_flag(self, draft, category) -> None:
        verdict = evaluate_outbound(draft, subject_own_data=True)

        assert verdict.allowed is False, f"класс {category} перестал блокировать"
        assert verdict.own_data_categories == ()

    def test_a_broken_check_still_fails_closed_under_the_flag(self, monkeypatch) -> None:
        """Сломанная проверка закрывается наглухо и с признаком: решение
        владельца §111 «safety uncertain → fail closed» послабление не
        отменяет."""
        from apps.orchestrator.safety import outbound as ob

        monkeypatch.setattr(ob, "_CATEGORIES", "не-таблица")

        verdict = ob.evaluate_outbound("любой текст", subject_own_data=True)

        assert verdict.allowed is False
        assert verdict.categories == (ob.CHECK_FAILED_CATEGORY,)


class TestTheExportBranchSetsTheFlag:
    """Цепочка признака начинается у навыка — и проверяется ВЫЗОВОМ навыка, а не
    чтением поля у пустого `SkillResult` (найдено ревью: прежний узел ветку
    экспорта не трогал вовсе)."""

    def test_the_export_branch_marks_its_result(self, monkeypatch) -> None:
        from apps.skills.base import SkillContext
        from apps.skills.privacy_consent import skill as privacy_skill

        monkeypatch.setattr(
            privacy_skill,
            "data_export",
            lambda bot_user: {"bot_user": {}, "consents": [], "conversations": []},
        )
        monkeypatch.setattr(privacy_skill, "emit", lambda *a, **k: None)
        context = SkillContext(
            # Навыку нужен только `id` для события; модели здесь не нужны, а
            # контракт требует их типов — приведение названо явно.
            bot_user=cast(Any, SimpleNamespace(id="bu-2435")),
            conversation=cast(Any, SimpleNamespace(id="c-2435")),
            message_text="выгрузить мои данные",
        )

        result = privacy_skill.PrivacyConsentSkill().handle(context)

        # Сначала о НАЛИЧИИ: это именно ветка экспорта.
        assert result.meta["intent"] == "export"
        assert "Ваши данные" in result.reply_text
        assert result.subject_own_data is True

    def test_the_delete_branch_does_not_mark_its_result(self, monkeypatch) -> None:
        """Ветка удаления архива не отдаёт — значит и признак ей не нужен."""
        from apps.skills.base import SkillContext
        from apps.skills.privacy_consent import skill as privacy_skill

        monkeypatch.setattr(privacy_skill, "emit", lambda *a, **k: None)
        context = SkillContext(
            # Навыку нужен только `id` для события; модели здесь не нужны, а
            # контракт требует их типов — приведение названо явно.
            bot_user=cast(Any, SimpleNamespace(id="bu-2435")),
            conversation=cast(Any, SimpleNamespace(id="c-2435-del")),
            message_text="удалить мои данные",
        )

        result = privacy_skill.PrivacyConsentSkill().handle(context)

        assert result.meta["intent"] == "delete"
        assert result.subject_own_data is False


class TestThePriceOfTheHexBoundary:
    """Цена границы hex-соседства, названная вслух.

    Номер, СПЕЦИАЛЬНО вписанный в hex-подобную обёртку, больше не блокируется.
    Это неустранимо для любого правила «цифры внутри hex — не телефон», и выбор
    был между «ломаем выгрузку каждому 250-му» и «крафт проходит». Узел стоит
    здесь, чтобы цена не превратилась в сюрприз: если владелец решит платить
    иначе, он меняет знак этого узла осознанно.
    """

    def test_a_phone_hidden_in_a_uuid_shaped_wrapper_passes(self) -> None:
        crafted = "11111111-2222-3333-4444-89991234567a"

        assert evaluate_outbound(f"Мастер Мария {crafted}").allowed is True

    def test_the_same_number_written_plainly_is_blocked(self) -> None:
        """Обратная половина той же цены: без обёртки он блокируется."""
        assert evaluate_outbound("Мастер Мария 8 999 123 45 67").allowed is False


class TestWhatThePersonGetsInTheirOwnArchive:
    """Решение объёма, названное вслух, а не спрятанное в коде.

    Номер, написанный человекочитаемо ВНУТРИ собственной выгрузки, уходит
    человеку вместе с архивом. Это следствие признака «это свои данные», и оно
    не случайно:

    * прятать такой номер значило бы повторить тот самый дефект, от которого
      лист, но шире: 0.4% выгрузок ломались из-за UUID'ов, а «в истории есть
      что-то похожее на телефон» — доля куда больше;
    * человек получает то, что сам же и написал, и только себе: ответ уходит в
      его собственный диалог;
    * решение владельца, на которое ссылается шаблон («телефон клиента
      исполнителю не передаётся ни в каком виде»), — про передачу МАСТЕРУ, а
      не про доступ субъекта к своим данным по ст. 14.

    Остаточный вопрос, который я оставляю владельцу и не решаю кодом молча: в
    выгрузке может оказаться номер ТРЕТЬЕГО лица, который человек когда-то
    переписал в чат. Он и так у него есть, но если владелец решит прятать — это
    правка одной строки здесь, и тогда узел ниже поменяет знак.
    """

    @pytest.mark.parametrize("phone", REAL_PHONES)
    def test_a_phone_inside_the_archive_reaches_the_person(self, phone: str) -> None:
        text = _archive_text(UUIDS_READ_AS_PHONES[2][0]).replace(
            "выгрузить мои данные", f"выгрузить мои данные {phone}"
        )

        verdict = evaluate_outbound(text, subject_own_data=True)

        assert verdict.allowed is True
        assert verdict.text == text
        # И это видно в журнале: не «тишина», а названный пропуск.
        assert verdict.own_data_categories == ("contact",)

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

        # Текст, где совпадение ПЕРЕЖИВАЕТ маскировку машинных идентификаторов:
        # иначе снимать нечего и записи о пропуске не будет — и это правильно.
        text = _archive_text(UUIDS_READ_AS_PHONES[2][0]).replace(
            "выгрузить мои данные", "выгрузить мои данные 8 999 123 45 67"
        )
        with caplog.at_level(logging.INFO):
            outcome = guard_outbound(
                text,
                surface="max",
                subject_own_data=True,
            )

        assert outcome.allowed is True
        messages = [r.getMessage() for r in caplog.records]
        # Запись о пропуске — есть, и она называет категорию, которая была бы
        # заблокирована; записи о блокировке при этом нет.
        passed = [m for m in messages if "own_data_passed" in m]
        assert passed, f"журнал молчит о пропуске своих данных: {messages}"
        assert "contact" in passed[0]
        assert not [m for m in messages if "outbound.blocked" in m], messages


# ---------------------------------------------------------------------------
# Живой путь: через шов хода, а не в обход (требование к слиянию)
# ---------------------------------------------------------------------------


class TestTheLivePathThroughTheSeam:
    """Один узел обязан идти тем путём, которым идёт живой ход.

    Почему это отдельное требование, а не перестраховка: признак появился у
    `SkillResult`, а `TurnReply` его не нёс, и единственный читатель —
    исходящий гейт — стоит ЗА швом (`apps/orchestrator/turn_seam.py`). Узлы
    выше зовут `evaluate_outbound` напрямую, поэтому шов в их проверке не
    участвует вовсе: 21 зелёный узел и «0 из 2000» были правдой про функцию и
    неправдой про продукт. Поймал это сторож шва (`seam_field_loss`), а не мой
    набор.

    Здесь ход идёт целиком: событие канала → обработчик → шов → навык → шов →
    гейт → отправка. И проверяется не карта переноса, а ПОВЕДЕНИЕ: человек
    получил свои данные.

    Телефон в истории нужен затем, что без него маскировки машинных
    идентификаторов достаточно самой по себе, и узел прошёл бы даже с потерянным
    на шве признаком — то есть не доказывал бы ничего про признак.
    """

    PHONE = "8 999 123 45 67"

    @pytest.mark.django_db
    def test_the_person_gets_their_own_data_through_the_live_path(self, monkeypatch) -> None:
        from apps.channels.max import handler as max_handler
        from apps.channels.max import outbound as max_outbound
        from apps.conversations.services import record_message, resolve_active_conversation
        from apps.identity.services.resolver import resolve_or_create_bot_user
        from apps.orchestrator.memory import short_term
        from apps.orchestrator.memory.tests.test_short_term import _FakeRedis
        from apps.replay.golden_path import build_max_payload
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        fake = _FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
        monkeypatch.setattr(max_outbound, "send_chat_action", lambda *a, **k: None)
        sent: list[str] = []

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
            sent.append(text)
            return {"ok": True}

        monkeypatch.setattr(max_handler, "send_message", fake_send)

        tenant = Tenant.objects.create(slug="own-data-2435", name="Own Data 2435")
        uid = "724350"
        with tenant_scope(tenant):
            bot_user = resolve_or_create_bot_user(
                channel="max",
                channel_user_id=uid,
                chat_id=uid,
            )
            bot_user.welcomed_at = timezone.now()
            bot_user.save(update_fields=["welcomed_at"])
            conversation = resolve_active_conversation(bot_user, create_if_missing=True)
            assert conversation is not None
            # История человека содержит номер, который он сам когда-то написал.
            record_message(
                conversation,
                role="user",
                content=f"мастер просила передать {self.PHONE}",
            )

            max_handler.handle_max_event(
                build_max_payload("выгрузить мои данные", user_id=int(uid), mid=f"own-{uid}")
            )

        assert sent, "ход не отправил человеку ничего"
        reply = "\n".join(sent)
        # Сначала о НАЛИЧИИ: это именно ответ выгрузки, а не подмена.
        assert "Ваши данные" in reply, f"вместо выгрузки ушло: {reply[:120]!r}"
        assert "данные" in reply.casefold()
        # И телефон из собственной истории дошёл вместе с архивом — то есть
        # признак пережил шов. Потеряется на шве — здесь будет подмена.
        assert self.PHONE in reply
