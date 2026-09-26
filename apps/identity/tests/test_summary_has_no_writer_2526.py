"""`UserPersonalContext.summary` не пишет никто — и подсказка не выдаёт пустоту за выжимку (DRF-2526).

`help_text` поля обещает «Ayla's running summary of who this user is», а
писателя в боевом коде нет ни одного: единственная запись — обнуление в
`forget_all_sweep`. Выполнять обещание сегодня нельзя — проза «кто этот
человек» есть вывод по определению (так её и записал `POLICY_DEBT` в
`personal_fields.py`), а вывод в постоянную память по AYLA-DEC-0024 идёт
только через `MemoryProposal`, которого в коде нет. Поэтому обещание снято, а
здесь — два прибора на то, что осталось правдой:

* **перепись писателей** по всему боевому коду: появится запись в поле —
  узел назовёт её место, и решение по AYLA-DEC-0024 придётся принять, а не
  обнаружить;
* **пустота не доезжает до подсказки** — по той функции, которую зовёт
  обработчик MAX (`handler.py` → `render_current_personal_context`).

Слепые пятна переписи, названные заранее: `setattr(upc, "summary", …)`,
`Model.objects.create(**data)` и сырой SQL. Их сегодня нет ни одного — но и
видеть их этот узел не умеет.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: Вызовы с ключом `summary=`, которые к полю отношения не имеют. Новый вызов
#: вне списка — узел краснеет и просит отнести его сюда или признать писателем.
_NOT_THE_FIELD = {
    ("apps/admin_api/services/assistant.py", "AdminProposal"),
    ("apps/admin_api/services/salon_day.py", "SalonDay"),
    ("apps/identity/services/memory_key_policy.py", "PersonalContextView"),
    ("apps/identity/services/memory_reader.py", "PersonalContextView"),
    ("apps/master_api/services/assistant_actions.py", "ProposedAction"),
}

#: Единственная законная запись в поле — обнуление при «забудь всё».
_ERASURE = (
    "apps/identity/services/forget_all_sweep.py",
    "UserPersonalContext.objects.filter(user_id=user_id).update",
)


def _census() -> tuple[int, list[str], list[tuple[str, str, ast.expr]]]:
    scanned = 0
    attr_writes: list[str] = []
    kw_writes: list[tuple[str, str, ast.expr]] = []
    for path in (REPO / "apps").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "summary":
                        attr_writes.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "summary":
                        kw_writes.append((rel, ast.unparse(node.func), keyword.value))
    return scanned, attr_writes, kw_writes


def test_nobody_writes_the_summary():
    scanned, attr_writes, kw_writes = _census()

    assert scanned > 300, f"просмотрено {scanned} файлов — перепись не туда смотрит"
    assert attr_writes == [], (
        f"просмотрено {scanned} файлов; присваивание `.summary = …`: {attr_writes}. "
        "Если это UserPersonalContext — это писатель вывода, AYLA-DEC-0024"
    )

    erasure = [(rel, func, value) for rel, func, value in kw_writes if (rel, func) == _ERASURE]
    assert len(erasure) == 1, f"обнуление в forget_all_sweep не найдено: {kw_writes}"
    value = erasure[0][2]
    assert isinstance(value, ast.Constant) and value.value is None, ast.unparse(value)

    unknown = sorted(
        f"{rel}: {func}(summary=…)"
        for rel, func, _ in kw_writes
        if (rel, func) != _ERASURE and (rel, func.rsplit(".", 1)[-1]) not in _NOT_THE_FIELD
    )
    assert unknown == [], f"просмотрено {scanned} файлов; новые вызовы с summary=: {unknown}"


@pytest.mark.django_db
@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
def test_a_blank_summary_never_reaches_the_prompt(blank):
    """Пустое поле не даёт ни абзаца, ни пустой строки внутри абзаца.

    Сравнение — с тем же человеком без строки контекста вовсе: пустота в
    `summary` обязана быть неотличима от её отсутствия. Факт рядом нужен,
    чтобы абзац вообще был: без него «None» доказывал бы только пустоту всего.
    """
    from apps.identity.models import MemoryEntry, UserPersonalContext
    from apps.identity.services.memory_writer import write_entry
    from apps.persona.memory_surface import render_current_personal_context

    def _person(summary):  # noqa: ANN001, ANN202
        user_id = uuid.uuid4()
        upc = UserPersonalContext.objects.create(user_id=user_id, summary=summary)
        return user_id, upc

    alone_id, _ = _person(blank)
    assert render_current_personal_context(alone_id) is None

    with_fact_id, upc = _person(blank)
    reference_id, reference_upc = _person(None)
    for uid, parent in ((with_fact_id, upc), (reference_id, reference_upc)):
        write_entry(
            user_id=uid,
            personal_context=parent,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"key": "diet", "value": "vegan"},
            request_id=uuid.uuid4(),
            purpose="test:drf2526",
        )

    paragraph = render_current_personal_context(with_fact_id)
    assert paragraph is not None
    assert paragraph == render_current_personal_context(reference_id), repr(paragraph)

    # Контроль: непустое поле абзац МЕНЯЕТ. Без него равенство выше прошло бы
    # и у сборки, которая summary не читает вовсе.
    upc.summary = "любит тишину"
    upc.save(update_fields=["summary"])
    assert "любит тишину" in (render_current_personal_context(with_fact_id) or "")


@pytest.mark.django_db
@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
def test_the_second_reader_sees_blank_as_absent(blank):
    """Второй читатель поля — `read_personal_context`.

    В подсказку он не ведёт (его зовут дедуп записи и спящий `memory_inferred`),
    но строка чтения у него та же, что у `read_current_view`, и своего узла на
    пустоту по продуктовой записи не было.
    """
    from apps.identity.models import UserPersonalContext
    from apps.identity.services.memory_reader import read_personal_context

    user_id = uuid.uuid4()
    UserPersonalContext.objects.create(user_id=user_id, summary=blank)

    view = read_personal_context(user_id)
    assert view.summary is None, repr(view.summary)
    assert view.is_empty()
