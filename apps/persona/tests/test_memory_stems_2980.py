"""DRF-2980 / DRF-2979 — команда «забудь …» стирает тот домен, который назван.

Замер на dev (``d52b1c37`` и поверх DRF-2891): домен искался ПОДСТРОКОЙ стебля
в любом месте предмета, и настоящая команда памяти стирала не тот домен:

* «забудь, что я живу в центре» → бюджет («цен» ⊂ «центре»), а район оставался;
* «забудь мою оценку», «…что я ценю тишину», «…про мои ценности» → бюджет;
* «забудь, что мне удобно по средам» → питание («еда» ⊂ «средам»);
* «забудь, что у меня огород» → город; «…живу вокруг парка» → районы;
* «забудь про мастер-класс», «…про мастерскую» → любимые мастера;
* «забудь про вечеринку» → «когда приходишь»; «забудь временно…» → удобное время;
* «забудь, что у меня проблема с анкетой» при кето → кето («кето» ⊂ «анкетой»);
* DRF-2979: кнопка «Забыть: когда приходишь» шлёт «забудь когда приходишь», а
  стебель «когда прихож» (через «ж») её не ловил — ничего не стиралось.

Узлы:

* c1 — чистая функция ``_named_domains`` на корпусе: ложные домены не находятся,
  естественные формы каждого домена — находятся;
* c2 — каждая подпись кнопки (и «про {подпись}») называет ровно свой домен;
* c3 — «еду» как глагол и «после еды» — не питание, «про еду» — питание;
* d1 — на БД: ложный домен не стирает строку-«жертву»;
* d2 — однозначная команда при двух доменах в памяти стирает только свой;
* d3 — при всех семи строках каждая кнопка стирает свой домен и только его;
* d4 — строка с ключом не из семи доменов не ломает команду;
* d5 — факты: «анкетой» не стирает кето, «я кето» и «кетодиету» стирают;
* p1 — шаблоны линейны: вдвое длиннее вход — не более чем втрое дольше.
"""

from __future__ import annotations

import time
import uuid

import pytest

from apps.persona import memory_commands
from apps.persona.memory_commands import (
    _DOMAIN_LABELS,
    _FORGET_FIELD_RE,
    _KEY_KEYWORDS,
    _named_domains,
    _normalise,
    _spoken,
    handle_memory_command,
    memory_show_chips,
)

D, T, R, P, M, C, V = (
    "diet",
    "preferred_time_slots",
    "preferred_districts",
    "price_range",
    "favorite_masters",
    "city",
    "visit_context",
)


def _named(text: str) -> set[str]:
    match = _FORGET_FIELD_RE.search(_spoken(_normalise(text)))
    assert match is not None, f"не команда: {text}"
    return _named_domains(match.group(0))


#: Реплика → какие домены она называет. Пустое множество — никакой.
WRONG_DOMAIN = [
    ("забудь, что я живу в центре", {R}),  # было: бюджет; «центре» районом пишет said_memory
    ("забудь мою оценку", set()),
    ("забудь, что я ценю тишину", set()),
    ("забудь про мои ценности", set()),
    ("забудь, что мне удобно по средам", set()),
    ("забудь, что у меня огород", set()),
    ("забудь, что я живу вокруг парка", set()),
    ("забудь про мастер-класс", set()),
    ("забудь про мастер класс", set()),
    ("забудь про мастерскую", set()),
    ("забудь про вечеринку", set()),
    ("забудь временно про это", set()),
    ("забудь, что я округляю", set()),
    ("забудь, что я из городской больницы", set()),
    ("забудь, что у меня дача за городом", set()),
    ("забудь про вечерний макияж", set()),
    ("забудь про вечернее платье", set()),
    ("забудь, когда приходят гости", set()),
    ("забудь, когда приходит зарплата", set()),
    ("забудь, что у меня мало времени", set()),
    ("забудь, что я пью таблетки после еды", set()),
    ("забудь про пищевод", set()),
]

NATURAL = [
    # питание
    ("забудь про питание", {D}),
    ("забудь всё про моё питание", {D}),
    ("забудь мою диету", {D}),
    ("забудь про диеты", {D}),
    ("забудь что я на диете", {D}),
    ("забудь про кетодиету", {D}),
    ("забудь про еду", {D}),
    ("забудь мою еду", {D}),
    ("забудь что я ем мясо", {D}),
    ("забудь что я не ем глютен", {D}),
    ("забудь про мою пищу", {D}),
    ("забудь про пищевые ограничения", {D}),
    # удобное время
    ("забудь удобное время", {T}),
    ("забудь про удобное время", {T}),
    ("забудь удобное мне время", {T}),
    ("забудь в какое время мне удобно", {T}),
    ("забудь что мне удобно по утрам", {T}),
    ("забудь что мне удобно утром", {T}),
    # районы
    ("забудь мой район", {R}),
    ("забудь про районы", {R}),
    ("забудь мой микрорайон", {R}),
    ("забудь какое у меня метро", {R}),
    ("забудь про округ", {R}),
    # бюджет
    ("забудь мой бюджет", {P}),
    ("забудь про цены", {P}),
    ("забудь про цену", {P}),
    ("забудь мою ценовую категорию", {P}),
    ("забудь про ценовой диапазон", {P}),
    ("забудь сколько я готова тратить денег", {P}),
    ("забудь про деньги", {P}),
    ("забудь про рубли", {P}),
    ("забудь стоимость", {P}),
    # любимые мастера
    ("забудь про мастера", {M}),
    ("забудь любимого мастера", {M}),
    ("забудь про мастеров", {M}),
    ("забудь мою мастерицу", {M}),
    ("забудь про мастерицу Ольгу", {M}),
    ("забудь моего мастера-колориста", {M}),
    # город
    ("забудь мой город", {C}),
    ("забудь в каком я городе", {C}),
    ("забудь, что я из другого города", {C}),
    # когда приходишь
    ("забудь когда я прихожу", {V}),
    ("забудь когда я обычно прихожу", {V}),
    ("забудь про выходные", {V}),
    ("забудь что я прихожу после работы", {V}),
    ("забудь что я прихожу вечерком", {V}),
    ("забудь про вечерние визиты", {V}),
    # оба: «по вечерам» — и удобное время, и «когда приходишь»
    ("забудь что мне удобно по вечерам", {T, V}),
]


@pytest.mark.parametrize(("text", "named"), WRONG_DOMAIN + NATURAL)
def test_c1_the_command_names_exactly_these_domains(text: str, named: set[str]) -> None:
    assert _named(text) == named


def test_c2_every_domain_has_a_pattern_and_a_label() -> None:
    assert set(_KEY_KEYWORDS) == set(_DOMAIN_LABELS)


@pytest.mark.parametrize("domain", sorted(_DOMAIN_LABELS))
@pytest.mark.parametrize("form", ["забудь {label}", "забудь про {label}"])
def test_c2_every_chip_label_names_only_its_domain(domain: str, form: str) -> None:
    assert _named(form.format(label=_DOMAIN_LABELS[domain])) == {domain}


@pytest.mark.parametrize(
    "text",
    [
        "забудь что я еду в отпуск",
        "забудь что я еду послезавтра",
        "забудь что я еду через час",
        "забудь что я еду по делам",
        "забудь что я уже не еду",
        "забудь куда я еду",
        "забудь что я еду",
    ],
)
def test_c3_the_verb_to_go_is_not_food(text: str) -> None:
    # Близнец: то же слово существительным после «про» — питание.
    assert _named("забудь про еду") == {D}

    assert D not in _named(text)  # empty-assert-ok: близнец строкой выше — «про еду» → питание


# --- на БД -------------------------------------------------------------------

from apps.consent.services import record_global_consent  # noqa: E402
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext  # noqa: E402
from apps.identity.services import resolve_or_create_global_bot_user  # noqa: E402
from apps.identity.services.memory_reader import read_green_entries  # noqa: E402

db = pytest.mark.django_db(transaction=True)


@pytest.fixture
def make_person(settings: object, monkeypatch: pytest.MonkeyPatch):
    """Человек с заданными строками памяти. Анкета Ayla не предмет — мост заглушён."""
    settings.STRICT_TENANT_SCOPE = "strict"  # type: ignore[attr-defined]
    monkeypatch.setattr(memory_commands, "_bridge_clear", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_commands, "_declared_phrases_for_show", lambda *args: {})

    def make(rows: list[tuple[str, str]]) -> BotUser:
        user_id = uuid.uuid4()
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=f"2980-{user_id.hex[:10]}", ayla_user_id=user_id
        )
        record_global_consent(bot_user, source="welcome")
        context = UserPersonalContext.objects.create(user_id=user_id)
        for key, value in rows:
            MemoryEntry.objects.create(
                user_id=user_id,
                personal_context=context,
                sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_EXPLICIT,
                provenance=MemoryEntry.PROVENANCE_USER_STATED,
                kind="lifestyle",
                content={"key": key, "value": value},
            )
        return bot_user

    return make


def _say(person: BotUser, text: str):
    assert person.ayla_user_id is not None
    return handle_memory_command(user_id=person.ayla_user_id, text=text, bot_user=person)


def _keys(person: BotUser) -> set[str]:
    assert person.ayla_user_id is not None
    return {e.content["key"] for e in read_green_entries(person.ayla_user_id)}


#: (строка-«жертва», реплика). На dev каждая из этих реплик стирала строку.
VICTIMS = [
    (("price_range", "до 2000"), "забудь мою оценку"),
    (("price_range", "до 2000"), "забудь, что я ценю тишину"),
    (("diet", "vegan"), "забудь, что мне удобно по средам"),
    (("city", "Пенза"), "забудь, что у меня огород"),
    (("preferred_districts", "центр"), "забудь, что я живу вокруг парка"),
    (("favorite_masters", "Ольга"), "забудь про мастер-класс"),
    (("visit_context", "evening"), "забудь про вечеринку"),
    (("preferred_time_slots", "evening"), "забудь временно про это"),
]


@db
@pytest.mark.parametrize(("row", "text"), VICTIMS)
def test_d1_a_wrong_domain_word_does_not_erase_the_row(make_person, row, text) -> None:
    person = make_person([row])

    result = _say(person, text)

    assert result is not None
    assert not result.erased
    assert _keys(person) == {row[0]}


@db
def test_d1_living_in_the_centre_erases_the_district_not_the_budget(make_person) -> None:
    person = make_person([("price_range", "до 2000"), ("preferred_districts", "центре")])

    result = _say(person, "забудь, что я живу в центре")

    assert result is not None and result.erased
    assert _keys(person) == {"price_range"}


@db
def test_d2_one_named_domain_out_of_two_stored(make_person) -> None:
    person = make_person([("city", "Пенза"), ("price_range", "до 2000")])

    result = _say(person, "забудь, что я из другого города")

    assert result is not None and result.erased
    assert _keys(person) == {"price_range"}


ALL_ROWS = [
    ("diet", "vegan"),
    ("preferred_time_slots", "evening"),
    ("preferred_districts", "центр"),
    ("price_range", "до 2000"),
    ("favorite_masters", "Ольга"),
    ("city", "Пенза"),
    ("visit_context", "after_work"),
]


@db
@pytest.mark.parametrize("domain", sorted(_DOMAIN_LABELS))
def test_d3_with_everything_stored_each_chip_erases_only_its_domain(
    make_person, domain: str
) -> None:
    person = make_person(ALL_ROWS)
    assert person.ayla_user_id is not None
    chips = {
        chip["callback"]
        for chip in memory_show_chips(person, user_id=person.ayla_user_id)
        if chip["label"].endswith(_DOMAIN_LABELS[domain])
    }
    callback = chips.pop() if chips else f"забудь {_DOMAIN_LABELS[domain]}"

    result = _say(person, callback)

    assert result is not None and result.erased, result
    assert _keys(person) == {key for key, _ in ALL_ROWS} - {domain}


@db
def test_d4_a_row_outside_the_seven_domains_does_not_break_the_command(make_person) -> None:
    person = make_person([("allergy", "орехи"), ("diet", "vegan")])

    erased = _say(person, "забудь про питание")
    asked = _say(person, "забудь мою аллергию")

    assert erased is not None and erased.erased
    assert asked is not None and not asked.erased
    assert _keys(person) == {"allergy"}


@db
@pytest.mark.parametrize(
    ("text", "erased"),
    [
        ("забудь, что у меня проблема с анкетой", False),
        ("забудь про банкеты", False),
        ("забудь, что я кето", True),
        ("забудь про кетодиету", True),
    ],
)
def test_d5_a_fact_is_matched_from_the_start_of_a_word(make_person, text, erased) -> None:
    person = make_person([("diet", "keto")])

    result = _say(person, text)

    assert result is not None
    assert result.erased is erased
    assert _keys(person) == (set() if erased else {"diet"})


def test_p1_the_patterns_stay_linear() -> None:
    def run(n: int) -> float:
        hostile = [
            "когда я " * n,
            "еду в " * n,
            "вечер " * n,
            "мастер-" * n,
            "удобное " * n + "x",
            "про " * n + "еду",
        ]
        started = time.perf_counter()
        for text in hostile:
            _named_domains(text)
        return time.perf_counter() - started

    run(1_000)  # прогрев
    small, large = run(10_000), run(20_000)

    assert large < max(small, 0.01) * 3, (small, large)
