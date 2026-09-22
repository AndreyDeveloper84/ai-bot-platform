"""DRF-2134 — матрица «забудь всё»: каждое хранилище ПДн, исход по каждому, ПОСЛЕ свипа.

«Забудь всё» доказано по частям: развёртка (DRF-1370), обезличивание диалога
(DRF-1369), durable-стирание в каталог с readback (DRF-1950/1984). Доказательства
**по множеству целиком** не было: никто не сеял по строке в каждом хранилище,
не прогонял всю цепочку и не смотрел, что осталось. Этот модуль — оно.

# Состав множества выводится, не перечисляется

Источник — ``apps.identity.export_coverage``: реестр того, что бот хранит о
человеке (``SECTIONS`` + ``EXCLUSIONS`` покрывают каждый слот
``personal_fields.PERSONAL_FIELDS``, ``NON_REGISTRY_STORES`` — хранилища вне
формы «колонка»; ``KNOWN_LIMITS`` называет каталожный профиль). Хранилище
берётся из слота отбрасыванием поля: ``identity.ClientProfile.ltv`` →
``identity.ClientProfile``; ``memory_key:*`` → зелёные ``MemoryEntry``.

Две проверки в обе стороны, как у ``test_export_coverage``:

* хранилище объявлено в экспорте, но исхода в :data:`OUTCOMES` нет → красный
  («хранилище без исхода»);
* хранилище есть в :data:`OUTCOMES`, но экспорт о нём молчит → красный
  («хранилище вне экспорта — красный сам по себе»). Те, о которых экспорт
  молчит СЕГОДНЯ, названы в :data:`UNDECLARED_IN_EXPORT` и держатся на
  ``xfail(strict=True)``: лист следом, починка снимает xfail.

# Исход

``DELETE`` — 0 живых строк (для ``MemoryEntry`` «живая» = без надгробия:
контур не удаляет физически, надгробие и есть его удаление; для Redis — ключа
нет). ``ANONYMISE`` — строка есть, поля с ПДн пусты. ``RETAIN`` — строка есть,
не изменилась, и причина названа в реестре. Хранилище без исхода → красный.

# Цепочка

``request_forget_all`` → ``sweep_forget_all`` → ``sweep_pending_forget_all``
(второй проход: ничего не осталось) → ``erase_declared_profile_status`` с
каталогом, чей readback отвечает «стёрто». Сосед — второй человек с теми же
строками во всех хранилищах — после цепочки не изменился ни в одном.

# Дыры — не чинятся здесь

Каждая — ``xfail(strict=True)`` с текстом «лист следом» (:data:`ERASURE_HOLES`).
Когда починка снимет дыру, strict-xfail покраснеет сам и потребует убрать метку.

# Пределы

Локально sqlite: Postgres CHECK/RLS этот модуль не видит — их держат
``test_memory_entry_constraints`` и ``test_db_security``. Redis подменён
(ключи, не сервер). Каталог подменён: readback «стёрто» здесь — допущение,
сам readback держит ``test_ayla_erasure_retry``. ``FoodDiaryEntry``,
``ScanResult``, фото в MinIO — в коде бота таких хранилищ нет (grep по
``apps/``): если они существуют, то на стороне каталога/ai-core, и эта матрица
их не видит — названо, не подразумевается.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.conversations.models import (
    AiDraft,
    ArchivedMessage,
    Conversation,
    Message,
    StaffAssistantMessage,
    StaffAssistantThread,
)
from apps.identity.export_coverage import (
    EXCLUSIONS,
    KNOWN_LIMITS,
    NON_REGISTRY_SECTIONS,
    NON_REGISTRY_STORES,
    SECTIONS,
)
from apps.identity.models import (
    AylaErasureJob,
    BotUser,
    ClientProfile,
    MemoryEntry,
    UserPersonalContext,
    UserPreferences,
)
from apps.identity.services.forget_all_sweep import (
    pending_forget_all_user_ids,
    sweep_forget_all,
    sweep_pending_forget_all,
)
from apps.identity.services.memory_deleter import request_forget_all
from apps.identity.services.personal_context import GateStatus
from apps.loyalty.models import LoyaltyAccount
from apps.orchestrator.memory.ayla_bridge import erase_declared_profile_status
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

DELETE = "DELETE"
ANONYMISE = "ANONYMISE"
RETAIN = "RETAIN"

LEAF_TO_FOLLOW = "лист следом"

#: Каталожный профиль (users.UserPersonalContext в beautygo_backend) — последняя
#: строка матрицы (DRF-1984): «удалено» не говорится, пока readback не вернул
#: ``erased``.
CATALOG_STORE = "catalog.users.UserPersonalContext"

#: DRF-2214 — что каталог запомнил вне профиля и что «забудь всё» стирает там
#: же, через тот же C5.2 и тот же readback C5.3 (beautygo_backend #530/#541):
#: разделы выгрузки ``ayla`` (#544). Исход у всех — исход ``CATALOG_STORE``.
CATALOG_SECTIONS: tuple[str, ...] = (
    "catalog.goals",
    "catalog.wellness_plan",
    "catalog.nutrition_profile",
    "catalog.food_diary",
    "catalog.shown_hints",
)
CATALOG_STORES = frozenset({CATALOG_STORE, *CATALOG_SECTIONS})

#: Телефон в тестовом диапазоне (pii_guard: префикс 999) — направляется в
#: диалог, чтобы обезличивание было проверяемо, а не предположено.
_PHONE = "+7 999 123-45-67"
_SUMMARY = "Мария, 34, ходит на маникюр раз в три недели, любит тишину в кресле."


# ── Реестр исходов ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """Исход одного хранилища под «забудь всё» и кто его исполняет."""

    outcome: str
    executor: str
    reason: str


#: Хранилище → исход. Порядок — порядок строк матрицы; каталог последним.
OUTCOMES: dict[str, Outcome] = {
    "identity.MemoryEntry:green": Outcome(
        DELETE,
        "forget_all_sweep.sweep_forget_all → memory_deleter.soft_delete_green_entries",
        "надгробие status=deleted, deletion_reason=forget_all; живых строк 0",
    ),
    "identity.MemoryEntry:yellow": Outcome(
        DELETE,
        "forget_all_sweep.sweep_forget_all — все три зоны одним свипом (DRF-2180)",
        "человек сказал «забудь всё»; жёлтая зона — личные факты с TTL 365 дней",
    ),
    "identity.MemoryEntry:red": Outcome(
        DELETE,
        "forget_all_sweep.sweep_forget_all — плюс строка RedZoneAccessLog "
        "на каждую снятую строку (DRF-2180)",
        "специальная категория (152-ФЗ ст. 10) не должна переживать «забудь всё»",
    ),
    "identity.UserPersonalContext": Outcome(
        ANONYMISE,
        "forget_all_sweep.sweep_forget_all",
        "summary/display_name_preferred/language_preferred → NULL, "
        "soft_deleted_at проставлен; minor_lock — защита, а не факт, остаётся",
    ),
    "conversations.Message": Outcome(
        ANONYMISE,
        "conversations.erasure.anonymize_dialogue (из sweep_forget_all, cutoff = момент запроса)",
        "content/rendered_text → '', action_data/tool_call → NULL; строка остаётся",
    ),
    "conversations.ArchivedMessage": Outcome(
        RETAIN,
        "conversations.erasure.anonymize_dialogue пишет; purge_expired_archived_messages стирает",
        "OD_MEMORY §4: обезличенная переписка живёт 90 дней "
        "(ANONYMIZED_DIALOGUE_RETENTION_DAYS) для разбора спора о брони; "
        "прямые идентификаторы вырезаны",
    ),
    "conversations.AiDraft": Outcome(
        ANONYMISE,
        "conversations.erasure.anonymize_dialogue",
        "content → '' (черновик мастера цитирует клиента дословно)",
    ),
    "redis.short_term": Outcome(
        DELETE,
        "conversations.erasure._clear_redis_stores → short_term.clear",
        "окно сырых реплик (conv:<id>:msgs) удаляется, не ждёт TTL 24 ч",
    ),
    "redis.pii_tokenmap": Outcome(
        DELETE,
        "conversations.erasure._clear_redis_stores → pii_tokenizer.clear_conversation",
        "обратная карта токенов rev:<PHONE_…> → настоящий номер удаляется",
    ),
    "redis.ingress_stream": Outcome(
        DELETE,
        "conversations.erasure._purge_raw_entries → ingress.streams.purge_person_entries",
        "сырые тела вебхуков человека до момента запроса удаляются из ingress:* и "
        "их :dlq (DRF-2220); тело, отправителя которого не прочитать, уходит по "
        "INGRESS_RAW_RETENTION_HOURS",
    ),
    "conversations.Conversation": Outcome(
        DELETE,
        "conversations.erasure.anonymize_dialogue — skill_state={} тем же "
        "обновлением, что и anonymized_through (DRF-2181)",
        "skill_state держит незавершённые анкеты (питание: вес/рост/цель); "
        "после «забудь всё» должен быть пуст",
    ),
    "conversations.StaffAssistantMessage": Outcome(
        RETAIN,
        "privacy.delete_personal_data (каскад удаления аккаунта, DRF-1276) — не «забудь всё»",
        "рабочая поверхность сотрудника, не память о клиенте; стирается удалением аккаунта",
    ),
    "identity.UserPreferences": Outcome(
        RETAIN,
        "никто по замыслу (forget_all_sweep, docstring); privacy.delete_personal_data удаляет строку",
        "тумблеры уведомлений и день рождения — форма, которую человек ведёт сам; "
        "удаление вернуло бы notify_* в True и включило бы отключённые рассылки",
    ),
    "identity.ClientProfile": Outcome(
        RETAIN,
        "identity.services.recompute (пересчёт ежедневно); каскад удаления аккаунта — CASCADE по bot_user",
        "ПРЕДЛОЖЕНИЕ (решение владельца требуется): вычисляемый снимок RFM/LTV "
        "из бронирований и платежей, которые хранятся по закону; DELETE снимка "
        "пересчитался бы назавтра из тех же строк — фикция на сутки. "
        "Инъекция в промпт (DRF-1258) — отдельный вопрос гейта, не удаления",
    ),
    "loyalty.LoyaltyAccount": Outcome(
        RETAIN,
        "никто — export_coverage.REASONS['transactional_ledger']",
        "учётная запись операций, установленный законом срок хранения; "
        "«не стирается по „забудь всё“» — дословно из реестра экспорта",
    ),
    "identity.BotUser": Outcome(
        RETAIN,
        "privacy.delete_personal_data (D3, удаление аккаунта) — другой глагол",
        "оболочка канала: телефон/имя/аватар человек видит и правит сам; "
        "«забудь всё» — про память, «удали меня» — про оболочку",
    ),
    "consent.ConsentRecord": Outcome(
        RETAIN,
        "никто — append-only; withdraw() ставит withdrawn_at, строку не удаляет",
        "доказательство факта согласия и отзыва (152-ФЗ ст. 9): удалить — "
        "значит потерять, чем подтверждается законность обработки до отзыва",
    ),
    "tenancy.TenantStaff": Outcome(
        RETAIN,
        "staff_revoke / каскад удаления аккаунта — не «забудь всё»",
        "решение владельца 20.09 (§58): роль в салоне — доступ, не память; "
        "снимается отзывом роли, а не «забудь всё»",
    ),
    "tenancy.StaffInvite": Outcome(
        RETAIN,
        "никто — used_by SET_NULL при удалении аккаунта; revoke ставит revoked_at",
        "решение владельца 20.09 (§58): след, как человек попал в штат "
        "(код, срок, кем выдан); note — свободный текст выдавшего",
    ),
    # DRF-2214 — три хранилища бота, которые «забудь всё» не трогало.
    "recommendation.Recommendation": Outcome(
        ANONYMISE,
        "forget_all_sweep.sweep_forget_all → anonymise_recommendations (DRF-2214)",
        "why → [], facts → {}, goal_id → '': причины дословно и факты — слова "
        "человека, ключ цели держал бы знание о стёртой цели (#526). Строка "
        "остаётся для атрибуции B13 (reaction, booking_id); alternatives — "
        "курируемая таблица владельца, не данные о человеке, остаются",
    ),
    "redis.dre_state": Outcome(
        DELETE,
        "conversations.erasure._clear_redis_stores → decision_readiness.state.clear",
        "состояние разговора движка готовности (dre:state:<id>: слоты со "
        "сказанным человеком) удаляется, не ждёт TTL 2 ч",
    ),
    "nutrition_proactive:settings": Outcome(
        RETAIN,
        "никто — «настройки уведомлений остаются» (текст команды «забудь всё»)",
        "daily_report_time, water_reminders, opted_out_at — тумблеры, которые "
        "человек ведёт сам; стереть — значит молча выключить то, что он включил",
    ),
    "nutrition_proactive:observations": Outcome(
        DELETE,
        "forget_all_sweep.sweep_forget_all → forget_nutrition_observations (DRF-2214)",
        "water.* (сколько человек выпил — данные о здоровье) и last_report_date",
    ),
    "nutrition_proactive:journal": Outcome(
        RETAIN,
        "никто — антиспам читает журнал (weekly_sent_count, surface_ignored_streak)",
        "outbox: только время и вид отправки, без содержимого — недельный "
        "бюджет и серия игнорирования продолжают работать",
    ),
    **{
        section: Outcome(
            DELETE,
            "ayla_erasure.erase_with_readback — C5.2 стирает запомненное каталогом "
            "(DRF-2214, beautygo_backend #530), readback C5.3 считает остаток (#541)",
            "«удалено» — только когда readback вернул erased: remembered_rows == 0 "
            "по каждой личности",
        )
        for section in CATALOG_SECTIONS
    },
    CATALOG_STORE: Outcome(
        DELETE,
        "ayla_erasure.erase_with_readback (DELETE → readback erasure-status; DRF-1950/1984)",
        "«удалено» — только после readback erased по каждой личности; "
        "до того — «удаление запущено», задание повторяет",
    ),
}

#: Хранилища ПДн, о которых export_coverage сегодня МОЛЧИТ. Каждое — находка:
#: лист следом; когда строка появится в реестре экспорта, strict-xfail покраснеет.
UNDECLARED_IN_EXPORT: dict[str, str] = {
    # DRF-2183 закрыл четыре строки: согласия объявлены выгруженными
    # (`NON_REGISTRY_SECTIONS`), черновики мастера и оба хранилища Redis —
    # невыгруженными с причиной. Метки сняты, потому что strict-xfail
    # покраснел сам.
    "tenancy.TenantStaff": "решение владельца 20.09 (§58): хранилище с исходом — в экспорте нет",
    "tenancy.StaffInvite": "решение владельца 20.09 (§58): хранилище с исходом — в экспорте нет",
}

#: Хранилища, где сегодняшняя цепочка НЕ даёт ожидаемого исхода. Лист следом.
ERASURE_HOLES: dict[str, str] = {
    # DRF-2180 закрыл обе строки MemoryEntry: свип снимает все три зоны, на
    # красную — строка RedZoneAccessLog. DRF-2181 закрыл Conversation:
    # anonymize_dialogue опустошает skill_state. Метки сняты, потому что
    # strict-xfail покраснел сам, как и задумано этим реестром.
}


# ── Вывод состава из реестра экспорта ────────────────────────────────────────


def _store_of(slot: str) -> str:
    """Слот реестра → хранилище. ``memory_key:*`` — зелёные MemoryEntry."""

    if slot.startswith("memory_key:"):
        return "identity.MemoryEntry:green"
    if ":" in slot:
        return slot
    app, model = slot.split(".")[:2]
    return f"{app}.{model}"


def stores_declared_by_export_coverage() -> set[str]:
    declared = {
        _store_of(slot)
        for slot in (*SECTIONS, *EXCLUSIONS, *NON_REGISTRY_STORES, *NON_REGISTRY_SECTIONS)
    }
    if any(re.search(r"users\.UserPersonalContext", limit) for limit in KNOWN_LIMITS):
        declared.add(CATALOG_STORE)
    return declared


def _membership_params() -> list[Any]:
    params = []
    for store in OUTCOMES:
        marks = []
        if store in UNDECLARED_IN_EXPORT:
            marks.append(
                pytest.mark.xfail(
                    strict=True, reason=f"{LEAF_TO_FOLLOW}: {UNDECLARED_IN_EXPORT[store]}"
                )
            )
        params.append(pytest.param(store, marks=marks, id=store))
    return params


def _outcome_params() -> list[Any]:
    params = []
    for store in OUTCOMES:
        marks = []
        if store in ERASURE_HOLES:
            marks.append(
                pytest.mark.xfail(strict=True, reason=f"{LEAF_TO_FOLLOW}: {ERASURE_HOLES[store]}")
            )
        params.append(pytest.param(store, marks=marks, id=store))
    return params


class TestTheCompositionIsDerivedNotRecalled:
    def test_the_derivation_sees_the_registry(self):
        """Пустой вывод сделал бы обе проверки ниже вакуумными — сперва присутствие."""

        declared = stores_declared_by_export_coverage()
        assert "identity.MemoryEntry:green" in declared
        assert "identity.UserPersonalContext" in declared
        assert CATALOG_STORE in declared
        assert len(declared) >= 10, sorted(declared)

    def test_every_declared_store_has_an_outcome(self):
        """Хранилище в экспорте без исхода здесь — красный сам по себе."""

        declared = stores_declared_by_export_coverage()
        assert declared, "вывод из реестра пуст — сравнивать не с чем"
        missing = sorted(declared - set(OUTCOMES))
        assert not missing, (
            "хранилища из export_coverage без исхода в матрице «забудь всё» — "
            f"добавить в OUTCOMES с исходом и причиной: {missing}"
        )

    @pytest.mark.parametrize("store", _membership_params())
    def test_every_store_with_an_outcome_is_declared_in_the_export(self, store):
        """Хранилище вне экспорта — красный сам по себе (лист: DRF-2134 §1)."""

        assert store in stores_declared_by_export_coverage(), (
            f"{store} хранит ПДн и имеет исход под «забудь всё», но реестр экспорта "
            "(apps/identity/export_coverage.py) о нём молчит"
        )

    def test_every_outcome_names_its_executor_and_reason(self):
        shrugs = [
            store
            for store, o in OUTCOMES.items()
            if o.outcome not in {DELETE, ANONYMISE, RETAIN}
            or len(o.executor.strip()) < 8
            or len(o.reason.strip()) < 24
        ]
        assert shrugs == [], shrugs  # empty-assert-ok: OUTCOMES — константа модуля, не выборка

    def test_the_catalog_readback_is_the_last_row(self):
        assert list(OUTCOMES)[-1] == CATALOG_STORE

    def test_holes_and_undeclared_name_real_stores(self):
        unknown = sorted((set(ERASURE_HOLES) | set(UNDECLARED_IN_EXPORT)) - set(OUTCOMES))
        assert unknown == [], unknown  # empty-assert-ok: константы модуля, не выборка


# ── Подмены: Redis и каталог ─────────────────────────────────────────────────


class _FakeRedis:
    """Ключи, не сервер. ``delete`` — единственный глагол стирания у обоих модулей."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.deleted: list[str] = []
        # DRF-2220 — ingress streams: stream → [(id, fields)].
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1 if self.store.pop(key, None) is not None else 0

    @staticmethod
    def _bound(raw: str, *, upper: bool) -> tuple[float, float, bool]:
        # (ms, seq, exclusive). An id without «-seq» covers the whole
        # millisecond, as in Redis: as an upper bound it reaches every seq.
        exclusive = raw.startswith("(")
        raw = raw.lstrip("(")
        if raw == "-":
            return (float("-inf"), 0, exclusive)
        if raw == "+":
            return (float("inf"), 0, exclusive)
        ms, _, seq = raw.partition("-")
        default_seq = float("inf") if upper else 0
        return (float(ms), float(seq) if seq else default_seq, exclusive)

    def xrange(self, stream: str, min: str = "-", max: str = "+", count: int | None = None):  # noqa: A002
        lo_ms, lo_seq, lo_ex = self._bound(min, upper=False)
        hi_ms, hi_seq, hi_ex = self._bound(max, upper=True)
        out = []
        for entry_id, fields in self.streams.get(stream, []):
            ms, _, seq = entry_id.partition("-")
            key = (float(ms), float(seq))
            if key < (lo_ms, lo_seq) or (lo_ex and key == (lo_ms, lo_seq)):
                continue
            if key > (hi_ms, hi_seq) or (hi_ex and key == (hi_ms, hi_seq)):
                continue
            out.append((entry_id, dict(fields)))
        return out[:count] if count else out

    def xdel(self, stream: str, *entry_ids: str) -> int:
        before = len(self.streams.get(stream, []))
        self.streams[stream] = [e for e in self.streams.get(stream, []) if e[0] not in entry_ids]
        return before - len(self.streams[stream])


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    from apps.llm import pii_tokenizer
    from apps.orchestrator.memory import short_term

    from apps.ingress import streams

    from apps.orchestrator.decision_readiness import state as dre_state

    fake = _FakeRedis()
    monkeypatch.setattr(dre_state, "_redis_client", lambda: fake)
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


def _erased_status() -> dict:
    return {
        "erased": True,
        "identities": [{"kind": "account", "context_row": "tombstone", "erased": True}],
    }


def _holds_values_status() -> dict:
    return {
        "erased": False,
        "identities": [{"kind": "account", "context_row": "holds_values", "erased": False}],
    }


class _FakeCatalog:
    """Каталог как его видит бот: DELETE и readback. Readback — по сценарию."""

    def __init__(self, status: dict) -> None:
        self.status = status
        self.deleted_for: list[str] = []
        self.readback_for: list[str] = []

    def delete_personal_data(self, *, ayla_user_id: str, external_user_id: str) -> None:
        self.deleted_for.append(ayla_user_id)

    def get_erasure_status(self, *, ayla_user_id: str, external_user_id: str) -> dict:
        self.readback_for.append(ayla_user_id)
        return self.status

    def close(self) -> None:
        pass


# ── Посев: один человек во всех хранилищах ───────────────────────────────────


@dataclass
class Person:
    label: str
    tenant: Tenant
    bot_user: BotUser
    ayla_user_id: uuid.UUID
    upc: UserPersonalContext
    conversation: Conversation
    entries: dict[str, MemoryEntry]
    staff_thread: StaffAssistantThread


def _memory_entry(upc: UserPersonalContext, zone: str, label: str) -> MemoryEntry:
    if zone == MemoryEntry.SENSITIVITY_GREEN:
        return MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=zone,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            kind="lifestyle",
            status=MemoryEntry.STATUS_ACTIVE,
            content={"key": "diet", "value": f"vegan-{label}"},
        )
    if zone == MemoryEntry.SENSITIVITY_YELLOW:
        return MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=zone,
            source=MemoryEntry.SOURCE_INFERRED,
            last_inferred_at=timezone.now(),
            consent_at=timezone.now(),
            kind="other",
            content={"key": "family", "value": f"двое детей-{label}"},
        )
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=timezone.now(),
        content={"marker": f"RED_CANARY-{label}"},
        source_tenant_id=uuid.uuid4(),
    )


#: DRF-2220 — the stream the seeded raw entry lands in; must be one the purge
#: scans (a registered ingress stream), checked by the matrix test below.
_INGRESS_STREAM = "ingress:max_global"
_INGRESS_SEQ = [0]


def seed_person(tenant: Tenant, fake_redis: _FakeRedis, label: str) -> Person:
    """По одной строке в КАЖДОМ хранилище из :data:`OUTCOMES` (кроме каталога — он подменён)."""

    from apps.catalog.models import CatalogMaster
    from apps.recommendation.models import Recommendation

    ayla_user_id = uuid.uuid4()
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"fam-{label}-{uuid.uuid4().hex[:8]}",
        chat_id=f"fam-{label}",
        ayla_user_id=ayla_user_id,
        phone=_PHONE,
        display_name=f"Мария {label}",
        client_name=f"Мария Иванова {label}",
        context={
            "tone": f"warm-{label}",
            # DRF-2214 — настройки остаются, наблюдения уходят, журнал остаётся.
            "nutrition_proactive": {
                "daily_report_time": "21:00",
                "water_reminders": True,
                "opted_out_at": None,
                "last_report_date": "2026-09-20",
                "water": {
                    "date": "2026-09-20",
                    "sent": 2,
                    "last_total_ml": 1400,
                    "ignored_streak": 1,
                },
                "outbox": [{"surface": "report", "sent_at": "2026-09-20T18:00:00+00:00"}],
            },
        },
    )
    upc = UserPersonalContext.objects.create(
        user_id=ayla_user_id,
        summary=f"{_SUMMARY} [{label}]",
        display_name_preferred=f"Маша {label}",
        language_preferred="ru",
        minor_lock=True,
    )
    entries = {
        zone: _memory_entry(upc, zone, label)
        for zone in (
            MemoryEntry.SENSITIVITY_GREEN,
            MemoryEntry.SENSITIVITY_YELLOW,
            MemoryEntry.SENSITIVITY_RED,
        )
    }

    # Диалог — ДО момента запроса: cutoff свипа = forget_all_requested_at.
    earlier = timezone.now() - timedelta(minutes=5)
    conversation = Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        skill_state={"nutrition_intake": {"weight_kg": 61, "height_cm": 168, "label": label}},
    )
    Conversation.all_tenants.filter(pk=conversation.pk).update(created_at=earlier)
    for role, text in (
        ("user", f"мой телефон {_PHONE}, я веган [{label}]"),
        ("assistant", "Записала."),
    ):
        message = Message.all_tenants.create(
            tenant=tenant,
            conversation=conversation,
            role=role,
            content=text,
            rendered_text=text,
            action_data={"offer": f"вариант для {label}"},
        )
        Message.all_tenants.filter(pk=message.pk).update(created_at=earlier)
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=f"Анна {label}",
        external_id=uuid.uuid4().int % 10**7,
        external_updated_at=timezone.now(),
    )
    AiDraft.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        master=master,
        content=f"Здравствуйте! Вы писали, что вы веган [{label}]",
    )
    fake_redis.store[f"conv:{conversation.id}:msgs"] = [f"raw-{label}"]
    fake_redis.store[f"pii_tokenmap:{conversation.id}"] = {"rev:PHONE_1": _PHONE}
    # DRF-2214 — состояние разговора движка готовности: слот со сказанным.
    fake_redis.store[f"dre:state:{conversation.id}"] = json.dumps(
        {"slots": {"budget": {"state": "known", "value": f"до 3000 [{label}]"}}}, ensure_ascii=False
    )
    Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="weight",
        what="Лимфодренажный массаж",
        subline="курс 5 сеансов",
        why=[f"вы сказали, что хотите к свадьбе сестры [{label}]"],
        facts={"goal": "вес", "answer": f"после родов [{label}]"},
        alternatives=[{"what": "Прессотерапия", "subline": "курс"}],
        fingerprint="absence:weight",  # DRF-2308: ключ цели открытым текстом
    )
    # DRF-2220 — the raw webhook of one of those turns, left in the stream
    # (a failed entry: a processed one is already gone). Its id is the
    # enqueue millisecond, before the request like the turns themselves.
    _INGRESS_SEQ[0] += 1
    fake_redis.streams.setdefault(_INGRESS_STREAM, []).append(
        (
            f"{int(earlier.timestamp() * 1000)}-{_INGRESS_SEQ[0]}",
            {
                "data": json.dumps(
                    {
                        "update_type": "message_created",
                        "message": {
                            "sender": {"user_id": bot_user.channel_user_id},
                            "recipient": {"chat_id": bot_user.chat_id},
                            "body": {"text": f"я веган, мой номер {_PHONE} [{label}]"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "trace_id": "",
                "resolved_tenant_id": "",
            },
        )
    )

    staff_thread = StaffAssistantThread.all_tenants.create(
        tenant=tenant, bot_user=bot_user, role_at_open="admin"
    )
    StaffAssistantMessage.all_tenants.create(
        tenant=tenant, thread=staff_thread, seq=1, role="user", content=f"диктовка {label}"
    )

    UserPreferences.all_tenants.create(
        bot_user=bot_user,
        tenant=tenant,
        notify_reminders=False,
        notify_retention=False,
        notify_promo=False,
        notify_birthday=True,
        birthday_date=timezone.now().date().replace(month=3, day=8),
    )
    # ClientProfile создаётся сигналом при создании BotUser — заполняем.
    ClientProfile.all_tenants.filter(bot_user=bot_user).update(
        recency_days=7,
        frequency_visits=5,
        monetary_total=12500,
        rfm_segment="champion",
        ltv=12500,
        preferred_master_id=f"m-{label}",
        sentiment_score=0.8,
    )
    LoyaltyAccount.all_tenants.create(tenant=tenant, customer=bot_user, balance=350, enrolled=True)
    ConsentRecord.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MEMORY_GREEN,
        granted=True,
        source=f"test-{label}",
        document_version="v1",
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.ADMIN)
    StaffInvite.all_tenants.create(
        tenant=tenant,
        role=StaffInvite.Role.ADMIN,
        code_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=timezone.now() + timedelta(days=1),
        used_at=timezone.now(),
        used_by=bot_user,
        note=f"для Марии {label}",
    )
    return Person(
        label=label,
        tenant=tenant,
        bot_user=bot_user,
        ayla_user_id=ayla_user_id,
        upc=upc,
        conversation=conversation,
        entries=entries,
        staff_thread=staff_thread,
    )


# ── Снимок состояния по хранилищу ────────────────────────────────────────────


def _live_entries(person: Person, zone: str) -> list[Any]:
    return list(
        MemoryEntry.objects.filter(
            user_id=person.ayla_user_id, sensitivity_zone=zone, soft_deleted_at__isnull=True
        )
        .order_by("created_at")
        .values("id", "status", "content")
    )


def _tombstoned_entries(person: Person, zone: str) -> list[Any]:
    return list(
        MemoryEntry.objects.filter(
            user_id=person.ayla_user_id, sensitivity_zone=zone, soft_deleted_at__isnull=False
        ).values("status", "deletion_reason", "delete_requested_at")
    )


def _rows(model_manager, **filters) -> list[Any]:
    return list(model_manager.filter(**filters).order_by("pk").values())


def snapshot(store: str, person: Person, fake_redis: _FakeRedis) -> Any:
    """Что лежит в хранилище про этого человека. Сравнивается «до» и «после»."""

    bu = person.bot_user
    if store == "identity.MemoryEntry:green":
        return _live_entries(person, MemoryEntry.SENSITIVITY_GREEN)
    if store == "identity.MemoryEntry:yellow":
        return _live_entries(person, MemoryEntry.SENSITIVITY_YELLOW)
    if store == "identity.MemoryEntry:red":
        return _live_entries(person, MemoryEntry.SENSITIVITY_RED)
    if store == "identity.UserPersonalContext":
        return _rows(UserPersonalContext.objects, user_id=person.ayla_user_id)
    if store == "conversations.Message":
        return list(
            Message.all_tenants.filter(conversation=person.conversation)
            .order_by("created_at")
            .values("content", "rendered_text", "action_data", "tool_call")
        )
    if store == "conversations.ArchivedMessage":
        return list(
            ArchivedMessage.all_tenants.filter(conversation=person.conversation)
            .order_by("original_created_at")
            .values("body", "rendered_body", "reason", "retention_until")
        )
    if store == "conversations.AiDraft":
        return list(AiDraft.all_tenants.filter(conversation=person.conversation).values("content"))
    if store == "redis.short_term":
        return fake_redis.store.get(f"conv:{person.conversation.id}:msgs")
    if store == "redis.pii_tokenmap":
        return fake_redis.store.get(f"pii_tokenmap:{person.conversation.id}")
    if store == "redis.ingress_stream":
        from apps.ingress.streams import raw_streams

        # «No entries» reads as None, the same «key is gone» the other Redis
        # stores report, so the shared DELETE rule applies unchanged.
        return [
            fields["data"]
            for stream in raw_streams()
            for _entry_id, fields in fake_redis.streams.get(stream, [])
            if json.loads(fields["data"])["message"]["sender"]["user_id"] == bu.channel_user_id
        ] or None
    if store == "conversations.Conversation":
        return list(
            Conversation.all_tenants.filter(pk=person.conversation.pk).values(
                "skill_state", "anonymized_through", "anonymized_reason"
            )
        )
    if store == "conversations.StaffAssistantMessage":
        return list(
            StaffAssistantMessage.all_tenants.filter(thread=person.staff_thread).values(
                "content", "role", "seq"
            )
        )
    if store == "identity.UserPreferences":
        return _rows(UserPreferences.all_tenants, bot_user=bu)
    if store == "identity.ClientProfile":
        return list(
            ClientProfile.all_tenants.filter(bot_user=bu).values(
                "recency_days",
                "frequency_visits",
                "monetary_total",
                "rfm_segment",
                "ltv",
                "preferred_master_id",
                "sentiment_score",
            )
        )
    if store == "loyalty.LoyaltyAccount":
        return list(
            LoyaltyAccount.all_tenants.filter(customer=bu).values("balance", "tier", "enrolled")
        )
    if store == "identity.BotUser":
        rows = list(
            BotUser.all_tenants.filter(pk=bu.pk).values(
                "phone", "display_name", "client_name", "context", "timezone", "deleted_at"
            )
        )
        # DRF-2214 — подключ nutrition_proactive — свои три строки матрицы
        # (настройки / наблюдения / журнал); оболочка сверяется без него.
        for row in rows:
            row["context"] = {
                k: v for k, v in (row["context"] or {}).items() if k != "nutrition_proactive"
            }
        return rows
    if store.startswith("nutrition_proactive:"):
        bot = BotUser.all_tenants.get(pk=bu.pk)
        prefs = (bot.context or {}).get("nutrition_proactive") or {}
        keys = {
            "nutrition_proactive:settings": (
                "daily_report_time",
                "water_reminders",
                "opted_out_at",
            ),
            "nutrition_proactive:observations": ("water", "last_report_date"),
            "nutrition_proactive:journal": ("outbox",),
        }[store]
        # «Ключей нет» читается как None — то же «ключ снят», что у Redis.
        return {k: prefs[k] for k in keys if k in prefs} or None
    if store == "recommendation.Recommendation":
        from apps.recommendation.models import Recommendation

        return list(
            Recommendation.objects.filter(bot_user=bu).values(
                "id", "why", "facts", "goal_id", "fingerprint", "alternatives", "what", "reaction"
            )
        )
    if store == "redis.dre_state":
        return fake_redis.store.get(f"dre:state:{person.conversation.id}")
    if store == "consent.ConsentRecord":
        return list(
            ConsentRecord.all_tenants.filter(bot_user=bu).values(
                "consent_type", "granted", "withdrawn_at", "source"
            )
        )
    if store == "tenancy.TenantStaff":
        return list(TenantStaff.all_tenants.filter(bot_user=bu).values("role", "deactivated_at"))
    if store == "tenancy.StaffInvite":
        return list(
            StaffInvite.all_tenants.filter(used_by=bu).values("note", "used_at", "revoked_at")
        )
    if store in CATALOG_STORES:
        return list(
            AylaErasureJob.objects.filter(ayla_user_id=person.ayla_user_id).values(
                "status", "attempts", "completed_at"
            )
        )
    raise AssertionError(f"нет снимка для {store} — хранилище добавлено в OUTCOMES без чтения")


def _present(state: Any) -> bool:
    return bool(state)


# ── Ожидаемый исход по хранилищу ─────────────────────────────────────────────


def assert_outcome(
    store: str, person: Person, before: Any, after: Any, catalog: _FakeCatalog
) -> None:
    """DELETE → 0 живых; ANONYMISE → строка есть, ПДн пусты; RETAIN → строка не изменилась."""

    expected = OUTCOMES[store].outcome
    assert _present(before) or store in {"conversations.ArchivedMessage", *CATALOG_STORES}, (
        f"{store}: посев не оставил строки — проверять «после» не по чему"
    )

    if expected == RETAIN:
        if store == "conversations.ArchivedMessage":
            # Строк «до» нет по построению: архив рождается в свипе.
            assert len(after) == len(snapshot("conversations.Message", person, _FakeRedis()))
            for row in after:
                assert _PHONE not in row["body"], row["body"]
                assert (
                    "[PHONE]" in row["body"] or "веган" in row["body"] or row["body"] == "Записала."
                )
                assert row["reason"] == ArchivedMessage.Reason.FORGET_ALL
                assert row["retention_until"] > timezone.now() + timedelta(days=80)
            return
        assert _present(after), f"{store}: RETAIN, а строки нет"
        assert after == before, f"{store}: RETAIN, а строка изменилась: {before} → {after}"
        return

    if expected == ANONYMISE:
        assert _present(after), f"{store}: ANONYMISE, а строки нет"
        if store == "identity.UserPersonalContext":
            (row,) = after
            assert row["summary"] is None
            assert row["display_name_preferred"] is None
            assert row["language_preferred"] is None
            assert row["soft_deleted_at"] is not None
            assert row["minor_lock"] is True  # защита остаётся
            return
        if store == "conversations.Message":
            assert len(after) == len(before)
            for row in after:
                assert row["content"] == ""
                assert row["rendered_text"] == ""
                assert row["action_data"] is None
                assert row["tool_call"] is None
            return
        if store == "conversations.AiDraft":
            assert [row["content"] for row in after] == [""]
            return
        if store == "recommendation.Recommendation":
            assert len(after) == len(before)
            for was, row in zip(before, after, strict=True):
                assert row["why"] == []
                assert row["facts"] == {}
                assert row["goal_id"] == ""
                # DRF-2308 — отпечаток нёс ключ цели открытым текстом (ABSENCE)
                # или несолёный хеш цели/причин (DIRECTION): заменён на
                # уникальную метку без смысла.
                assert row["fingerprint"] == f"erased:{row['id']}", row["fingerprint"]
                assert row["fingerprint"] != was["fingerprint"]
                assert "weight" not in row["fingerprint"]
                # Курируемое и атрибуция — остаются.
                assert row["alternatives"] == was["alternatives"]
                assert row["what"] == was["what"]
                assert row["reaction"] == was["reaction"]
            return
        raise AssertionError(f"{store}: ANONYMISE без списка полей ПДн")

    assert expected == DELETE
    if store in CATALOG_STORES:
        assert catalog.deleted_for == [str(person.ayla_user_id)]
        assert catalog.readback_for == [str(person.ayla_user_id)]
        (job,) = after
        assert job["status"] == AylaErasureJob.Status.COMPLETED
        assert job["completed_at"] is not None
        return
    if store.startswith("identity.MemoryEntry:"):
        zone = store.rsplit(":", 1)[1]
        assert after == [], f"{store}: живые строки после «забудь всё»: {after}"
        stones = _tombstoned_entries(person, zone)
        assert len(stones) == len(before)
        for stone in stones:
            assert stone["status"] == MemoryEntry.STATUS_DELETED
            assert stone["deletion_reason"] == MemoryEntry.DELETION_REASON_FORGET_ALL
        return
    if store.startswith("redis.") or store == "nutrition_proactive:observations":
        assert after is None, f"{store}: ключ жив после «забудь всё»: {after}"
        return
    if store == "conversations.Conversation":
        (row,) = after
        assert row["anonymized_through"] is not None
        assert row["skill_state"] == {}, row["skill_state"]
        return
    raise AssertionError(f"{store}: DELETE без правила «0 строк»")


# ── Цепочка ──────────────────────────────────────────────────────────────────


@dataclass
class Swept:
    person: Person
    neighbour: Person
    before: dict[str, dict[str, Any]]
    catalog: _FakeCatalog
    fake_redis: _FakeRedis
    status: GateStatus


def run_forget_all(person: Person, catalog: _FakeCatalog) -> GateStatus:
    """request_forget_all → sweep_forget_all → sweep_pending_forget_all → ayla_erasure."""

    assert request_forget_all(person.ayla_user_id) is True
    assert person.ayla_user_id in pending_forget_all_user_ids()

    first = sweep_forget_all(person.ayla_user_id)
    assert first.changed
    assert first.entries_deleted >= 1
    assert first.conversations_anonymized >= 1

    # Второй проход — подметальщик по расписанию: находит незавершённое или ничего.
    summary = sweep_pending_forget_all()
    assert summary["errors"] == 0
    assert person.ayla_user_id not in pending_forget_all_user_ids()

    return erase_declared_profile_status(person.bot_user, client=catalog)


@pytest.fixture
def swept(settings, fake_redis) -> Callable[..., Swept]:
    settings.STRICT_TENANT_SCOPE = "off"
    settings.AYLA_ERASURE_RETRY_ENABLED = True

    def _run(*, readback: dict | None = None) -> Swept:
        tenant = Tenant.objects.create(slug=f"fam-{uuid.uuid4().hex[:8]}", name="Forget-all matrix")
        person = seed_person(tenant, fake_redis, "person")
        neighbour = seed_person(tenant, fake_redis, "neighbour")
        before = {
            who.label: {store: snapshot(store, who, fake_redis) for store in OUTCOMES}
            for who in (person, neighbour)
        }
        catalog = _FakeCatalog(readback or _erased_status())
        status = run_forget_all(person, catalog)
        return Swept(person, neighbour, before, catalog, fake_redis, status)

    return _run


class TestAfterTheSweepEveryStoreHasItsOutcome:
    @pytest.mark.parametrize("store", _outcome_params())
    def test_store_outcome(self, swept, store):
        run = swept()
        assert run.status is GateStatus.OK, run.status
        after = snapshot(store, run.person, run.fake_redis)
        assert_outcome(store, run.person, run.before["person"][store], after, run.catalog)

    def test_the_neighbour_is_untouched_in_every_store(self, swept):
        run = swept()
        before = run.before["neighbour"]
        assert all(
            _present(before[s])
            for s in OUTCOMES
            if s not in {"conversations.ArchivedMessage", *CATALOG_STORES}
        )
        after = {store: snapshot(store, run.neighbour, run.fake_redis) for store in OUTCOMES}
        changed = {
            store: (before[store], after[store])
            for store in OUTCOMES
            if before[store] != after[store]
        }
        assert changed == {}, changed  # empty-assert-ok: присутствие соседа доказано строкой выше
        assert run.neighbour.ayla_user_id not in run.catalog.deleted_for
        assert f"conv:{run.neighbour.conversation.id}:msgs" not in run.fake_redis.deleted

    def test_a_second_sweep_moves_nothing_and_keeps_the_neighbour(self, swept):
        run = swept()
        again = sweep_forget_all(run.person.ayla_user_id)
        assert again.changed is False
        assert f"conv:{run.neighbour.conversation.id}:msgs" in run.fake_redis.store


class TestTheStreamsUnreachable:
    """DRF-2220 — Redis down at «забудь всё»: the rest runs, the gap is named.

    The ingress streams hold copies that expire by INGRESS_RAW_RETENTION_HOURS,
    so an outage there must neither block the database half nor be reported
    as erased. Checked end to end: the dialogue is anonymised, the raw entry
    is still there, and the sweep's own audit row says the streams were not
    checked.
    """

    def test_the_database_half_runs_and_the_streams_are_named_unchecked(self, swept, monkeypatch):
        import redis

        from apps.audit.models import AuditLog
        from apps.ingress import streams

        def _down():
            raise redis.ConnectionError("ingress redis is down")

        monkeypatch.setattr(streams, "_client", _down)

        run = swept()

        assert run.status is GateStatus.OK, run.status
        after = snapshot("conversations.Message", run.person, run.fake_redis)
        assert_outcome(
            "conversations.Message",
            run.person,
            run.before["person"]["conversations.Message"],
            after,
            run.catalog,
        )
        assert snapshot("redis.ingress_stream", run.person, run.fake_redis) is not None
        rows = [
            a.payload
            for a in AuditLog.all_tenants.filter(action="memory.forget_all_swept")
            if a.payload.get("user_id") == str(run.person.ayla_user_id)
        ]
        assert rows, "the sweep wrote no audit row"
        assert all(r["raw_streams_checked"] is False for r in rows), rows


class TestTheCatalogReadbackIsTheLastWord:
    """DRF-1984: пока readback не вернул ``erased`` — не «удалено», а «запущено»."""

    def test_holds_values_readback_keeps_the_job_open(self, swept):
        run = swept(readback=_holds_values_status())
        assert run.status is GateStatus.STARTED
        (job,) = AylaErasureJob.objects.filter(ayla_user_id=run.person.ayla_user_id).values(
            "status", "attempts", "next_attempt_at"
        )
        assert job["status"] == AylaErasureJob.Status.PENDING
        assert job["attempts"] == 1
        assert job["next_attempt_at"] is not None
        # Бот-половина при этом ДОДЕЛАНА: readback каталога — последняя строка, не первая.
        seeded = run.before["person"]["identity.MemoryEntry:green"]
        assert len(seeded) == 1
        live = snapshot("identity.MemoryEntry:green", run.person, run.fake_redis)
        assert live == []  # empty-assert-ok: посев доказан строкой выше (seeded)
