"""Клиент границы резолвера — `POST /api/v1/internal/recommendation/resolve/`.

Вторая половина границы контракта §9.4. Первая стоит на стороне Ayla
(`recommendation/views.py`), и обе проверяют схему: договор, который проверяет
только один конец, — это не договор, а надежда.

Чем этот клиент отличается от `recommendations_client`
--------------------------------------------------------
Тот — транзит: докстринг прямо объявлял «this layer is a translation hop,
**not a schema gate**» и «pass-through; no shape enforcement here».
Настоящим контрактом (§2.1 C3) эта роль **отменена**. Здесь схема
проверяется, а результат разложен на три различимых исхода.

Три исхода, и различие между ними структурное
----------------------------------------------
``OK`` / ``UNAVAILABLE`` / ``CONTRACT_VIOLATION``. Последние два разводятся
не эвристикой, а устройством кода: ``try`` накрывает **ровно транспорт**,
и проверка формы стоит после него, поэтому по определению видит только
доставленные ответы. Ложного ``CONTRACT_VIOLATION`` при обычном отказе не
может быть **не потому, что мы стараемся**, а потому что состояние туда
не доходит. Тот же приём, что в `apps/miniapp/src/lib/customer-booking.ts`
(DRF-1556): у двух состояний противоположная цена молчания, и делить одну
ветку им нельзя.

* ``UNAVAILABLE`` — сеть, таймаут, 5xx, открытый предохранитель, 503 от
  ненастроенного источника. Молчание тут законно: подбор — необязательное
  украшение, а детектор, кричащий на каждый мёртвый скорер, глушат за неделю
  и он молчит ровно тогда, когда нужен.
* ``CONTRACT_VIOLATION`` — источник **ответил**, но не в объявленной форме,
  либо прислал неизвестную мажорную версию. Это «мы и они разошлись в том,
  о чём договорились», и оно обязано быть громким.

Конформность — целиком (§9.4.1, OD §53.1)
------------------------------------------
Один битый элемент из двадцати делает невалидным **ответ**, а не элемент.
«Пропустить годные» запрещено прямо: это означало бы, что потребитель решает,
какие из присланных рекомендаций увидит человек, — то есть ведёт отбор,
то есть держит политику. Так возвращается четвёртый авторитет, самый
незаметный: он живёт в фильтре и никогда не назовёт себя ранжированием.
Текст сообщения при этом перечисляет нарушения поимённо — это диагностика,
чтобы чинили **источник**, а не экран.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.recommendations_client import (
    RecommendationsConfigError,
    _circuit,
)
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)

TIMEOUT_S: Final[float] = 5.0

#: Мажорная версия контракта, которую этот клиент умеет разбирать.
#: Ответ другой мажорной версии — `CONTRACT_VIOLATION`, а не «попробуем
#: разобрать»: попытка разобрать неизвестное и есть тот способ, которым
#: расхождение доезжает до человека молча.
SUPPORTED_SPEC_MAJOR: Final[int] = 1

_PATH: Final[str] = "internal/recommendation/resolve/"


@dataclass(frozen=True)
class ResolveOutcome:
    """Исход вызова. Ровно один из трёх, и они не сливаются."""

    state: str  # "ok" | "unavailable" | "contract_violation"
    decision: dict | None = None
    detail: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.state == "ok"


def resolve_recommendation(*, external_user_id: str, payload: dict[str, Any]) -> ResolveOutcome:
    """Позвать границу и классифицировать ответ.

    Исключений не бросает: у вызывающего три ветки, и каждая — нормальное
    состояние продукта, а не авария потока управления.
    """
    try:
        url = _build_url()
    except RecommendationsConfigError as exc:
        # Конфигурация — не отказ Ayla, но для вызывающего это та же
        # недоступность: подбора нет, врать нельзя, шуметь незачем.
        logger.error("resolver_client.config_error: %s", exc)
        return ResolveOutcome("unavailable", detail=f"config: {exc}")

    now = time.monotonic()
    if _circuit.is_open(now=now):
        return ResolveOutcome("unavailable", detail="circuit_open")

    # ВНУТРИ try — только транспорт. Ни разбора, ни проверки формы: всё,
    # что здесь падает, по построению означает «источник не ответил».
    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            response = http.post(url, headers=_headers(external_user_id), json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning("resolver_client.network_failure exc=%s", type(exc).__name__)
        return ResolveOutcome("unavailable", detail=f"network: {type(exc).__name__}")

    if response.status_code >= 500 or response.status_code == 503:
        _circuit.record_failure(now=time.monotonic())
        return ResolveOutcome("unavailable", detail=f"server: HTTP {response.status_code}")
    if response.status_code != 200:
        # 4xx: мы отправили не то. Предохранитель не трогаем — источник жив.
        logger.warning("resolver_client.client_error status=%d", response.status_code)
        return ResolveOutcome(
            "contract_violation", detail=f"HTTP {response.status_code} на запрос границы"
        )

    try:
        body = response.json()
    except ValueError as exc:
        _circuit.record_failure(now=time.monotonic())
        return ResolveOutcome("unavailable", detail=f"malformed_json: {exc}")

    _circuit.record_success()

    violation = decision_contract_violation(body)
    if violation is not None:
        # Громко и с указанием на источник: чинить надо ЕГО, а не экран.
        logger.error(
            "resolver_client.contract_violation — источник ответил, но не в форме, "
            "которую объявляет граница (docs/OPEN_DECISIONS.md §52–§53, контракт §9.4): %s",
            violation,
        )
        return ResolveOutcome("contract_violation", detail=violation)

    return ResolveOutcome("ok", decision=body["data"])


def decision_contract_violation(payload: Any) -> str | None:
    """Вернуть описание нарушения формы или ``None``, если ответ конформен.

    Проверка **не булева наполовину**: она либо признаёт весь ответ, либо
    отвергает весь ответ. Первое найденное нарушение и есть ответ функции —
    перечислять все незачем, чинить источник придётся с первого.
    """
    if not isinstance(payload, dict):
        return f"ожидался объект, получено {_shape(payload)}"

    data = payload.get("data")
    if not isinstance(data, dict):
        return f"ожидался конверт {{data: {{...}}}}, получено data={_shape(data)}"

    version = data.get("resolver_spec_version")
    if not isinstance(version, str):
        return f"resolver_spec_version отсутствует или не строка: {_shape(version)}"
    major = version.split(".", 1)[0]
    if not major.isdigit() or int(major) != SUPPORTED_SPEC_MAJOR:
        return (
            f"неизвестная мажорная версия контракта {version!r}; клиент умеет "
            f"{SUPPORTED_SPEC_MAJOR}.x. Разбирать неизвестное запрещено (§9.4)"
        )

    ordered = data.get("ordered")
    if not isinstance(ordered, list):
        return f"ordered отсутствует или не список: {_shape(ordered)}"

    for index, item in enumerate(ordered):
        problem = _candidate_violation(item, index)
        if problem is not None:
            # §53.1: невалиден ОТВЕТ, а не элемент. Указание на элемент —
            # диагностика: она говорит источнику, где именно он нарушил.
            return f"ответ невалиден целиком; первое нарушение — {problem}"

    for field in ("decision_id", "request_id", "policy_versions"):
        if field not in data:
            return f"обязательное поле {field} отсутствует"

    if _has_display_string(data):
        return (
            "ответ несёт строку для показа человеку — граница отдаёт reason_codes "
            "и evidence, фразу собирает представление (§7)"
        )
    return None


def _candidate_violation(item: Any, index: int) -> str | None:
    if not isinstance(item, dict):
        return f"ordered[{index}]: ожидался объект, получено {_shape(item)}"
    candidate = item.get("candidate")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
        return f"ordered[{index}].candidate: нет идентификатора кандидата"
    for field in ("rank", "tier"):
        if not isinstance(item.get(field), int) or isinstance(item.get(field), bool):
            return f"ordered[{index}].{field}: ожидалось целое, получено {_shape(item.get(field))}"
    codes = item.get("reason_codes")
    if not isinstance(codes, list) or not codes or not all(isinstance(c, str) for c in codes):
        # Кандидат без кодов — это строка, про которую нельзя сказать,
        # почему она здесь. Гейт WHY владельца (25.08) не пропустил бы её
        # дальше, но здесь она уже нарушение формы, а не «нечего показать».
        return f"ordered[{index}].reason_codes: ожидался непустой список строк, получено {_shape(codes)}"
    if not isinstance(item.get("evidence", []), list):
        return (
            f"ordered[{index}].evidence: ожидался список, получено {_shape(item.get('evidence'))}"
        )
    return None


#: Поля, наличие которых означает, что источник снова собрал фразу за
#: потребителя. Проверяется рекурсивно: «Рейтинг 4.9» пришёл человеку
#: именно такой строкой.
_DISPLAY_FIELDS = frozenset({"reasoning_text", "reason_text", "why_text"})


def _has_display_string(node: Any) -> bool:
    if isinstance(node, dict):
        if _DISPLAY_FIELDS & set(node):
            return True
        return any(_has_display_string(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_display_string(value) for value in node)
    return False


def _build_url() -> str:
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        raise RecommendationsConfigError("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")
    try:
        return AylaUrlBuilder(base_url).build(_PATH)
    except AylaUrlError as exc:
        raise RecommendationsConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc


def _headers(external_user_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.AYLA_INTERNAL_API_TOKEN}",
        "X-External-User-ID": external_user_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (str, int, float, bool)):
        return f"{type(value).__name__}({value!r})"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object{sorted(value)[:5]}"
    return type(value).__name__
