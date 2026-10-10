"""DRF-1942 — голосовое как вход на ГЛОБАЛЬНОМ пути MAX (этап 1, PR 3).

Тот же контракт, что у per-tenant теста (``apps/channels/max/tests/
test_handler_voice_input.py``), доказанный на втором входе: флаг выключен →
заглушка DRF-1939 байт в байт; расшифровка X доходит до консьержа как
набранный X; S1 в расшифровке останавливает ход до консьержа; отказ —
фразой человеку. Онбординг выключен, чтобы свободный текст шёл прямо к
модели-шпиону, как в ``test_first_contact_c01``.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.tests.test_handler_voice_input import _row_columns
from apps.channels.max.voice import VOICE_ACTION_TYPE, VOICE_NOT_SUPPORTED_TEXT
from apps.conversations.models import Conversation, Message
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.speech import registry
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.tests.test_ogg import opus_head, page

pytestmark = pytest.mark.django_db

_DOWNLOAD = "apps.channels.max.voice_turn.download_audio"
AUDIO = {
    "type": "audio",
    "payload": {"id": 7, "url": "https://a.oneme.ru/v.ogg?sig=S", "token": "t"},
}
S1_TEXT = "последние дни мне так тяжело что хочу умереть"


def ogg_of(seconds: float) -> bytes:
    return page(0, opus_head(0), flags=2) + page(int(seconds * 48_000), b"x", seq=1)


def _msg(
    *,
    text: str = "",
    user_id: int,
    chat_id: int = 8899,
    mid: str = "m-1",
    attachments=None,
) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": attachments or [],
            },
        },
    }


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.GLOBAL_BOT_ONBOARDING = False
    settings.VOICE_INPUT_ENABLED = True
    settings.VOICE_ALLOWED_USER_IDS = "*"  # DRF-2424 — список допуска: всем
    settings.VOICE_CROSS_BORDER_ALLOWED = True
    settings.VOICE_STT_PROVIDER = "fake"
    settings.VOICE_GATE_STRIP_PUNCT = True
    settings.VOICE_ECHO_MODE = "never"
    registry.set_provider_for_tests(None)
    yield
    registry.set_provider_for_tests(None)


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )


@pytest.fixture(autouse=True)
def _no_upcoming(monkeypatch):
    from apps.booking.services.records import VisitsResult

    monkeypatch.setattr(
        "apps.booking.services.records.list_upcoming",
        lambda **kwargs: VisitsResult(status="empty"),
    )


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _provider(text: str) -> FakeSpeechProvider:
    provider = FakeSpeechProvider(text=text)
    registry.set_provider_for_tests(provider)
    return provider


def _rows_of(user_id: int) -> list[dict]:
    """Строки Message одного собеседника глобального бота (DRF-2488).

    Все колонки, кроме заведомо разных у двух ходов (``_row_columns`` из
    per-tenant теста), отсортированные по роли и тексту: pk не монотонный.
    """
    rows = Message.all_tenants.filter(conversation__bot_user__channel_user_id=str(user_id)).values(
        *_row_columns()
    )
    return sorted(rows, key=lambda r: (r["role"], r["content"], r["action_type"]))


def _user_rows(user_id: int) -> list[tuple[str, str]]:
    rows = Message.all_tenants.filter(
        conversation__bot_user__channel_user_id=str(user_id), role="user"
    )
    return sorted((m.content, m.input_channel) for m in rows)


class TestGlobalVoice:
    def test_flag_off_is_the_drf1939_stub(self, sent, fake_redis, concierge, settings):
        settings.VOICE_INPUT_ENABLED = False
        provider = _provider("привет")
        with patch(_DOWNLOAD) as dl:
            max_handler.handle_global_max_event(_msg(user_id=70000, attachments=[AUDIO]))
        assert [c["text"] for c in sent] == [VOICE_NOT_SUPPORTED_TEXT]
        assert Message.all_tenants.filter(action_type=VOICE_ACTION_TYPE).count() == 1
        concierge.assert_not_called()
        dl.assert_not_called()
        assert provider.calls == []
        # DRF-2488 — без расшифровки пометки нет: ``text`` с пустым content.
        assert _user_rows(70000) == [("", "text")]

    def test_voice_reaches_the_model_exactly_like_typed_text(self, sent, fake_redis, concierge):
        _provider("хочу снять напряжение")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=70001, chat_id=1, mid="v-1", attachments=[AUDIO])
            )
        max_handler.handle_global_max_event(
            _msg(text="хочу снять напряжение", user_id=70002, chat_id=2, mid="t-1")
        )
        assert concierge.call_count == 2
        voice_text = concierge.call_args_list[0].args[0]
        typed_text = concierge.call_args_list[1].args[0]
        assert voice_text == typed_text == "хочу снять напряжение"
        assert sent[0]["text"] == sent[1]["text"]

    def test_s1_in_voice_stops_before_the_model(self, sent, fake_redis, concierge):
        _provider(S1_TEXT)
        with patch(_DOWNLOAD, return_value=ogg_of(3)):
            max_handler.handle_global_max_event(_msg(user_id=70003, attachments=[AUDIO]))
        assert [c["text"] for c in sent] == [CRISIS_REPLY_TEXT]
        assert Message.all_tenants.filter(action_type="safety_pre_check").count() == 1
        concierge.assert_not_called()

    def test_k19_comma_hyperbole_goes_to_the_model_with_strip(self, sent, fake_redis, concierge):
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70004, attachments=[AUDIO]))
        assert concierge.call_count == 1
        assert (
            concierge.call_args.args[0] == "Умираю, хочу кофе."
        )  # модели — оригинал, не копия без знаков

    def test_k19_same_transcript_passes_without_the_copy_too(
        self, sent, fake_redis, concierge, settings
    ):
        # DRF-2684 (K19-А): правило «умираю» само не замечает знаков. До правки
        # при выключенном флаге эта же расшифровка давала кризисный ответ.
        settings.VOICE_GATE_STRIP_PUNCT = False
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70005, attachments=[AUDIO]))
        assert concierge.call_count == 1
        assert concierge.call_args.args[0] == "Умираю, хочу кофе."
        assert Message.all_tenants.filter(action_type="safety_pre_check").count() == 0

    @pytest.mark.parametrize(("user_id", "strip"), [(70015, True), (70016, False)])
    def test_k19_a_crisis_behind_the_hyperbole_stops_with_and_without_the_copy(
        self, sent, fake_redis, concierge, settings, user_id, strip
    ):
        settings.VOICE_GATE_STRIP_PUNCT = strip
        _provider("Умираю, хочу просто умереть.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=user_id, attachments=[AUDIO]))
        assert [c["text"] for c in sent] == [CRISIS_REPLY_TEXT]
        concierge.assert_not_called()

    def test_too_long_is_answered_and_the_model_is_not_called(
        self, sent, fake_redis, concierge, settings
    ):
        settings.VOICE_MAX_DURATION_S = 60
        provider = _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(61)):
            max_handler.handle_global_max_event(_msg(user_id=70006, attachments=[AUDIO]))
        assert len(sent) == 1
        assert "60 секунд" in sent[0]["text"]
        assert Message.all_tenants.filter(action_type="voice_too_long").count() == 1
        concierge.assert_not_called()
        assert provider.calls == []
        assert _user_rows(70006) == [("", "text")]  # DRF-2488 — отказ не помечается

    def test_echo_always_prefixes_the_model_reply(self, sent, fake_redis, concierge, settings):
        settings.VOICE_ECHO_MODE = "always"
        _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70007, attachments=[AUDIO]))
        assert sent[0]["text"] == "Я услышала: «привет»\n\nРасскажи чуть подробнее?"

    @pytest.mark.parametrize(
        ("said", "reaches_the_model"),
        [
            ("Запиши к мастеру завтра в десять тридцать.", "Запиши к мастеру завтра в 10:30."),
            ("Запиши к Денису на пятнадцать ноль ноль.", "Запиши к Денису на 15:00."),
            ("Запиши на двадцать пятое октября.", "Запиши на 25 октября."),
        ],
    )
    def test_a_spoken_time_reaches_the_model_as_it_was_said(
        self, sent, fake_redis, concierge, settings, said, reaches_the_model
    ):
        """DRF-2710 — модели и человеку достаётся названное время, а не сумма.

        До правки нормализатор чисел складывал «десять тридцать» в 40: модель
        получала «…завтра в 40.», и запись уходила на время, которого человек
        не называл. Та же строка стояла в эхе — так владелец это и увидел.
        """
        settings.VOICE_ECHO_MODE = "always"
        _provider(said)
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70020, attachments=[AUDIO]))

        assert concierge.call_count == 1
        assert concierge.call_args.args[0] == reaches_the_model
        assert sent[0]["text"] == f"Я услышала: «{reaches_the_model}»\n\nРасскажи чуть подробнее?"

    def test_voice_and_typed_rows_differ_only_in_input_channel(self, sent, fake_redis, concierge):
        """DRF-2488 — паритет строк Message на глобальном пути.

        Голос с расшифровкой X и набранный X пишут одинаковые строки по всем
        колонкам, кроме заведомо разных; расходятся ровно в одном месте —
        ``input_channel`` у реплики человека.
        """
        _provider("хочу снять напряжение")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=70011, chat_id=11, mid="v-11", attachments=[AUDIO])
            )
        max_handler.handle_global_max_event(
            _msg(text="хочу снять напряжение", user_id=70012, chat_id=12, mid="t-12")
        )

        assert "input_channel" in _row_columns()
        voice_rows, typed_rows = _rows_of(70011), _rows_of(70012)
        assert len(voice_rows) == len(typed_rows) >= 2
        differences = [
            (a["role"], column, a[column], b[column])
            for a, b in zip(voice_rows, typed_rows, strict=True)
            for column in a
            if a[column] != b[column]
        ]
        assert differences == [("user", "input_channel", "voice", "text")]
        assert _user_rows(70011) == [("хочу снять напряжение", "voice")]

    def test_voice_under_operator_is_transcribed_and_marked(self, sent, fake_redis, concierge):
        """DRF-2488 — на глобальном пути голосовое под оператором распознаётся.

        В отличие от салонного пути, здесь распознавание стоит выше глушения
        DRF-1015 (гейт safety обязан видеть кризис и под оператором, N-1),
        поэтому реплика ложится расшифровкой с пометкой ``voice``, а бот молчит.
        """
        max_handler.handle_global_max_event(_msg(text="привет", user_id=70013, mid="h-1"))
        assert len(sent) == 1
        sent.clear()
        conv = Conversation.all_tenants.get(bot_user__channel_user_id="70013")
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        concierge.reset_mock()

        provider = _provider("а когда ответит оператор")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70013, mid="h-2", attachments=[AUDIO]))
        assert len(provider.calls) == 1
        assert _user_rows(70013) == [("а когда ответит оператор", "voice"), ("привет", "text")]
        concierge.assert_not_called()
        # Ответа бота нет (уведомление о молчании DRF-1486 уходит своим
        # отправителем из ``apps.handoff.silence``, не через handler).
        assert sent == []

    def test_logs_carry_no_transcript(self, sent, fake_redis, concierge, caplog, settings):
        """DRF-2488 — пометка не тянет расшифровку в логи: слушаем корень на DEBUG.

        Пост-ответный разбор намерений выключен: это настоящий исходящий HTTP
        к OpenAI (в CI ключ-заглушка → 401), тесту не нужна сеть. Тело запроса
        SDK в лог больше не пишет и при DEBUG — ``openai._base_client`` прибит
        на INFO в ``LOGGING`` (DRF-2634, свой тест в ``apps/observability``).
        """
        settings.INTENT_RESOLUTION_LIVE_ENABLED = False
        caplog.set_level(logging.DEBUG)
        _provider("СЕКРЕТНОЕ СЛОВО ксилофон")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70014, attachments=[AUDIO]))
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "channels.max.voice.resolved provider=fake" in text
        assert "conversations.message.stored(global)" in text
        assert _user_rows(70014) == [("СЕКРЕТНОЕ СЛОВО ксилофон", "voice")]
        assert "ксилофон" not in text

    def test_allowlist_on_the_global_path(self, sent, fake_redis, concierge, settings):
        """DRF-2424 — на глобальном пути тот же список допуска."""
        settings.VOICE_ALLOWED_USER_IDS = "70021"
        provider = _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=70021, chat_id=21, mid="al-1", attachments=[AUDIO])
            )
        assert len(provider.calls) == 1
        assert _user_rows(70021) == [("привет", "voice")]
        sent.clear()

        with patch(_DOWNLOAD) as dl:
            max_handler.handle_global_max_event(
                _msg(user_id=70022, chat_id=22, mid="al-2", attachments=[AUDIO])
            )
        assert [c["text"] for c in sent] == [VOICE_NOT_SUPPORTED_TEXT]
        dl.assert_not_called()
        assert len(provider.calls) == 1
        assert _user_rows(70022) == [("", "text")]


@pytest.fixture
def persisting_concierge(monkeypatch):
    """Консьерж, который, как настоящий, сам пишет свой ответ в переписку (DRF-2686).

    Обычная фикстура ``concierge`` отдаёт ``persisted=False`` — строку пишет
    обработчик, и потеря эха в строке консьержа на ней не видна.
    """
    from apps.conversations.services import record_global_message
    from apps.orchestrator.discovery import DiscoveryReply

    def reply(message_text, *, conversation, trace_id=None, **kwargs):
        record_global_message(
            conversation,
            role="assistant",
            content="Расскажи чуть подробнее?",
            rendered_text="Расскажи чуть подробнее?",
            action_type="concierge",
            tokens_in=11,
            tokens_out=5,
            trace_id=trace_id,
        )
        return DiscoveryReply(text="Расскажи чуть подробнее?", persisted=True)

    spy = MagicMock(side_effect=reply)
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _assistant_rows(user_id: int) -> list[Message]:
    return list(
        Message.all_tenants.filter(
            conversation__bot_user__channel_user_id=str(user_id), role="assistant"
        )
    )


class TestEchoInTheConciergeRow:
    """DRF-2686 — переписка хранит ответ консьержа таким, каким его прочитали."""

    ECHOED = "Я услышала: «привет»\n\nРасскажи чуть подробнее?"

    def _voice(self, user_id: int) -> None:
        _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=user_id, attachments=[AUDIO]), trace_id=str(uuid.uuid4())
            )

    def test_the_row_carries_the_echo_the_person_read(
        self, sent, fake_redis, persisting_concierge, settings
    ):
        settings.VOICE_ECHO_MODE = "always"
        self._voice(70031)
        assert [c["text"] for c in sent] == [self.ECHOED]
        (row,) = _assistant_rows(70031)  # одна строка — не дубль
        assert row.content == row.rendered_text == sent[0]["text"]
        assert (row.action_type, row.tokens_in, row.tokens_out) == ("concierge", 11, 5)

    def test_no_echo_mode_leaves_the_row_as_written(
        self, sent, fake_redis, persisting_concierge, settings
    ):
        settings.VOICE_ECHO_MODE = "never"
        self._voice(70032)
        (row,) = _assistant_rows(70032)
        assert row.content == sent[0]["text"] == "Расскажи чуть подробнее?"

    def test_typed_text_leaves_the_row_as_written(
        self, sent, fake_redis, persisting_concierge, settings
    ):
        settings.VOICE_ECHO_MODE = "always"
        max_handler.handle_global_max_event(
            _msg(text="привет", user_id=70033), trace_id=str(uuid.uuid4())
        )
        (row,) = _assistant_rows(70033)
        assert row.content == sent[0]["text"] == "Расскажи чуть подробнее?"

    def test_a_blocked_reply_is_a_new_row_and_the_concierge_row_is_untouched(
        self, sent, fake_redis, persisting_concierge, settings, monkeypatch
    ):
        """Гард заблокировал ответ: человеку ушла замена без эха, она и записана."""
        from apps.orchestrator.safety.gate import OUTBOUND_ACTION_TYPE
        from apps.orchestrator.safety.outbound import OutboundVerdict

        settings.VOICE_ECHO_MODE = "always"
        monkeypatch.setattr(
            max_handler,
            "guard_outbound",
            lambda text, **kwargs: OutboundVerdict(allowed=False, text="Тут нужен человек."),
        )
        self._voice(70034)
        assert [c["text"] for c in sent] == ["Тут нужен человек."]  # эха у замены нет
        rows = {r.action_type: r.content for r in _assistant_rows(70034)}
        assert rows == {
            "concierge": "Расскажи чуть подробнее?",
            OUTBOUND_ACTION_TYPE: "Тут нужен человек.",
        }

    def test_a_missing_row_does_not_cost_the_turn(self, sent, fake_redis, monkeypatch, settings):
        """Консьерж сказал ``persisted``, а строки нет — ход всё равно доходит до человека."""
        from apps.orchestrator.discovery import DiscoveryReply

        settings.VOICE_ECHO_MODE = "always"
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=True)),
        )
        self._voice(70035)
        assert [c["text"] for c in sent] == [self.ECHOED]
        assert _assistant_rows(70035) == []


#: Обычная речь, которая в ЭХЕ цепляла гард исходящего (DRF-2702): по одной
#: фразе на класс. Входной гейт их пропускает — это не кризис и не неотложка.
SPOKEN_THAT_TRIPPED_THE_GUARD = [
    pytest.param("Запишите меня на массаж, мой номер 8 905 123 45 67.", id="contact-phone"),
    pytest.param("Моя почта ivan@example.com", id="contact-email"),
    pytest.param("Я выпила парацетамол, можно на массаж?", id="medical"),
    pytest.param("Вы гарантируете результат?", id="promise"),
    pytest.param("Хожу на маникюр раз в месяц.", id="planning"),
    pytest.param("Болит три дня подряд.", id="nag"),
]


class TestEchoStaysOutOfTheGuard:
    """DRF-2702 — гард исходящего проверяет ответ бота, а не слова человека в эхе."""

    REPLY = "Расскажи чуть подробнее?"

    def _voice(self, user_id: int, spoken: str) -> None:
        _provider(spoken)
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=user_id, attachments=[AUDIO]), trace_id=str(uuid.uuid4())
            )

    @pytest.mark.parametrize("spoken", SPOKEN_THAT_TRIPPED_THE_GUARD)
    def test_the_persons_own_words_do_not_block_the_reply(
        self, sent, fake_redis, concierge, settings, spoken
    ):
        from apps.orchestrator.safety.outbound import evaluate_outbound

        settings.VOICE_ECHO_MODE = "always"
        self._voice(70041, spoken)
        ((heard, channel),) = _user_rows(70041)
        assert channel == "voice"
        echoed = f"Я услышала: «{heard}»\n\n{self.REPLY}"
        # Узел не пустой: именно эта склейка до DRF-2702 шла в гард и блокировалась.
        assert evaluate_outbound(echoed).blocked
        assert [c["text"] for c in sent] == [echoed]
        assert [(r.action_type, r.content) for r in _assistant_rows(70041)] == [("", echoed)]

    def test_the_guard_never_sees_the_echo(
        self, sent, fake_redis, concierge, settings, monkeypatch
    ):
        settings.VOICE_ECHO_MODE = "always"
        seen: list[str] = []
        real = max_handler.guard_outbound

        def spy(text, **kwargs):
            seen.append(text)
            return real(text, **kwargs)

        monkeypatch.setattr(max_handler, "guard_outbound", spy)
        self._voice(70042, "привет")
        assert seen[0] == self.REPLY
        assert sent[0]["text"] == f"Я услышала: «привет»\n\n{self.REPLY}"
        assert [t for t in seen if "Я услышала" in t] == []

    def test_a_contact_in_the_bots_reply_is_still_blocked(
        self, sent, fake_redis, monkeypatch, settings
    ):
        """Голос ничего не ослабил: телефон в ОТВЕТЕ бота блокируется, эха у замены нет."""
        from apps.orchestrator.discovery import DiscoveryReply
        from apps.orchestrator.safety.gate import OUTBOUND_ACTION_TYPE
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

        settings.VOICE_ECHO_MODE = "always"
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(
                return_value=DiscoveryReply(
                    text="Телефон мастера: +7 999 123-45-67", persisted=False
                )
            ),
        )
        self._voice(70043, "привет")
        assert [c["text"] for c in sent] == [REPLACEMENT_TEXT]
        assert [r.action_type for r in _assistant_rows(70043)] == [OUTBOUND_ACTION_TYPE]

    def test_a_model_written_echo_lookalike_is_checked(
        self, sent, fake_redis, monkeypatch, settings
    ):
        """Исключение — конструкцией, не формой: «Я услышала…» от модели идёт в гард."""
        from apps.orchestrator.discovery import DiscoveryReply
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

        settings.VOICE_ECHO_MODE = "always"
        lookalike = "Я услышала: «привет»\n\nТелефон мастера: +7 999 123-45-67"
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(return_value=DiscoveryReply(text=lookalike, persisted=False)),
        )
        max_handler.handle_global_max_event(
            _msg(text="привет", user_id=70044), trace_id=str(uuid.uuid4())
        )
        assert [c["text"] for c in sent] == [REPLACEMENT_TEXT]
