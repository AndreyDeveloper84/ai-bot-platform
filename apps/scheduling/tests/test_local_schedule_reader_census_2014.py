"""Перепись читателей локального расписания: кабинет читает рамку, а не копию (DRF-2014).

`apps/scheduling` — локальная копия расписания. При `BOOKING_VIA_AYLA_REST=true`
клиенту часы продаёт каталог, а копию боту никто не обновляет: замер главного
окна (`ruvds-o1mqo`, `ayla-bot-staging-postgres-1`, 15.09.2026 ~23:20 UTC,
READ ONLY) — 28 строк `workinghours` у 4 мастеров от 22.07.2026 против 63 строк
у 9 мастеров в каталоге, `scheduleexception` — 0 строк.

Правило без сторожа — совпадение: сегодня дашборд чинится, а завтра появится
новый читатель копии мимо флага, и никто этого не заметит. Поэтому каждый файл
`apps/` (не тесты, не миграции), называющий `WorkingHours` или
`ScheduleException`, имеет запись с **своей** причиной и номером листа/вопроса:

* ``FRAME`` — сам переключатель источника (`load_day_frame`);
* ``WRITER_OR_OPERATOR`` — писатели и операторские экраны: снятие копии — вопрос
  владельца X5, здесь они не трогаются;
* ``CLIENT_PATH_X6`` — клиентский путь, решение X6;
* ``DEBT`` — известный безфлаговый читатель с номером листа.

И отдельно: файлы, которым называть модели копии **нельзя вовсе** — они обязаны
читать рамку через `load_day_frame`. Дашборд мастера (DRF-2014) именно такой.

**Нижняя граница объявлена.** Квантор «для всех» пуст на пустом скане: если
сканер ничего не нашёл, тест обязан краснеть, а не радоваться. Ниже стоят
явные минимумы на число просканированных файлов и на число найденных
упоминаний.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APPS = ROOT / "apps"

#: Имена моделей локальной копии расписания.
LOCAL_MODELS = {"WorkingHours", "ScheduleException"}
#: Флаг-осведомлённый загрузчик рамки.
FRAME_LOADER = "load_day_frame"

FRAME = "FRAME"
WRITER_OR_OPERATOR = "WRITER_OR_OPERATOR"
CLIENT_PATH_X6 = "CLIENT_PATH_X6"
DEBT = "DEBT"

#: Файлы, которым называть модели копии нельзя: читают рамку через предикат.
FORBIDDEN: dict[str, str] = {
    "apps/master_api/services/dashboard.py": (
        "DRF-2014: окно дашборда — каталожная рамка через load_day_frame; "
        "прямое чтение копии смешивало устаревшую рамку со свежей занятостью"
    ),
}

#: Перепись бот `dev` `8bf99f7c` (16.09.2026), после правки DRF-2014.
EXPECTED: dict[str, tuple[str, str]] = {
    "apps/master_api/services/schedule_frame.py": (
        FRAME,
        "сам переключатель: флаг OFF — локальные таблицы, ON — каталог",
    ),
    "apps/master_api/services/schedule.py": (
        FRAME,
        "экран расписания и заявка на отсутствие читают рамку через load_day_frame "
        "(DRF-2019); ScheduleException здесь — словарь видов (Type, FULL_DAY_TYPES), "
        "а не чтение копии",
    ),
    "apps/scheduling/admin.py": (WRITER_OR_OPERATOR, "операторский CRUD копии; вопрос X5"),
    "apps/scheduling/services/resolver.py": (
        WRITER_OR_OPERATOR,
        "локальный резолвер слотов пути «флаг выключен»; вопрос X5",
    ),
    "apps/admin_api/services/availability.py": (
        WRITER_OR_OPERATOR,
        "одобрение заявки мастера пишет локальное исключение; путь записи не "
        "трогаем (X5), запись в каталог рядом и под флагом",
    ),
    "apps/catalog/services/schedule_confirmation.py": (
        WRITER_OR_OPERATOR,
        "подтверждение недели: свой флаг-осведомлённый выбор источника, "
        "FrameHours брать нельзя — теряются перерывы",
    ),
    "apps/catalog/management/commands/seed_dev_formula_tela.py": (
        WRITER_OR_OPERATOR,
        "dev-фикстура",
    ),
}

#: Предмет первой переписи — две модели РАМКИ дня (`WorkingHours`,
#: `ScheduleException`). `TimeBlock` сюда не входит и намеренно не значится:
#: локальный резолвер слотов и локальное создание записи пути «флаг выключен»
#: — X5; они не читают рамку и не могут показать мастеру чужое рабочее время.
#:
#: Вторая перепись (ниже, `SLOT_POLICY_EXPECTED`) — политика слотов
#: `SlotConfig` / `get_slot_config`. До решения X6 она стояла здесь как «вопрос
#: владельца» и сканером не покрывалась; X6 решён (DRF-2014, 17.09): на пути
#: `BOOKING_VIA_AYLA_REST=true` горизонт и шаг слотов — каталога, локальную
#: копию политики не читает ни один клиентский путь. Нашлось это ровно так, как
#: и предупреждал абзац выше: клиентский горизонт (`miniapp_api/views.py`)
#: читал `get_slot_config` ДО ветки флага, и локальное умолчание 60 дней
#: клампило окно каталога — держалось совпадением с `BOOKING_MAX_AHEAD_DAYS=60`.
#:
#: Нижняя граница скана: меньше — значит сканер сломан, а не стало чисто.
MIN_FILES_SCANNED = 100
MIN_CLASSIFIED_FILES = 7
MIN_MENTIONS = 35

# ── Вторая перепись: политика слотов ────────────────────────────────────────

#: Имена локальной политики слотов: модель и её единственный читатель-функция.
SLOT_POLICY_NAMES = {"SlotConfig", "get_slot_config"}

FLAG_OFF_BRANCH = "FLAG_OFF_BRANCH"

#: Перепись бот `dev` `d5e3120a` (17.09.2026), после правки DRF-2014.
SLOT_POLICY_EXPECTED: dict[str, tuple[str, str]] = {
    "apps/scheduling/admin.py": (WRITER_OR_OPERATOR, "операторский CRUD политики; вопрос X5"),
    "apps/scheduling/services/resolver.py": (
        WRITER_OR_OPERATOR,
        "владелец get_slot_config и локальный резолвер пути «флаг выключен»; X5",
    ),
    "apps/booking/services/create.py": (
        CLIENT_PATH_X6,
        "локальное создание записи — только ветка «флаг выключен» "
        "(views.py ветвит создание по BOOKING_VIA_AYLA_REST); X5",
    ),
    "apps/miniapp_api/views.py": (
        FLAG_OFF_BRANCH,
        "клиентские слоты: get_slot_config читается ТОЛЬКО на ветке «флаг выключен» — "
        "поведение держит test_slot_config_not_read_via_ayla_2014 (N2: на пути Ayla "
        "вызовов ноль), а не это слово",
    ),
}
MIN_SLOT_POLICY_FILES = 4
MIN_SLOT_POLICY_MENTIONS = 6


#: Что перепись видит, а что нет — названный предел, а не молчание.
#:
#: Видит: прямое обращение к классу (`WorkingHours`, `ScheduleException` —
#: имя, атрибут, импорт) и динамическую загрузку `apps.get_model("scheduling", …)`
#: / `get_model("scheduling.WorkingHours")`.
#:
#: НЕ видит: обратный менеджер по `related_name` (`master.working_hours`,
#: `master.schedule_exceptions`) и `getattr` по вычисленному имени. Покрыть их
#: одним написанием нельзя без ложных срабатываний: `services/schedule.py:202`
#: держит `"working_hours": d.working_hours` — это поле дневного DTO, а не
#: обратный менеджер, и шаблон ошибался бы в обе стороны. Читатель через
#: обратный менеджер пройдёт мимо этой переписи — предел назван здесь и в теле PR.
NAMED_LIMITS = (
    "обратный менеджер по related_name (master.working_hours / .schedule_exceptions)",
    "getattr по вычисленному имени модели",
)


def _is_scheduling_get_model(node: ast.Call) -> bool:
    """`apps.get_model("scheduling", "WorkingHours")` / `get_model("scheduling.X")`."""
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    if name != "get_model":
        return False
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if arg.value.split(".")[0] == "scheduling":
                return True
    return False


def mentions(source: str, names: set[str] | None = None) -> int:
    """Сколько раз файл называет имена ``names`` (умолчание — модели рамки; см. NAMED_LIMITS)."""
    names = LOCAL_MODELS if names is None else names
    tree = ast.parse(source)
    found = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            found += 1
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found += 1
        elif isinstance(node, ast.alias) and node.name in names:
            found += 1
        elif (
            names is LOCAL_MODELS and isinstance(node, ast.Call) and _is_scheduling_get_model(node)
        ):
            found += 1
    return found


def uses_frame_loader(source: str) -> bool:
    return FRAME_LOADER in source


def violations(
    sources: dict[str, str],
    expected: dict[str, tuple[str, str]] | None = None,
    forbidden: dict[str, str] | None = None,
    names: set[str] | None = None,
) -> list[str]:
    """Нарушения переписи. ``expected``/``forbidden`` — параметры, а не глобали:
    иначе сторож нельзя позвать на своих же синтетических входах, и его
    собственная стража меряла бы боевую перепись. ``names`` — какие имена
    считать (рамка дня по умолчанию; политика слотов — ``SLOT_POLICY_NAMES``)."""
    expected = EXPECTED if expected is None else expected
    forbidden = FORBIDDEN if forbidden is None else forbidden
    out: list[str] = []
    seen: set[str] = set()
    for rel, source in sorted(sources.items()):
        found = mentions(source, names)
        if rel in forbidden:
            if found:
                out.append(f"{rel}: читает копию расписания напрямую ({found}) — {forbidden[rel]}")
            elif not uses_frame_loader(source):
                out.append(f"{rel}: не читает ни копию, ни рамку — {forbidden[rel]}")
            continue
        if not found:
            continue
        seen.add(rel)
        if rel not in expected:
            out.append(f"{rel}: {found} упоминаний копии расписания, файл не классифицирован")
    for rel in sorted(set(expected) - seen):
        out.append(f"{rel}: в переписи, но упоминаний нет — убрать запись")
    return out


def _sources() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in APPS.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        found[rel] = path.read_text(encoding="utf-8")
    return found


#: Синтетический файл, читающий копию напрямую — две ссылки на класс.
_BLIND_READER = (
    "from apps.scheduling.models import WorkingHours\n"
    "def _block(master, day):\n"
    "    return WorkingHours.all_tenants.filter(master_id=master.id).first()\n"
)
_THROUGH_FRAME = (
    "from apps.master_api.services.schedule_frame import load_day_frame\n"
    "def _block(master, day, tz):\n"
    "    wh, exc, extra = load_day_frame(master, from_date=day, to_date=day, tz=tz)\n"
    "    return wh.get(day.weekday())\n"
)


def test_every_local_schedule_reader_is_classified():
    sources = _sources()
    # Нижняя граница: пустой или обрезанный скан обязан краснеть.
    assert len(sources) >= MIN_FILES_SCANNED, len(sources)
    classified = {rel: src for rel, src in sources.items() if rel in EXPECTED}
    assert len(classified) >= MIN_CLASSIFIED_FILES, sorted(classified)
    assert sum(mentions(src) for src in classified.values()) >= MIN_MENTIONS

    # Присутствие до отсутствия: на ТОМ ЖЕ наборе подменённый файл обязан
    # ловиться. Иначе пустой список ниже читался бы как «чисто», а значил бы
    # «сканер молчит».
    poisoned = dict(sources)
    poisoned[next(iter(FORBIDDEN))] = _BLIND_READER
    assert violations(poisoned), "сторож не видит прямого чтения копии"

    assert violations(sources) == []

    # У каждой записи — своя причина, а не общая формулировка на всех.
    reasons = [reason for _kind, reason in EXPECTED.values()]
    assert len(set(reasons)) == len(reasons)
    # Пределы названы в самом стороже, а не только в теле PR.
    assert NAMED_LIMITS and all(NAMED_LIMITS)


def test_availability_path_is_no_longer_classified_as_debt():
    """Переход записи переписи — такое же утверждение о коде, как любое другое (DRF-2019).

    После правки `request_availability_change` сверяет пересечение по живой
    рамке, и запись про долг становится ложной. Узел стоит отдельно от порядка
    слияния: если PR соберут в другом порядке или один отменят, он скажет об
    этом вслух, а не промолчит, потому что «в линии коммитов так вышло».
    """
    rel = "apps/master_api/services/schedule.py"
    kind, reason = EXPECTED[rel]

    # Роль, а не написание: ключ DRF-2019 в причине законен — он называет лист,
    # которым файл стал читателем рамки. Запрещать подстроку значило бы стеречь
    # орфографию; стережём класс записи и то, чем он обоснован.
    assert kind == FRAME, (kind, reason)
    assert FRAME_LOADER in reason, reason


def test_guard_catches_a_new_flag_blind_reader():
    """Положительная стража сторожа — на своих входах, не на боевой переписи.

    ``violations`` принимает перепись параметром именно ради этого: с
    глобальным словарём синтетический вызов тянул бы за собой все боевые
    записи и проверял бы не сторожа, а их.
    """
    rel = "apps/x/services/dashboard.py"
    forbidden = {rel: "синтетика: обязан читать рамку"}

    assert violations({rel: _BLIND_READER}, expected={}, forbidden=forbidden) == [
        f"{rel}: читает копию расписания напрямую (2) — {forbidden[rel]}"
    ]
    assert violations({rel: _THROUGH_FRAME}, expected={}, forbidden=forbidden) == []

    # Новый читатель копии, которого нет в переписи, — тоже красный.
    new_reader = "apps/x/services/new_reader.py"
    assert violations({new_reader: _BLIND_READER}, expected={}, forbidden={}) == [
        f"{new_reader}: 2 упоминаний копии расписания, файл не классифицирован"
    ]


# ── Вторая перепись: политика слотов (DRF-2014, X6 решён) ───────────────────

_SLOT_POLICY_READER = (
    "from apps.scheduling.services.resolver import get_slot_config\n"
    "def horizon(tenant):\n"
    "    return get_slot_config(tenant).max_advance_days\n"
)


def test_every_slot_policy_reader_is_classified():
    """Каждый файл, называющий `SlotConfig` / `get_slot_config`, имеет запись со
    своей причиной. Клиентский путь — только `FLAG_OFF_BRANCH`, и его причина
    обязана называть поведенческий узел, а не обещать словами."""
    sources = _sources()
    assert len(sources) >= MIN_FILES_SCANNED, len(sources)
    classified = {rel: src for rel, src in sources.items() if rel in SLOT_POLICY_EXPECTED}
    assert len(classified) >= MIN_SLOT_POLICY_FILES, sorted(classified)
    assert (
        sum(mentions(src, SLOT_POLICY_NAMES) for src in classified.values())
        >= MIN_SLOT_POLICY_MENTIONS
    )

    # Присутствие до отсутствия: подменённый файл на том же наборе ловится.
    poisoned = dict(sources)
    poisoned["apps/x/services/new_horizon.py"] = _SLOT_POLICY_READER
    assert violations(
        poisoned, expected=SLOT_POLICY_EXPECTED, forbidden={}, names=SLOT_POLICY_NAMES
    )

    assert (
        violations(sources, expected=SLOT_POLICY_EXPECTED, forbidden={}, names=SLOT_POLICY_NAMES)
        == []
    )

    reasons = [reason for _kind, reason in SLOT_POLICY_EXPECTED.values()]
    assert len(set(reasons)) == len(reasons)
    kind, reason = SLOT_POLICY_EXPECTED["apps/miniapp_api/views.py"]
    assert kind == FLAG_OFF_BRANCH
    assert "test_slot_config_not_read_via_ayla_2014" in reason


def test_slot_policy_guard_catches_a_new_reader():
    new_reader = "apps/x/services/new_horizon.py"
    assert violations(
        {new_reader: _SLOT_POLICY_READER}, expected={}, forbidden={}, names=SLOT_POLICY_NAMES
    ) == [f"{new_reader}: 2 упоминаний копии расписания, файл не классифицирован"]
    # Файл, читающий только рамку дня, второй переписи не касается.
    assert (
        violations({new_reader: _THROUGH_FRAME}, expected={}, forbidden={}, names=SLOT_POLICY_NAMES)
        == []
    )
