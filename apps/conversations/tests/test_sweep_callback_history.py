"""Уборка накопленных сырых ``cb:`` строк — команда ``sweep_callback_history``.

Гейт персистенса починен вперёд (#1325, #1329 и этот PR), но 68 строк уже
лежат в боевой базе и будут читаться консьержем, пока их не уберут. Команда
применяет задним числом ТЕ ЖЕ решения, что приняты в коде, — и здесь это
заперто на данных, а не на словах.

Главное, что проверяется: по умолчанию команда НИЧЕГО НЕ МЕНЯЕТ. Скрипт
уборки боевой истории, у которого сухой прогон окажется не сухим, — это
худший из возможных дефектов в этом файле, поэтому проверка стоит на
пересчитанных строках, а не на отсутствии исключения.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.conversations.management.commands.sweep_callback_history import classify
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user

pytestmark = pytest.mark.django_db

T = "11111111-1111-1111-1111-111111111111"
M = "22222222-2222-2222-2222-222222222222"


def _conversation(user_id: int = 98001):
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    return resolve_active_global_conversation(bot_user)


def _row(conversation, content: str, role: str = "user"):
    return Message.all_tenants.create(
        tenant=conversation.tenant,
        conversation=conversation,
        role=role,
        content=content,
    )


def _contents(conversation, role: str = "user") -> list[str]:
    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role=role)
        .order_by("created_at")
        .values_list("content", flat=True)
    )


# --------------------------------------------------------------------------- #
# 1. Классификация — то же решение, что в коде, и теми же резолверами          #
# --------------------------------------------------------------------------- #
class TestTheVerdictMatchesTheShippedDecision:
    @pytest.mark.parametrize(
        "content",
        [
            f"cb:discover:book:{T}:{M}",
            "cb:discover:",
            f"cb:discover:masters:{T}",
            f"cb:visit:card:{T}",
            f"cb:book:pick_date:{T}",
            f"cb:catalog:services:{T}",
        ],
    )
    def test_card_taps_are_deleted(self, content):
        assert classify(content).action == "delete", content

    def test_a_welcome_tap_is_rewritten_to_its_label(self):
        from apps.skills.welcome.skill import welcome_tap_labels

        labels = welcome_tap_labels()
        assert labels, "клавиатура приветствия пуста — проверка ниже ни о чём"
        payload, label = next(iter(labels.items()))
        verdict = classify(payload)
        assert verdict.action == "rewrite", payload
        assert verdict.new_content == label

    @pytest.mark.parametrize(
        "content",
        [
            "cb:welcome:no_such_button",  # форма верна, кнопку сняли
            "cb:menu:services",  # решения по семейству нет
            "cb:water:add:1",
            "cb:retry:last",
            "хочу маникюр в пензе",  # вообще не тап
        ],
    )
    def test_everything_without_a_shipped_decision_is_left_alone(self, content):
        assert classify(content).action == "skip", content


# --------------------------------------------------------------------------- #
# 2. Сухой прогон действительно сухой                                          #
# --------------------------------------------------------------------------- #
class TestDryRunChangesNothing:
    def test_rows_survive_byte_for_byte(self):
        conversation = _conversation()
        _row(conversation, "хочу маникюр в пензе")
        _row(conversation, f"cb:discover:book:{T}:{M}")
        _row(conversation, "cb:welcome:consent_yes")
        before = _contents(conversation)

        out = StringIO()
        call_command("sweep_callback_history", stdout=out)

        # Положительная стража на тех же данных: отчёт не пуст и он про эти
        # строки — иначе «ничего не изменилось» было бы истиной о пустом
        # прогоне, который просто ничего не нашёл.
        report = out.getvalue()
        assert "cb:discover" in report, report
        assert _contents(conversation) == before

    def test_apply_without_dump_is_refused(self):
        conversation = _conversation()
        _row(conversation, f"cb:discover:book:{T}:{M}")

        with pytest.raises(CommandError, match="--dump"):
            call_command("sweep_callback_history", "--apply")

        assert _contents(conversation) == [f"cb:discover:book:{T}:{M}"]


# --------------------------------------------------------------------------- #
# 3. Применение — и дамп, из которого можно восстановить                       #
# --------------------------------------------------------------------------- #
class TestApplyDoesExactlyWhatItReported:
    def test_cards_go_and_phrases_stay_as_phrases(self, tmp_path):
        from apps.skills.welcome.skill import welcome_tap_labels

        conversation = _conversation()
        # Порог первого контакта считается по ВСЕМ строкам диалога, поэтому
        # набранные реплики здесь не украшение: без них уборка справедливо
        # отказалась бы трогать диалог.
        _row(conversation, "хочу маникюр в пензе")
        _row(conversation, "а в субботу?")
        _row(conversation, "Вот мастера.", role="assistant")
        _row(conversation, f"cb:discover:book:{T}:{M}")
        _row(conversation, f"cb:discover:masters:{T}")
        _row(conversation, "cb:welcome:consent_yes")
        _row(conversation, "cb:menu:services")

        dump = tmp_path / "sweep.jsonl"
        call_command("sweep_callback_history", "--apply", f"--dump={dump}", stdout=StringIO())

        assert _contents(conversation) == [
            "хочу маникюр в пензе",
            "а в субботу?",
            welcome_tap_labels()["cb:welcome:consent_yes"],
            "cb:menu:services",
        ]
        # Ответ бота не тронут ничем.
        assert _contents(conversation, role="assistant") == ["Вот мастера."]

        dumped = [json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()]
        assert {d["content"] for d in dumped} == {
            f"cb:discover:book:{T}:{M}",
            f"cb:discover:masters:{T}",
            "cb:welcome:consent_yes",
        }
        assert {d["action"] for d in dumped} == {"delete", "rewrite"}

    def test_an_existing_dump_is_refused_not_clobbered(self, tmp_path):
        conversation = _conversation()
        _row(conversation, "хочу маникюр")
        _row(conversation, "а в субботу?")
        _row(conversation, f"cb:discover:book:{T}:{M}")
        dump = tmp_path / "sweep.jsonl"
        dump.write_text("уже занято", encoding="utf-8")

        with pytest.raises(CommandError, match="не перезаписывается"):
            call_command("sweep_callback_history", "--apply", f"--dump={dump}")

        assert dump.read_text(encoding="utf-8") == "уже занято"
        assert f"cb:discover:book:{T}:{M}" in _contents(conversation)


# --------------------------------------------------------------------------- #
# 4. Порог первого контакта — уборка не возвращает человеку приветствие        #
# --------------------------------------------------------------------------- #
class TestTheFirstContactThresholdIsRespected:
    """Диалог, обмелевший ниже порога, снова читается как первый контакт.

    ``_conversation_already_under_way`` и
    ``WelcomeSkill._flow_already_established`` считают СТРОКИ. Уборка, которая
    молча опустит диалог ниже порога, пришлёт человеку на пилоте приветствие,
    которое он уже проходил, — это ровно то, чего скрипт уборки делать не
    должен.
    """

    def test_a_conversation_that_would_go_below_is_skipped(self, tmp_path):
        conversation = _conversation(98002)
        _row(conversation, f"cb:discover:book:{T}:{M}")

        out = StringIO()
        dump = tmp_path / "sweep.jsonl"
        call_command("sweep_callback_history", "--apply", f"--dump={dump}", stdout=out)

        assert _contents(conversation) == [f"cb:discover:book:{T}:{M}"]
        assert "порога" in out.getvalue(), out.getvalue()

    def test_force_overrides_it_and_says_so(self, tmp_path):
        conversation = _conversation(98003)
        _row(conversation, f"cb:discover:book:{T}:{M}")
        # Положительная стража: строка действительно лежала до уборки.
        # Без неё «после уборки пусто» прошло бы и на диалоге, где ничего
        # не было, — то есть доказывало бы не уборку, а пустоту.
        assert _contents(conversation) == [f"cb:discover:book:{T}:{M}"]

        out = StringIO()
        dump = tmp_path / "sweep.jsonl"
        call_command(
            "sweep_callback_history",
            "--apply",
            "--force-below-threshold",
            f"--dump={dump}",
            stdout=out,
        )

        assert _contents(conversation) == []
        assert "force-below-threshold" in out.getvalue().lower(), out.getvalue()
