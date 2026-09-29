"""DRF-2608 — фразы бренда: сторона разговора и спящее состояние по записи.

1. Пара, которая обязана различаться: запрещённая фраза бренда В ОТВЕТЕ Ayla
   блокируется (``post_check``); та же фраза ВО ВХОДЕ человека проходит
   (``pre_check``). До DRF-2608 вход блокировался той же фразой.
2. Спящее состояние — ЗАПИСАНО: боевой конвейер (``apps/orchestrator``) не
   читает ``BrandVoiceConfig`` салона — ``load_brand_voice`` не вызывается, в
   роутер уходит ``brand_voice=None``, ``post_check`` зовётся без него. На
   29.09 в боевой базе ``persona_brandvoiceconfig`` — 0 строк. Включение
   (читать голос салона и проверять им ответ) — решение владельца: у проверки
   на каждом ответе есть цена во времени. Этот узел сломается, когда
   включение появится, и потребует снять запись — проверка не уснёт тихо.
"""

from __future__ import annotations

import re
from pathlib import Path

from apps.orchestrator.safety.post_check import PostCheckVerdict, post_check
from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check

VOICE = {"forbidden_phrases": [r"(?i)гарантирую результат"]}
PHRASE = "Гарантирую результат после первого сеанса"

ORCHESTRATOR = Path(__file__).resolve().parents[2]

# Чтение голоса салона — вызов, импорт загрузчика или прямой запрос модели.
# Упоминание в докстринге («dict from :func:`…load_brand_voice`») — не чтение.
READS_BRAND_VOICE = re.compile(
    r"load_brand_voice\s*\(|import[^\n]*\bload_brand_voice\b|BrandVoiceConfig\.objects"
)


def test_brand_phrase_in_ayla_reply_is_blocked() -> None:
    result = post_check(PHRASE, brand_voice=VOICE)
    assert result.verdict == PostCheckVerdict.BLOCK
    assert any("гарантирую" in p for p in result.matched_patterns)


def test_same_brand_phrase_in_the_persons_input_passes() -> None:
    result = pre_check(PHRASE, brand_voice=VOICE)
    assert result.verdict == SafetyVerdict.ALLOW
    assert not any("гарантирую" in p for p in result.matched_patterns)


def _production_sources() -> list[Path]:
    return [p for p in ORCHESTRATOR.rglob("*.py") if "tests" not in p.parts]


def test_orchestrator_is_scanned() -> None:
    # Положительный контроль: без него «вызова нет» значило бы «ничего не прочитали».
    names = {p.name for p in _production_sources()}
    assert {"pipeline.py", "composer.py", "intent_router.py"} <= names


def test_the_reader_pattern_sees_a_call_an_import_and_a_query() -> None:
    # Положительный контроль узкого признака: иначе «читателей нет» могло бы
    # значить «признак ослеп».
    assert READS_BRAND_VOICE.search("voice = load_brand_voice(tenant)")
    assert READS_BRAND_VOICE.search("from apps.voice.services import load_brand_voice")
    assert READS_BRAND_VOICE.search("BrandVoiceConfig.objects.get(tenant=t)")
    assert not READS_BRAND_VOICE.search("dict from :func:`apps.voice.services.load_brand_voice`.")


def test_brand_voice_config_is_not_read_on_the_live_path_by_record() -> None:
    """Запись DRF-2608: голос бренда салона в бою не читается. Включение —
    слово владельца; вместе с ним этот узел снимается (не «чинится»)."""
    sources = _production_sources()
    assert "pipeline.py" in {p.name for p in sources}
    readers = [
        str(p.relative_to(ORCHESTRATOR))
        for p in sources
        if READS_BRAND_VOICE.search(p.read_text(encoding="utf-8"))
    ]
    assert readers == [], f"голос бренда читается в бою — снять запись DRF-2608: {readers}"
