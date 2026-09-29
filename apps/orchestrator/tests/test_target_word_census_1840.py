"""«Ориентир», не «норма» — слово §82 во всех репликах бота и Mini App (DRF-1840).

Перепись пользовательских строк по всему дереву: бот — по AST (куски
f-строк целиком, докстринги вне), Mini App — литералы и текст JSX. Судится
ПО ПРЕДЛОЖЕНИЯМ, не по строке: «записывать еду и воду … нормы» — не про
воду. Вода остаётся «норма» — решение владельца 11.09 §5.2 («норма воды —
мера шага», «до нормы, не до цели»).

* w1 — стража-перепись: «норм» вне воды встречается только в allow-list —
  фразах владельца (пакет 12.09 §2, DRF-1698 «APPROVE WITH TEXT CHANGES»;
  кнопка и фраза входа «Рассчитать мои нормы») и описании намерения для
  роутера (то, что говорят люди). Каждая запись allow-list обязана
  найтись — устаревшая запись красная; водные предложения обязаны
  остаться (положительная стража);
* w2 — личная поверхность: «нет профиля» и недельные строки о белке;
* w3 — блок питания в промпте консьержа: недельная строка о белке;
* w4 — карточка анкеты, отказ по health-фактору, реплика об override;
* w5 — отказ меню маркетплейса после «не сейчас»: зовёт к согласию
  дневника (DRF-2100 — оно одно), не закрывает и не переспрашивает;
* w6 — примеры голоса для модели: «ориентир», не «норма» — иначе модель
  учится слову, которое владелец снял.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.orchestrator import nutrition_context, personal_surface
from apps.skills.menu import marketplace
from apps.skills.nutrition_anketa import skill as anketa_skill

REPO = Path(__file__).resolve().parents[3]
APPS = REPO / "apps"

_NORM = re.compile(r"норм", re.I)
#: «нормально», «нормализ», «нормативн» — не про ориентир.
_NOT_TARGET = re.compile(r"нормальн|нормализ|норматив", re.I)
#: «water» — идентификаторы Mini App рядом с текстом (``ruPluralWater``).
_WATER = re.compile(r"вод|стакан|\bмл\b|жидк|water", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_TS_LITERAL = re.compile(r"""(["'`])((?:\\.|(?!\1).)*?)\1""", re.S)
_JSX_TEXT = re.compile(r">([^<>]*?)<", re.S)
_TS_COMMENT = re.compile(r"/\*.*?\*/|^\s*//[^\n]*|(?<=\s)//[^\n]*", re.S | re.M)

#: Фразы владельца и одна строка для роутера: (файл, начало предложения).
#: Смена этих слов — только словом владельца (текст согласия — ещё и
#: вопрос версии документа personal-calculation-v1).
ALLOWED: tuple[tuple[str, str], ...] = (
    (
        "skills/nutrition_anketa/skill.py",
        "Хотите, чтобы Ayla рассчитывала ваши персональные нормы?",
    ),
    ("skills/nutrition_anketa/skill.py", "Я сохраню эти данные в вашем профиле"),
    ("skills/nutrition_anketa/skill.py", "Рассчитать мои нормы"),
    ("skills/nutrition_anketa/skill.py", "рассчитать мои нормы"),
    ("skills/nutrition_anketa/skill.py", "Если передумаете, напишите: «Рассчитать мои нормы»."),
    ("skills/nutrition_anketa/skill.py", "Ayla перестанет использовать ваши вес, рост, возраст"),
    ("skills/nutrition_anketa/skill.py", "Параметры удалены, нормы больше не показываются."),
    (
        "skills/nutrition_anketa/skill.py",
        "Если захотите вернуть расчёт, напишите: «Рассчитать мои нормы».",
    ),
    (
        "skills/nutrition_anketa/skill.py",
        "Персональный расчёт отключён: параметры больше не используются",
    ),
    (
        "orchestrator/nutrition_global.py",
        "Пользователь хочет заполнить или продолжить анкету питания",
    ),
)


def _py_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    inside: set[int] = set()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = [v for v in node.values if isinstance(v, ast.Constant)]
            inside.update(id(v) for v in parts)
            out.append((node.lineno, "".join(str(v.value) for v in parts)))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in inside
            and id(node) not in docstrings
        ):
            out.append((node.lineno, node.value))
    return out


def _ts_strings(path: Path) -> list[tuple[int, str]]:
    # Комментарии — не реплики; вырезаются с сохранением номеров строк.
    text = _TS_COMMENT.sub(
        lambda m: "\n" * m.group(0).count("\n"), path.read_text(encoding="utf-8")
    )
    out: list[tuple[int, str]] = []
    for pattern in (_TS_LITERAL, _JSX_TEXT):
        for m in pattern.finditer(text):
            s = m.group(m.lastindex or 1)
            if s and s.strip():
                out.append((text.count("\n", 0, m.start()) + 1, s))
    return out


def _skip(rel: str) -> bool:
    return any(
        seg in rel
        for seg in ("/tests/", "/test_", ".test.", "/migrations/", "node_modules", "/dist/")
    )


def _census() -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """(нарушения, водные предложения) по всему дереву."""
    target: list[tuple[str, int, str]] = []
    water: list[tuple[str, int, str]] = []
    for path in sorted(APPS.rglob("*")):
        rel = path.relative_to(APPS).as_posix()
        if _skip(rel):
            continue
        if path.suffix == ".py":
            strings = _py_strings(path)
        elif path.suffix in {".ts", ".tsx"}:
            strings = _ts_strings(path)
        else:
            continue
        for lineno, s in strings:
            for sentence in _SENTENCE.split(s):
                sentence = sentence.strip()
                if not _NORM.search(sentence) or _NOT_TARGET.search(sentence):
                    continue
                (water if _WATER.search(sentence) else target).append((rel, lineno, sentence))
    return target, water


class TestW1TheWordIsGoneEverywhereButWater:
    def test_only_the_owners_phrases_still_say_norma(self) -> None:
        target, water = _census()
        allowed_seen: set[tuple[str, str]] = set()
        offenders: list[str] = []
        for rel, lineno, sentence in target:
            match = next((a for a in ALLOWED if a[0] == rel and sentence.startswith(a[1])), None)
            if match is None:
                offenders.append(f"{rel}:{lineno}: {sentence!r}")
            else:
                allowed_seen.add(match)
        assert not offenders, "«норма» вне воды и вне фраз владельца:\n" + "\n".join(offenders)
        # Каждая allow-запись найдена: запись про снятую строку — сама дефект.
        assert allowed_seen, "фразы владельца не найдены вовсе — перепись слепа"
        stale = sorted(set(ALLOWED) - allowed_seen)
        assert not stale, f"allow-list устарел: {stale}"
        # POSITIVE: водные предложения на месте — бот и Mini App.
        assert any(rel == "nutrition_proactive/render.py" for rel, _, _ in water)
        assert any(
            rel.endswith("CustomerWellnessDashboardScreen.tsx") and "до нормы" in s
            for rel, _, s in water
        )

    def test_the_census_reads_fstrings_whole_and_skips_docstrings(self, tmp_path: Path) -> None:
        """Ложный вход инструменту: f-строка про воду цела, докстринг не считается."""
        probe = tmp_path / "probe.py"
        probe.write_text(
            '"""Докстринг про нормы."""\n'
            'X = f"До дневной нормы ещё {n} мл."\n'
            'Y = "Белок: от нормы."\n',
            encoding="utf-8",
        )
        strings = [s for _, s in _py_strings(probe)]
        assert "До дневной нормы ещё  мл." in strings
        assert "Белок: от нормы." in strings
        assert not any("Докстринг" in s for s in strings)


class TestW2ThePersonalSurfaceSaysTarget:
    def test_no_profile_text(self) -> None:
        assert (
            personal_surface.NO_PROFILE_TEXT
            == "Ориентиров пока нет — я ещё не считала их для тебя."
        )

    def test_the_week_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        deficits = SimpleNamespace(
            days_observed=5, protein_avg_pct_goal=72.0, protein_low_streak_days=3
        )
        monkeypatch.setattr(personal_surface, "_fetch", lambda bot_user, name: deficits)
        monkeypatch.setattr(personal_surface, "_diary_chips", lambda profile: [])
        text = personal_surface._render_week(object(), profile=None).text
        assert "Белок: в среднем 72% от твоего ориентира." in text
        assert "Дней подряд ниже ориентира по белку: 3." in text
        assert "норм" not in text.lower()


class TestW3ThePromptBlockSaysTarget:
    def test_the_week_line(self) -> None:
        lines = nutrition_context._render_lines(
            SimpleNamespace(days_observed=5, protein_avg_pct_goal=62.4, protein_low_streak_days=4)
        )
        assert "Белок: в среднем 62% от ориентира." in lines
        assert not any("норм" in line.lower() for line in lines)


class TestW4TheAnketaCardSaysTarget:
    def test_summary_head_and_override_remark(self) -> None:
        from apps.skills.nutrition_anketa.tests.test_skill import _profile

        text = anketa_skill._format_summary(_profile(goal_overridden_by="bmi_floor"))
        assert text.startswith("Готово, посчитала твои ориентиры:")
        assert "Учла важное в анамнезе — ориентиры подобрала с поправкой на это." in text
        assert "норм" not in text.lower()

    def test_health_factor_refusal(self) -> None:
        text = anketa_skill._format_health_factor_refusal(["minor"])
        assert text.startswith(
            "Ориентиры не считаю: при возрасте до 18 лет Ayla их не рассчитывает."
        )
        assert "Дневник и вода работают как раньше" in text
        assert "норм" not in text.lower()


class TestW5TheMenuRefusalInvitesTheDiaryConsent:
    def test_text_names_the_diary_consent_and_does_not_close(self) -> None:
        text = marketplace.HEALTH_DECLINED_EARLIER_TEXT
        assert "согласие на дневник питания" in text
        assert "в профиле" in text
        low = text.lower()
        for closing in ("не работает", "не пущу", "здоровь"):
            assert closing not in low, closing
        # Не второй вопрос (канон 2.5/2.6): без вопросительного знака и кнопок согласия здесь.
        assert "?" not in text


class TestW6TheVoiceExamplesTeachTheRightWord:
    def test_no_norma_outside_water(self) -> None:
        from apps.promptreg import voice_examples

        offenders: list[str] = []
        for name in dir(voice_examples):
            value = getattr(voice_examples, name)
            if not isinstance(value, list):
                continue
            for example in value:
                for text in (getattr(example, "user", ""), getattr(example, "assistant", "")):
                    for sentence in _SENTENCE.split(str(text)):
                        if (
                            _NORM.search(sentence)
                            and not _WATER.search(sentence)
                            and not _NOT_TARGET.search(sentence)
                        ):
                            offenders.append(f"{name}: {sentence.strip()!r}")
        assert offenders == [], offenders
        assert any(
            "ориентир" in getattr(e, "assistant", "")
            for e in voice_examples.ANKETA_EMPATHY_EXAMPLES
        )
