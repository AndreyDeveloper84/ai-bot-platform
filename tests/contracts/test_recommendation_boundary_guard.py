"""Гард границы резолвера, половина бота — T4 (DRF-1565), контракт §2.1 C1.

Зеркало гарда, стоящего в `beautygo_backend`
(`recommendation/tests/test_boundary_guards.py`). Стоят они парой не для
симметрии: авторитетов было три, и **один из них живёт здесь**. Гард,
поставленный только на стороне Ayla, оставил бы бота свободно писать свою
формулу — то есть ровно то состояние, из которого мы выбираемся.

Ценность гарда не в вычистке старого (это делает T12), а в том, что
**новое** место ранжирования краснит билд уже сегодня, пока не мигрировал
ни один потребитель. Без этого вариант B из OD §53 выродился бы
в «оставили как было».
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_ORDERING_CALLS = re.compile(r"(order_by\(|\.sort\(|sorted\()")

#: Признак, по которому упорядочивать нельзя: качество, близость, рейтинг.
#: Сортировка по имени, дате или порядку в каталоге разрешена и всегда была
#: разрешена — запрещено ставить выше «лучшего».
_QUALITY_SIGNAL = re.compile(
    r"\b(rating|reviews_count|review_count|score|match_score|distance|haversine|proximity)\b"
)

_ARGS_WINDOW = 160

_SKIPPED_PARTS = ("migrations", "tests", ".venv", "venv", "__pycache__", "node_modules", ".git", "scripts")

#: Известные места. У каждого либо задача, которая его снимет, либо
#: объяснение, почему это не кандидаты рекомендации.
_ALLOWED = {
    # Авторитет A. Настоящая точка решения бота; становится потребителем в T12.
    "apps/marketplace/discovery.py": "DRF-1573 (T12): бот зовёт resolve() вместо своего порядка",
    # Меню услуг после тапа по мастеру и подбор услуг внутри мастера — тот же
    # авторитет, другая поверхность.
    "apps/orchestrator/handoff.py": "DRF-1573 (T12): та же миграция, вторая поверхность бота",
    # Салонный бот поверх YClients: порядок по релевантности запросу, без
    # качества и без близости. Данные чужой системы, доменной правды Ayla
    # под ними нет. Остаётся ничья по алфавиту — семья C-01/DRF-1529.
    "apps/skills/booking/tools.py": "не кандидаты рекомендации Ayla: релевантность поверх YClients (ничьи — DRF-1529)",
    # Кому передать записи деактивируемого мастера. Операционный инструмент
    # владелицы салона, а не выдача человеку.
    "apps/admin_api/services/master_deactivation.py": "не кандидаты рекомендации: переназначение записей внутри салона",
    # Релевантность документов базы знаний. Не провайдеры и не услуги.
    "apps/kb/services/retriever.py": "не кандидаты рекомендации: релевантность документов",
    "apps/skills/faq/skill.py": "не кандидаты рекомендации: релевантность документов",
}


def _python_files():
    for path in (REPO_ROOT / "apps").rglob("*.py"):
        if any(part in _SKIPPED_PARTS for part in path.parts):
            continue
        yield path.relative_to(REPO_ROOT).as_posix(), path


def _ranking_sites(text: str) -> list[tuple[int, str]]:
    found = []
    for call in _ORDERING_CALLS.finditer(text):
        window = text[call.start(): call.start() + _ARGS_WINDOW]
        signal = _QUALITY_SIGNAL.search(window)
        if signal:
            found.append((text[: call.start()].count("\n") + 1, f"{call.group(1)}… {signal.group(0)}"))
    return found


def test_no_new_ranking_surfaces():
    """Новое место ранжирования — красный билд. Сегодня, а не после миграций."""
    offenders = []
    for rel, path in _python_files():
        if rel in _ALLOWED:
            continue
        offenders.extend(
            f"{rel}:{line} — {fragment}"
            for line, fragment in _ranking_sites(path.read_text(encoding="utf-8", errors="ignore"))
        )
    assert not offenders, (
        "упорядочивание кандидатов вне резолвера (контракт §2.1 C1, OD §53):\n  "
        + "\n  ".join(offenders)
        + "\n\nПоверхность обязана звать границу резолвера "
        "(apps.integrations.ayla.recommendation_resolver_client), а не считать свой порядок. "
        "Если это НЕ кандидаты рекомендации — внесите файл в _ALLOWED с объяснением."
    )


def test_every_allowance_is_accounted_for():
    """Исключение без задачи или без объяснения — это «временно» навсегда."""
    for rel, reason in _ALLOWED.items():
        assert reason.startswith("DRF-") or reason.startswith("не кандидаты рекомендации"), (
            f"{rel}: исключение обязано называть задачу, которая его снимет, "
            f"либо объяснять, почему оно постоянное. Получено: {reason!r}"
        )


def test_allowances_are_not_stale():
    """Исключение, которому нечего прикрывать, обязано уйти из списка."""
    stale = []
    for rel in _ALLOWED:
        path = REPO_ROOT / rel
        assert path.exists(), f"исключение {rel} ссылается на несуществующий файл"
        if not _ranking_sites(path.read_text(encoding="utf-8", errors="ignore")):
            stale.append(rel)
    assert not stale, "исключения больше ничего не прикрывают — удалите: " + ", ".join(stale)


def test_transit_no_longer_claims_it_does_not_validate():
    """§2.1 C3: роль «translation hop, not a schema gate» отменена.

    Проверяется буквально по тексту: формулировка, которой обе стороны
    письменно отказались владеть формой ответа, не должна вернуться в код
    как правило. Она осталась в докстринге старого клиента — но как
    цитата с пометкой «ОТМЕНЕНА», и этот тест ловит попытку восстановить
    её как утверждение.
    """
    text = (REPO_ROOT / "apps/integrations/ayla/recommendations_client.py").read_text(encoding="utf-8")
    assert "ОТМЕНЕНА" in text, "клиент обязан помнить, что роль транзита без схемы отменена (§2.1 C3)"

    views = (REPO_ROOT / "apps/miniapp_api/views.py").read_text(encoding="utf-8")
    assert "ОТМЕНЕНА" in views, "прокси обязан помнить, что «Mini App owns the rendering contract» отменено"
