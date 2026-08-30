"""DRF-990, третий заход: ВСЁ семейство ``cb:discover:*`` в глобальной истории.

Боевой замер пилота 30.08: из 355 реплик с ролью ``user`` 68 — сырые строки
нажатия, и 55 из них (81 %) семейства ``cb:discover``. Почти каждая пятая
«реплика человека», которую консьерж читает как сказанное ему словами, —
служебный payload кнопки.

#1329 закрыл ``cb:discover:book:*``. Прогон по формам семейства (не чтением —
прогоном через :func:`handle_global_max_event`) показал, где проходит граница::

    ФОРМА                                          сегодня
    cb:discover:book:{T}:{M}                       молчит
    cb:discover:book:{T}:{M}:{S}                   молчит
    cb:discover:book:{T}:{M}::{ref}                молчит
    cb:discover:book:{T}:{M}:{S}:{ref}             молчит
    cb:discover:book:1              (битый id)     молчит
    cb:discover:book:...  (5 сегментов, перебор)   молчит
    cb:discover:book:               (пустой хвост) молчит
    cb:discover:book                (без двоеточия) СЫРЫМ В ИСТОРИЮ
    cb:discover:                                    СЫРЫМ В ИСТОРИЮ
    cb:discover                                     СЫРЫМ В ИСТОРИЮ
    cb:discover:masters:{T}                         СЫРЫМ В ИСТОРИЮ
    cb:discover:more:{ref}                          СЫРЫМ В ИСТОРИЮ
    cb:discover:salons:{T}                          СЫРЫМ В ИСТОРИЮ

Гейт сторожит ГЛАГОЛ (``cb:discover:book:``), а не СЕМЕЙСТВО. Все арности
единственного глагола, который репозиторий сегодня выкладывает, он закрывает —
включая деградации; мимо него идёт ровно то, что глаголом ``book:`` не
является. Своего маршрута у таких форм тоже нет: они падают до консьержа
сырым текстом и оседают в истории.

Решение по каждой форме — МОЛЧАНИЕ, и довод один на всё семейство, а не
пятнадцать доводов по формам:

* ``cb:discover:*`` целиком — это навигация по карточкам, которые бот
  НАРИСОВАЛ САМ: каждый payload несёт id мастера, салона или ссылку на
  сохранённый запрос, то есть то, чего человек не говорил и сказать не мог.
  Ровно довод ``cb:catalog:*`` (DRF-1304), и там гейт стоит по СЕМЕЙСТВУ, а
  не по глаголу;
* асимметрии, из-за которой анкете и приветствию дали ФРАЗУ, здесь нет.
  У анкеты пять шагов, клавиатуру имеют два, остальные человек набирает
  руками — молчание дало бы запись, где «30» есть, а пола нет. У воронки
  записи набранных шагов нет вовсе: ``cb:discover:book:`` — её первый шаг,
  все последующие (``cb:book:*``, DRF-988) уже молчат. Молчание однородно;
* то, чем человек ОТКРЫЛ воронку, он набирает («хочу маникюр в пензе») и
  оно в истории остаётся всегда. Ответы бота (карточки, передача в салон)
  пишутся как обычно. Контекст следующего хода не теряется.

Разбирается ФОРМА, а не префикс: человек может НАБРАТЬ «cb:discover: …»
руками, и подменять ему его собственные слова нельзя (правило C01,
``apps/channels/tests/test_first_contact_c01.py``). Поэтому здесь же стоят
проверки на набранный двойник.

Правило контура: отрицательному утверждению нужна положительная стража на
тех же данных. Рядом с «в истории нет ``cb:``» всюду стоит либо «история
непуста и в ней ровно то, что человек набрал», либо «бот действительно
ответил», либо «маршрутизация получила payload нетронутым» — иначе тест
зеленел бы на пустой выборке.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.tests.test_global_callback_history import (
    _assistant_messages,
    _msg,
    _raw,
    _tap,
    _user_messages,
    _welcomed_user,
)
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Оснастка                                                                     #
#                                                                              #
# Помощники (``_tap``, ``_msg``, ``_welcomed_user``, ``_user_messages``,       #
# ``_raw``) ИМПОРТИРОВАНЫ из соседнего модуля DRF-990, а не переписаны сюда:   #
# копия разъедется с оригиналом ровно в тот день, когда оснастку поправят.     #
# Фикстуры pytest между модулями не наследуются, поэтому объявлены здесь.      #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    """Разбор намерения — второй вызов модели на ходу со свободным текстом.

    Сценарии здесь набирают текст («хочу маникюр в пензе»), поэтому без этой
    заглушки тест ходил бы в сеть за разбором намерения — и падал бы по
    погоде, а не по существу.
    """
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


@pytest.fixture
def concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


T = "11111111-1111-1111-1111-111111111111"
M = "22222222-2222-2222-2222-222222222222"
S = "33333333-3333-3333-3333-333333333333"
#: base64url — тем же алфавитом кодируется ссылка на сохранённый запрос
#: (DRF-1324, ``cb:discover:book:{T}:{M}:{S}:{query_ref}``).
REF = "cXVlcnktcmVm"

OPENING_PHRASE = "хочу маникюр в пензе"


def _open_then_tap(user_id: int, payload: str):
    """Открыть разговор НАБРАННОЙ фразой и нажать кнопку.

    Набранная фраза — положительная стража: без неё «в истории нет ``cb:``»
    было бы истиной о пустой выборке.
    """
    _, conversation = _welcomed_user(user_id)
    max_handler.handle_global_max_event(
        _msg(text=OPENING_PHRASE, user_id=user_id, mid=f"d-{user_id}")
    )
    max_handler.handle_global_max_event(
        _tap(payload=payload, user_id=user_id, callback_id=f"dt-{user_id}")
    )
    return conversation


# --------------------------------------------------------------------------- #
# 1. Все арности выложенного глагола — молчат (закрыто #1329, здесь заперто)   #
# --------------------------------------------------------------------------- #
class TestEveryShippedArityIsSilent:
    """``cb:discover:book:*`` во всех формах, включая деградации.

    Маршрут НЕ подменён: ход идёт через настоящий
    ``_discovery_handoff_reply``, то есть проверяется заодно, что и сам путь
    передачи в салон не пишет реплику за человека.
    """

    @pytest.mark.parametrize(
        ("payload", "user_id"),
        [
            (f"cb:discover:book:{T}:{M}", 95101),
            (f"cb:discover:book:{T}:{M}:{S}", 95102),
            (f"cb:discover:book:{T}:{M}::{REF}", 95103),
            (f"cb:discover:book:{T}:{M}:{S}:{REF}", 95104),
            ("cb:discover:book:1", 95105),
            (f"cb:discover:book:{T}:{M}:{S}:{REF}:5", 95106),
            ("cb:discover:book:", 95107),
        ],
    )
    def test_the_tap_leaves_no_user_turn(self, payload, user_id, sent, fake_redis, concierge):
        conversation = _open_then_tap(user_id, payload)

        history = _user_messages(conversation)
        # Положительные стражи на ТЕХ ЖЕ данных: набранная фраза на месте и
        # бот на тап ответил — выборка не пуста и ход не был проглочен.
        assert OPENING_PHRASE in history, history
        assert sent, "бот не ответил — проверка ниже ничего не доказывает"
        assert not _raw(history), history


# --------------------------------------------------------------------------- #
# 2. Остальное семейство — красное до правки                                   #
# --------------------------------------------------------------------------- #
class TestTheRestOfTheFamilyIsSilentToo:
    """Гейт сторожит глагол, а не семейство, — и всё, что не ``book:``, течёт.

    Своего маршрута у этих форм нет: они доходят до консьержа сырым текстом.
    Маршрутизацию этот PR не трогает (устройство гейта — отдельный вопрос),
    но в ИСТОРИИ им места нет по тому же доводу, что и у ``book:``: payload
    несёт id карточки, нарисованной ботом, а не слова человека.
    """

    @pytest.mark.parametrize(
        ("payload", "user_id"),
        [
            ("cb:discover:book", 95201),
            ("cb:discover:", 95202),
            ("cb:discover", 95203),
            (f"cb:discover:masters:{T}", 95204),
            (f"cb:discover:more:{REF}", 95205),
            (f"cb:discover:salons:{T}", 95206),
        ],
    )
    def test_the_tap_leaves_no_user_turn(self, payload, user_id, sent, fake_redis, concierge):
        conversation = _open_then_tap(user_id, payload)

        history = _user_messages(conversation)
        assert OPENING_PHRASE in history, history
        assert _assistant_messages(conversation), "бот не ответил — стража пуста"
        assert not _raw(history), history


# --------------------------------------------------------------------------- #
# 3. Набранный двойник — это слова человека, и они остаются                    #
# --------------------------------------------------------------------------- #
class TestTypedLookalikeIsStillThePersonsOwnWords:
    """Разбирается ФОРМА, а не префикс.

    Без этих проверок «молчание по семейству» съело бы реплику человека,
    который набрал похожую строку руками, — а это дефект хуже того, который
    здесь чинится: сырой payload модель хотя бы толкует, стёртую реплику
    она не увидит вовсе.
    """

    @pytest.mark.parametrize(
        ("typed", "user_id"),
        [
            ("cb:discover: это я просто так написала", 95301),
            (f"CB:DISCOVER:BOOK:{T}:{M}", 95302),
            ("а что такое cb:discover?", 95303),
        ],
    )
    def test_typed_text_survives_verbatim(self, typed, user_id, sent, fake_redis, concierge):
        _, conversation = _welcomed_user(user_id)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=user_id, mid=f"t-{user_id}"))

        assert _user_messages(conversation) == [typed]


# --------------------------------------------------------------------------- #
# 4. Правка живёт на месте записи в историю, а не в ``event.text``             #
# --------------------------------------------------------------------------- #
class TestRoutingStillSeesTheUntouchedPayload:
    """Передача в салон маршрутизируется ПО payload'у.

    Если бы молчание достигалось подменой ``event.text`` выше по ходу,
    сломалась бы сама воронка записи (``_discovery_handoff_reply`` разбирает
    ровно ``cb:discover:book:{T}:{M}[:{S}[:{ref}]]``). Стража: резолвер
    маршрута получает строку байт в байт.
    """

    @pytest.fixture
    def stub_discover_route(self, monkeypatch):
        from apps.orchestrator.discovery import DiscoveryReply

        seen: list[str] = []

        def fake(event, bot_user, trace_id):
            seen.append(event.text)
            return DiscoveryReply(text="Передаю тебя в салон.")

        monkeypatch.setattr(max_handler, "_discovery_handoff_reply", fake)
        return seen

    def test_the_handoff_gets_the_payload_byte_for_byte(
        self, sent, fake_redis, concierge, stub_discover_route
    ):
        payload = f"cb:discover:book:{T}:{M}:{S}:{REF}"
        conversation = _open_then_tap(95401, payload)

        assert stub_discover_route == [payload], stub_discover_route
        assert not _raw(_user_messages(conversation)), _user_messages(conversation)


# --------------------------------------------------------------------------- #
# 5. Короткая память — тот же гейт, та же проверка                             #
# --------------------------------------------------------------------------- #
class TestShortTermMemoryGetsNoRawPayload:
    """Консьерж читает не только таблицу ``Message``, но и короткую память.

    Гейт один на оба хранилища (``record_global_message`` + ``short_term``),
    и проверка обязана стоять на обоих: зелёная таблица при грязном Redis
    оставила бы дефект ровно там, где модель и читает контекст хода.
    """

    @pytest.mark.parametrize(
        ("payload", "user_id"),
        [
            (f"cb:discover:book:{T}:{M}", 95501),
            (f"cb:discover:masters:{T}", 95502),
        ],
    )
    def test_recall_holds_the_typed_phrase_and_no_payload(
        self, payload, user_id, sent, fake_redis, concierge
    ):
        conversation = _open_then_tap(user_id, payload)

        recent = short_term.recall(conversation.id)
        user_turns = [m for m in recent if m.get("role") == "user"]
        assert user_turns, "короткая память пуста — проверка ниже ничего не доказывает"
        assert OPENING_PHRASE in [str(m.get("content", "")) for m in user_turns], recent
        assert not [m for m in user_turns if str(m.get("content", "")).startswith("cb:")], recent


# --------------------------------------------------------------------------- #
# 6. Резолвер отдельно от хендлера — граница «тап / набранный текст»           #
# --------------------------------------------------------------------------- #
class TestTheResolverDrawsTheLineAtTheForm:
    """Граница проверяется на самой функции, а не только сквозь весь ход.

    Сквозной прогон выше доказывает поведение; здесь заперта ПРИЧИНА, по
    которой оно такое, — иначе следующая правка формы регулярного выражения
    упала бы в двадцати сквозных тестах сразу и без внятного «что именно
    сломалось».
    """

    @pytest.mark.parametrize(
        "payload",
        [
            f"cb:discover:book:{T}:{M}",
            f"cb:discover:book:{T}:{M}:{S}",
            f"cb:discover:book:{T}:{M}::{REF}",
            f"cb:discover:book:{T}:{M}:{S}:{REF}",
            "cb:discover:book:1",
            "cb:discover:book:",
            "cb:discover:book",
            "cb:discover:",
            "cb:discover",
            f"cb:discover:masters:{T}",
            f"cb:discover:more:{REF}",
        ],
    )
    def test_a_payload_of_the_family_is_a_tap_and_says_nothing(self, payload):
        from apps.orchestrator.discovery import resolve_discover_tap

        tap = resolve_discover_tap(payload)
        assert tap is not None, payload
        assert tap.history_text is None, payload

    @pytest.mark.parametrize(
        "typed",
        [
            "cb:discover: это я просто так написала",
            f"CB:DISCOVER:BOOK:{T}:{M}",
            "а что такое cb:discover?",
            "cb:discovery:book:1",
            "cb:catalog:services:1",
            "хочу маникюр в пензе",
            "",
        ],
    )
    def test_anything_else_is_left_to_the_caller(self, typed):
        from apps.orchestrator.discovery import resolve_discover_tap

        assert resolve_discover_tap(typed) is None, typed

    def test_the_shipped_prefix_is_covered_by_the_form(self):
        """Стража от расхождения: константа маршрута и форма истории — об одном.

        Если ``CALLBACK_DISCOVER_BOOK_PREFIX`` когда-нибудь переименуют, а
        регулярное выражение забудут, тап снова поедет в историю сырым — и
        узнается это здесь, а не в чате у человека.
        """
        from apps.orchestrator.discovery import (
            CALLBACK_DISCOVER_BOOK_PREFIX,
            resolve_discover_tap,
        )

        assert resolve_discover_tap(f"{CALLBACK_DISCOVER_BOOK_PREFIX}{T}:{M}") is not None

    def test_a_real_query_ref_fits_the_form(self):
        """Четвёртый сегмент строит НЕ тест, а сам кодировщик (DRF-1324).

        Литеральный «cXVlcnktcmVm» в параметрах выше — удобная подделка. Если
        алфавит ссылки когда-нибудь поменяют (padding, другой base64), форма
        перестанет её покрывать, и тап снова поедет в историю сырым. Здесь
        ссылка берётся у самого ``encode_query_ref``.
        """
        from apps.orchestrator.discovery import (
            CALLBACK_DISCOVER_BOOK_PREFIX,
            encode_query_ref,
            resolve_discover_tap,
        )

        ref = encode_query_ref("маникюр в пензе недорого")
        assert ref, "кодировщик вернул пустую ссылку — проверка ниже ни о чём"
        payload = f"{CALLBACK_DISCOVER_BOOK_PREFIX}{T}:{M}:{S}:{ref}"
        assert resolve_discover_tap(payload) is not None, payload


# --------------------------------------------------------------------------- #
# 7. Почему здесь нет прогона golden-фикстур                                   #
# --------------------------------------------------------------------------- #
class TestNoGoldenFixtureFeedsThisGate:
    """Заявление «golden затронутого пути нет» — проверкой, а не на словах.

    #1325 и #1329 правили гейт у семейств, у которых golden-фикстуры есть
    (``nutrition_anketa``, ``food_*``), и гоняли их. У ``cb:discover:*``
    фикстуры с колбэком на входе нет ни в одном наборе — воронка записи
    покрыта e2e-наборами (``test_marketplace_handoff_e2e``,
    ``test_global_booking_continuation``, ``test_tenant_less_discovery_e2e``),
    и они прогоняются как обычно.

    Проверка самоотменяющаяся: как только кто-нибудь заведёт golden-фикстуру
    с ``cb:discover:`` на входе, она упадёт здесь — и следующий читатель
    узнает, что прогон пора добавить, а не обнаружит молча пропущенный набор.
    """

    def test_the_golden_tree_is_really_there(self):
        """Стража сторожа: перебор ниже читает настоящее дерево фикстур."""
        assert self._golden_inputs(), "дерево golden пусто — проверка ниже ни о чём"

    def test_no_golden_input_is_a_discover_callback(self):
        offenders = [t for t in self._golden_inputs() if t.startswith("cb:discover")]
        assert not offenders, offenders

    @staticmethod
    def _golden_inputs() -> list[str]:
        from pathlib import Path

        import apps.replay
        from apps.replay.fixtures.loader import load_fixture_set

        package_file = apps.replay.__file__
        assert package_file is not None, "apps.replay — обычный пакет, у него есть __file__"
        golden = Path(package_file).parent / "fixtures" / "golden"
        return [
            str(f.input.get("text", ""))
            for directory in sorted(p for p in golden.iterdir() if p.is_dir())
            for f in load_fixture_set(directory)
        ]
