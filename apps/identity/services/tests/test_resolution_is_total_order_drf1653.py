"""Выбор строки не должен зависеть от того, тикнули ли часы (DRF-1653).

### Что сломалось и почему это не про тесты

`apps/master_api/tests/test_auth_bot_user_resolution.py` падал один раз из
двух в трёхпакетном прогоне и проходил в одиночном. Причина оказалась не в
раскладке файлов и не в общем состоянии:

* `BotUser.last_seen` — `auto_now=True` (`apps/identity/models.py:167`), и
  значение, переданное в `create()`, **молча выбрасывается**;
* фикстура просила разрыв в 8 часов, а получала 8 миллисекунд — ровно
  столько, сколько занимала вторая вставка;
* `order_by("-last_seen")` — **не полный порядок**. При равенстве Postgres
  волен вернуть любую строку и волен вернуть в следующий раз другую: `UPDATE`
  переносит строку внутри heap.

Замер, 11.09.2026:

    фикстура просила разницу   28800.0 с
    в базе оказалось               0.008261 с

В полном прогоне интерпретатор прогрет, вставки быстрее, разрыв схлопывается
в один тик — ничья, и ответ произволен. В одиночном прогоне пакет медленнее,
разрыв уцелевает, тест зелёный. «Один раз из двух» — это про **скорость**, а
не про состояние.

### Почему это дефект продукта, а не теста

Резолвер отвечает на вопрос «кто этот человек»: в одном тенанте он владелец,
в другом посторонний. Ответ, который меняется без изменения входа, хуже
неверного — его нельзя ни воспроизвести, ни описать в отчёте, ни покрыть
тестом.

### Почему `last_seen` здесь задаётся через `update()`

`create()` и `save()` не могут его задать — `auto_now` перепишет. `update()`
не грузит и не сохраняет модель, поэтому `auto_now` не срабатывает. Это
единственный способ **создать ничью намеренно**, а без намеренной ничьей
сторож стережёт ту же гонку с часами, что и сломанный тест.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.identity.models import BotUser
from apps.identity.services.bot_user_resolver import resolve_bot_user
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "83146139"


class _Verified:
    def __init__(self, user_id: str, bot_slug: str = "") -> None:
        self.user_id = user_id
        self.bot_slug = bot_slug


@pytest.fixture
def tied_rows(settings) -> tuple[BotUser, BotUser]:
    """Две строки одного человека с ПОБАЙТОВО равным `last_seen`."""

    settings.MAX_BOT_REGISTRY = ()
    settings.MAX_BOT_TENANT_SLUG = ""
    salon, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    glob, _ = Tenant.all_objects.get_or_create(slug="global_bot", defaults={"name": "Global bot"})

    first = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)
    second = BotUser.all_tenants.create(tenant=glob, channel="max", channel_user_id=CHANNEL_USER_ID)
    tie = timezone.now()
    BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).update(last_seen=tie)
    first.refresh_from_db()
    second.refresh_from_db()
    return first, second


def test_the_tie_is_real_before_anything_is_asserted_about_it(
    tied_rows: tuple[BotUser, BotUser],
) -> None:
    """Сначала — что ничья действительно построена.

    Если `update()` перестанет обходить `auto_now`, или фикстура разъедется,
    остальные тесты ниже начнут проверять обычный, различимый порядок — и
    останутся зелёными, ничего не стережа. Ничья — это их предмет, и её
    наличие надо утверждать отдельно.
    """
    first, second = tied_rows

    assert first.last_seen is not None
    assert first.last_seen == second.last_seen


def _order_by_clause(sql: str) -> str:
    head, _, tail = sql.partition("ORDER BY")
    assert tail, f"в запросе нет ORDER BY вовсе: {sql}"
    return tail


def _botuser_select(captured) -> str:
    """Тот единственный SELECT по botuser, в котором и решается порядок."""

    rows = [
        q["sql"]
        for q in captured.captured_queries
        if "identity_botuser" in q["sql"] and q["sql"].lstrip().upper().startswith("SELECT")
    ]
    assert rows, f"ни одного SELECT по botuser не перехвачено: {captured.captured_queries}"
    return rows[-1]


def test_the_resolver_asks_the_database_for_a_TOTAL_order(
    tied_rows: tuple[BotUser, BotUser],
) -> None:
    """Порядок обязан доопределяться уникальным полем — это проверка по SQL.

    ### Почему не «ответ повторился»

    Здесь стоял тест «позвали дважды, получили то же самое». Он **проходил и
    без тай-брейка**, и это выяснилось подменой: произвольный порядок бывает
    устойчивым. Postgres при ничьей волен вернуть любую строку — но «волен» не
    значит «каждый раз другую». На одном и том же плане, с холодной таблицей и
    без перемещений он отдаёт одну и ту же, сколько ни спрашивай.

    То есть повторяемость **наблюдаемая** не отличает «порядок полный» от
    «порядок произвольный, но сегодня стабильный». Сторож, зелёный на сломанном
    коде, — не сторож.

    Проверяемое утверждение здесь не про сегодняшний ответ, а про **обещание**,
    которое запрос даёт базе: в `ORDER BY` должно стоять поле, уникальное по
    строке. Тогда ответ один и тот же не потому, что повезло, а потому, что
    другого быть не может.
    """
    with CaptureQueriesContext(connection) as captured:
        resolved = resolve_bot_user(_Verified(CHANNEL_USER_ID), surface="probe")

    assert resolved is not None, "резолвер никого не нашёл — проверять порядок не в чем"
    order_by = _order_by_clause(_botuser_select(captured))

    assert '"id"' in order_by, (
        "ORDER BY не доопределён уникальным полем: "
        f"{order_by.strip()!r}. При равном last_seen Postgres вправе вернуть "
        "любую из строк — и вправе вернуть в следующий раз другую."
    )


def test_resolution_repeats_itself_on_a_tie(tied_rows: tuple[BotUser, BotUser]) -> None:
    """Наблюдаемая половина того же: дважды подряд — один ответ.

    Слабее предыдущего и сама по себе ничего не доказывает (см. его докстринг).
    Оставлена потому, что проверяет другое: что обещание из `ORDER BY`
    действительно доезжает до ответа, а не остаётся в тексте запроса.
    """
    once = resolve_bot_user(_Verified(CHANNEL_USER_ID), surface="probe")
    twice = resolve_bot_user(_Verified(CHANNEL_USER_ID), surface="probe")

    # Оба конца проверяются до сравнения: `None == None` прошло бы молча и
    # означало бы «резолвер не нашёл никого», а не «нашёл одно и то же».
    assert once is not None
    assert twice is not None
    assert once.pk == twice.pk


def test_resolution_survives_a_row_moving_within_the_heap(
    tied_rows: tuple[BotUser, BotUser],
) -> None:
    """`UPDATE` переносит строку физически — ответ обязан не измениться.

    Это и есть тот способ, которым «произвольный порядок» проявляется в
    Postgres на живых данных: строку тронули, она переехала, и запрос без
    полного порядка стал отдавать другую.
    """
    first, _second = tied_rows
    before = resolve_bot_user(_Verified(CHANNEL_USER_ID), surface="probe")
    assert before is not None

    tie = first.last_seen
    BotUser.all_tenants.filter(pk=first.pk).update(chat_id="moved")
    BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).update(last_seen=tie)

    after = resolve_bot_user(_Verified(CHANNEL_USER_ID), surface="probe")

    assert after is not None
    assert before.pk == after.pk


def test_reminder_routing_repeats_itself_on_a_tie(settings) -> None:
    """Второе место с тем же дефектом: `eventbus/consumers/booking.py`.

    Там пара `(tenant, ayla_user_id)` неуникальна **по построению** — один
    человек держит по строке на канал, и ради этого сортировка и заведена.
    Значит ничья там не редкость, а ожидаемое состояние, и напоминание могло
    бы уходить сегодня в MAX, завтра в Telegram без единого изменения входа.
    """
    import uuid

    from apps.eventbus.consumers.booking import _resolve_bot_user as _reminder_row  # noqa: PLC0415

    tenant, _ = Tenant.all_objects.get_or_create(slug="formula-tela", defaults={"name": "Ф"})
    ayla_user_id = uuid.uuid4()
    for channel in ("max", "telegram"):
        BotUser.all_tenants.create(
            tenant=tenant,
            channel=channel,
            channel_user_id=f"u-{channel}",
            ayla_user_id=ayla_user_id,
        )
    tie = timezone.now()
    rows = BotUser.all_tenants.filter(tenant=tenant, ayla_user_id=ayla_user_id)
    rows.update(last_seen=tie)

    seen = {row.last_seen for row in rows}
    assert len(seen) == 1, "ничья не построена — стеречь нечего"

    with CaptureQueriesContext(connection) as captured:
        once = _reminder_row(tenant=tenant, user_id=ayla_user_id)
    assert once is not None, "напоминание некому маршрутизировать — стеречь нечего"

    order_by = _order_by_clause(_botuser_select(captured))
    assert '"id"' in order_by, (
        "маршрутизация напоминания просит у базы неполный порядок: "
        f"{order_by.strip()!r}. Пара (tenant, ayla_user_id) неуникальна ПО "
        "ПОСТРОЕНИЮ — по строке на канал, — значит ничья здесь ожидаема, а не "
        "редка, и напоминание может уйти сегодня в один канал, завтра в другой."
    )

    BotUser.all_tenants.filter(pk=once.pk).update(chat_id="moved")
    rows.update(last_seen=tie)
    twice = _reminder_row(tenant=tenant, user_id=ayla_user_id)

    assert twice is not None
    assert once.pk == twice.pk
