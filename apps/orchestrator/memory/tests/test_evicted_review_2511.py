"""Уходящее из короткой памяти просматривается перед тем как исчезнуть (DRF-2511).

Предмет — два звена: `short_term.append` теперь **возвращает вытесненное**
(до листа вытеснение было ненаблюдаемо вовсе), и `review_evicted` дочитывает
из него явные факты.

### Что чинится, и чего эти узлы НЕ утверждают

Чинится потеря при **недоступности Ayla**: `record_explicit_green_facts`
возвращает ноль, когда личность не разрешилась, и «следующий ход» — уже
другой текст, так что повтора для того сообщения не существует. Просмотр
уходящего и есть тот повтор.

Узлы **не** утверждают, что «память больше не обрывается на двадцати
сообщениях». Явные факты снимаются на входе на каждой реплике; что шаблонный
извлекатель не узнаёт — не узнает и здесь, это тот же извлекатель.

### Почему пара узлов, а не один

Один узел «потерянный факт записан» зеленел бы и на коде, который пишет
**всё подряд** при каждом вытеснении, — то есть измерил бы дублирование, а не
починку. Поэтому рядом стоит второй: факт, уже записанный на входе, при
просмотре **не удваивается**.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term
from apps.orchestrator.memory.evicted_review import review_evicted

pytestmark = pytest.mark.django_db(transaction=True)

FACT = "кстати, я веган"


class _FakeRedis:
    """Списки Redis с честным конвейером: `execute` возвращает результаты.

    Отличие от стенда соседнего файла существенно: там `execute` отдаёт
    пустой список, и вытесненное было бы невидимо. Предмет этого файла —
    ровно то, что возвращает конвейер, поэтому стенд обязан это уметь.
    """

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self._pending: list[tuple[str, tuple[Any, ...]]] = []
        self._buffering = False

    def pipeline(self) -> "_FakeRedis":
        self._pending = []
        self._buffering = True
        return self

    def lrange(self, key: str, start: int, end: int) -> Any:
        # `recall` зовёт `lrange` НАПРЯМУЮ, минуя конвейер. Первая редакция
        # стенда буферизовала оба вызова и отдавала `None` прямому — и узел
        # краснел на стенде, а не на предмете.
        if self._buffering:
            self._pending.append(("lrange", (key, start, end)))
            return None
        return self._slice(key, start, end)

    def rpush(self, key: str, value: str) -> None:
        self._pending.append(("rpush", (key, value)))

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._pending.append(("ltrim", (key, start, end)))

    def expire(self, key: str, ttl: int) -> None:
        self._pending.append(("expire", (key, ttl)))

    def _slice(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        stop = len(lst) if end == -1 else (end + 1 if end >= 0 else len(lst) + end + 1)
        return lst[start:stop]

    def execute(self) -> list[Any]:
        out: list[Any] = []
        for cmd, args in self._pending:
            if cmd == "lrange":
                out.append(self._slice(*args))
            elif cmd == "rpush":
                key, value = args
                self.lists.setdefault(key, []).append(value)
                out.append(len(self.lists[key]))
            elif cmd == "ltrim":
                key, start, end = args
                lst = self.lists.get(key, [])
                self.lists[key] = lst[start:] if end == -1 else lst[start : end + 1]
                out.append(True)
            elif cmd == "expire":
                key, ttl = args
                self.ttls[key] = ttl
                out.append(True)
        self._pending = []
        self._buffering = False
        return out


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def _bot_user(uid: str, *, ayla: bool = True):  # noqa: ANN202
    return resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=uid,
        ayla_user_id=uuid.uuid4() if ayla else None,
    )


def _consent(bot_user, settings) -> None:  # noqa: ANN001
    settings.STRICT_TENANT_SCOPE = "strict"
    record_global_consent(bot_user, source="welcome")


def _current_diet(bot_user) -> Any:  # noqa: ANN001
    """Что человек увидит про диету — по политике ключей, а не сырым фильтром.

    Два различия, на которых я споткнулся сам, и оба стоят того, чтобы быть
    записанными:

    1. **вытеснение — это не мягкое удаление.** `supersede_entries` ставит
       состояние и `soft_deleted_at` не трогает (`memory_writer.py:231`:
       «Supersession is NOT deletion»), поэтому фильтр
       `soft_deleted_at__isnull=True` считает вытесненную строку живой;
    2. **«живые строки» — это не «текущий факт».** `read_personal_context`
       отдаёт оба противоречащих значения одного ключа; собирает их в один
       `read_current_view` → `select_current_facts`, и его же читает подсказка.
       Победитель выбирается по `_currency` = (explicit, created_at, id), то
       есть **по времени записи**.

    Второе и делает ловушку порядка настоящей: строка, записанная мостом из
    старого сообщения, имеет `created_at` = сейчас и потому оказалась бы
    свежее по-настоящему свежего факта.
    """
    from apps.identity.services.memory_key_policy import read_current_view

    facts = read_current_view(bot_user.ayla_user_id).green_facts
    diets = [f.content.get("value") for f in facts if f.content.get("key") == "diet"]
    return diets[0] if len(diets) == 1 else diets


def _fill(conversation_id, fake: _FakeRedis, *, first: str, depth: int = 20) -> None:  # noqa: ANN001
    """Набить окно ровно до края: первое сообщение — `first`."""
    short_term.append(conversation_id, role="user", content=first)
    for i in range(depth - 1):
        short_term.append(conversation_id, role="assistant", content=f"ответ {i}")


class TestВытеснениеСталоНаблюдаемым:
    def test_пока_окно_не_полно_ничего_не_уходит(self, fake_redis: _FakeRedis) -> None:
        cid = uuid.uuid4()

        dropped = short_term.append(cid, role="user", content="привет")

        # Сначала о наличии: сообщение легло. Без этого «вытесненных нет»
        # зеленело бы и на `append`, который вообще ничего не сделал.
        assert [m["content"] for m in short_term.recall(cid)] == ["привет"]
        assert dropped == []

    def test_двадцать_первое_возвращает_первое(self, fake_redis: _FakeRedis, settings) -> None:
        settings.SHORT_TERM_MEMORY_DEPTH = 20
        cid = uuid.uuid4()
        _fill(cid, fake_redis, first=FACT)

        dropped = short_term.append(cid, role="user", content="двадцать первое")

        assert [m["content"] for m in dropped] == [FACT]

    def test_окно_осталось_прежней_глубины(self, fake_redis: _FakeRedis, settings) -> None:
        """Положительная стража: возврат вытесненного не сломал сам трим."""
        settings.SHORT_TERM_MEMORY_DEPTH = 20
        cid = uuid.uuid4()
        _fill(cid, fake_redis, first=FACT)

        short_term.append(cid, role="user", content="двадцать первое")

        assert len(short_term.recall(cid)) == 20

    def test_испорченная_запись_не_валит_чтение(self, fake_redis: _FakeRedis, settings) -> None:
        """Ручная правка оператора не должна ронять ход целиком.

        При глубине 2 и двух лежащих записях этот `append` вытесняет **одну**
        — и это ровно испорченная. Она пропускается с предупреждением, а ход
        продолжается: вытесненных фактов ноль, окно живо.
        """
        settings.SHORT_TERM_MEMORY_DEPTH = 2
        cid = uuid.uuid4()
        key = f"conv:{cid}:msgs"
        fake_redis.lists[key] = ["{не json", json.dumps({"role": "user", "content": FACT})]

        dropped = short_term.append(cid, role="user", content="третье")

        # Сначала о наличии: окно живо и держит целые записи — значит разбор
        # не упал, а пропустил кривую.
        assert [m["content"] for m in short_term.recall(cid)] == [FACT, "третье"]
        assert dropped == []


class TestПотерянноеНаСбоеВозвращается:
    def test_факт_записан_при_уходе_из_окна(self, settings) -> None:
        """Сердце листа: на входе записать не удалось, при уходе — удалось.

        «Не удалось на входе» здесь смоделировано просто: писателя на входе
        не звали вовсе — ровно то состояние, в котором оказывается ход, когда
        Ayla недоступна и `record_explicit_green_facts` вернул ноль.
        """
        bu = _bot_user("ev-recovered")
        _consent(bu, settings)
        assert MemoryEntry.objects.count() == 0  # стража: до просмотра пусто

        written = review_evicted(bu, [{"role": "user", "content": FACT}])

        assert written == 1
        entry = MemoryEntry.objects.get(user_id=bu.ayla_user_id)
        assert entry.content["value"] == "vegan"

    def test_провенанс_остаётся_explicit(self, settings) -> None:
        """Слова человек произнёс — опоздание не меняет автора.

        `inferred` здесь был бы нарушением названного решения: выводимое
        становится памятью только через поток предложений (AYLA-DEC-0024), а
        писатель выводимого намеренно усыплён.
        """
        bu = _bot_user("ev-provenance")
        _consent(bu, settings)

        review_evicted(bu, [{"role": "user", "content": FACT}])

        entry = MemoryEntry.objects.get(user_id=bu.ayla_user_id)
        assert entry.source == MemoryEntry.SOURCE_EXPLICIT
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN


class TestУжеЗаписанноеНеУдваивается:
    def test_второй_раз_не_пишется(self, settings) -> None:
        """Второй узел пары — без него мы измерили бы дублирование.

        Дедуп живёт внутри писателя, и здесь проверяется, что просмотр им
        пользуется, а не обходит.
        """
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-dedup")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, FACT) == 1  # запись на входе

        written = review_evicted(bu, [{"role": "user", "content": FACT}])

        assert written == 0
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 1


class TestЧегоПросмотрНеДелает:
    def test_ответы_ассистента_не_просматриваются(self, settings) -> None:
        """Извлекать «факты о человеке» из своих же реплик — учиться на выдумках."""
        bu = _bot_user("ev-assistant")
        _consent(bu, settings)

        written = review_evicted(bu, [{"role": "assistant", "content": FACT}])

        assert written == 0
        assert MemoryEntry.objects.count() == 0

    def test_без_согласия_не_пишет(self, settings) -> None:
        """Гейт тот же, что у явных фактов, без послаблений."""
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _bot_user("ev-noconsent")

        assert review_evicted(bu, [{"role": "user", "content": FACT}]) == 0
        assert MemoryEntry.objects.count() == 0

    def test_пустое_вытеснение_работы_не_создаёт(self, settings) -> None:
        bu = _bot_user("ev-empty")
        _consent(bu, settings)

        assert review_evicted(bu, []) == 0

    def test_сбой_писателя_не_ломает_ход(self, settings, monkeypatch: pytest.MonkeyPatch) -> None:
        """Память не имеет права стоить человеку ответа."""
        bu = _bot_user("ev-boom")
        _consent(bu, settings)

        def boom(*a: Any, **k: Any) -> int:
            raise RuntimeError("хранилище недоступно")

        monkeypatch.setattr(
            "apps.orchestrator.memory.personal_context.record_explicit_green_facts", boom
        )

        assert review_evicted(bu, [{"role": "user", "content": FACT}]) == 0


class TestСтёртоеПоПросьбеНеВозвращается:
    """Право на забвение: мост не воскрешает то, что человек просил забыть.

    Дыра, которую это закрывает, не видна ни одному из соседних гейтов: дедуп
    писателя строится по **живым** фактам, а сторож забвения стоит на человеке
    целиком. Поштучное стирание не попадает ни туда, ни туда.

    На живом ходу это терпимо — факт вернётся, только если человек **повторит**
    его сам, а повторное заявление его право. Мост дочитывает старое
    сообщение, которого никто не повторял.
    """

    def _erase_all_green(self, bot_user) -> int:  # noqa: ANN001
        from apps.identity.services.memory_deleter import soft_delete_green_entries
        from apps.identity.services.memory_reader import read_green_entries

        rows = read_green_entries(bot_user.ayla_user_id)
        return soft_delete_green_entries(bot_user.ayla_user_id, [r.id for r in rows])

    def test_стёртый_факт_мост_не_возвращает(self, settings) -> None:
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-erased")
        _consent(bu, settings)
        # Положительная стража: факт БЫЛ, иначе «не вернулся» зеленело бы на
        # пустоте — мы бы измерили, что писать было нечего.
        assert record_explicit_green_facts(bu, FACT) == 1
        assert self._erase_all_green(bu) == 1

        written = review_evicted(bu, [{"role": "user", "content": FACT}])

        assert written == 0
        assert not MemoryEntry.objects.filter(
            user_id=bu.ayla_user_id, soft_deleted_at__isnull=True
        ).exists()

    def test_на_живом_ходу_повторное_заявление_по_прежнему_слышат(self, settings) -> None:
        """Ложный вход к предыдущему узлу — и он же граница правила.

        Запрет принадлежит вызову моста, а не хранилищу: человек, сказавший то
        же **снова**, вправе быть услышанным. Безусловный запрет внутри
        писателя отнял бы у него это право навсегда.
        """
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-restated")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, FACT) == 1
        assert self._erase_all_green(bu) == 1

        # Человек говорит это заново — обычный живой путь, без запрета.
        assert record_explicit_green_facts(bu, FACT) == 1
        assert (
            MemoryEntry.objects.filter(
                user_id=bu.ayla_user_id, soft_deleted_at__isnull=True
            ).count()
            == 1
        )

    def test_срок_хранения_это_не_просьба_человека(self, settings) -> None:
        """``ttl_purge`` — не воля человека, и возврату не препятствует.

        Без этого узла список причин можно было бы расширить до «любое
        удаление», и мост замолчал бы там, где человек ничего не просил.
        """
        from apps.identity.models import MemoryEntry as ME
        from apps.identity.services.memory_deleter import soft_delete_green_entries
        from apps.identity.services.memory_reader import read_green_entries
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-ttl")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, FACT) == 1
        rows = read_green_entries(bu.ayla_user_id)
        soft_delete_green_entries(
            bu.ayla_user_id, [r.id for r in rows], reason=ME.DELETION_REASON_TTL_PURGE
        )

        assert review_evicted(bu, [{"role": "user", "content": FACT}]) == 1


class TestОбратногоХодаПамятиНеБывает:
    """Старое сообщение не вытесняет более свежий факт о том же ключе.

    Ловушка тонкая и молчаливая: для ключа единичной кратности запись
    **вытесняет** прежние живые строки (`supersede`, reason=changed) — верно,
    когда человек только что поправил себя. Мост читает **старое** сообщение,
    и без запрета «я веган» из первой реплики победило бы «я вегетарианка» из
    пятой. Узел «факт записан» при этом остался бы зелёным.
    """

    OLD = "кстати, я веган"
    NEW = "я вегетарианка"

    def test_свежее_заявление_побеждает_старое_сообщение(self, settings) -> None:
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-order")
        _consent(bu, settings)
        # Так было на живом ходу: первое не записалось (Ayla лежала), пятое
        # записалось. Положительная стража — свежий факт действительно живой.
        assert record_explicit_green_facts(bu, self.NEW) == 1
        assert _current_diet(bu) == "vegetarian"

        # Мост доходит до СТАРОГО сообщения — оно уходит из окна последним.
        review_evicted(bu, [{"role": "user", "content": self.OLD}])

        assert _current_diet(bu) == "vegetarian", "свежее должно устоять"

    def test_на_живом_ходу_исправление_себя_по_прежнему_работает(self, settings) -> None:
        """Ложный вход: запрет принадлежит мосту, а не писателю вообще.

        Человек, поправивший себя в разговоре, обязан быть услышан — иначе мы
        починили бы обратный ход ценой прямого.
        """
        from apps.orchestrator.memory.personal_context import record_explicit_green_facts

        bu = _bot_user("ev-correction")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, self.OLD) == 1
        assert _current_diet(bu) == "vegan"  # стража: сначала было так

        assert record_explicit_green_facts(bu, self.NEW) == 1

        assert _current_diet(bu) == "vegetarian"

    def test_пустой_ключ_мост_заполняет(self, settings) -> None:
        """Положительная стража к запрету: он не выключил мост целиком."""
        bu = _bot_user("ev-order-empty")
        _consent(bu, settings)

        assert review_evicted(bu, [{"role": "user", "content": self.OLD}]) == 1
        assert _current_diet(bu) == "vegan"
