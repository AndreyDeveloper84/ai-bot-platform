"""E2E-06 — частичный успех провижининга каталога НЕ переживает обрыв.

Узлы написаны 16.09 **до** правки и были красными на ``dev`` без неё; правка
пришла PR #1799 (merge ``e8f1570e``). Здесь они остаются **регрессионными
сторожами** пути ``provision_catalog_workspace`` — по нему идут три живых
зовущих (``apps/channels/max/salon_handler.py``, ``solo_identity_link.py``),
минуя новую операцию ``ensure_catalog_specialist_identity``.

### Предмет — щель между двумя УСПЕШНЫМИ записями

``apps/identity/services/solo_catalog_provisioning.py`` @ ``e8f1570e``::

    :120-135  with transaction.atomic():
                  link.catalog_specialist_id  = dto.specialist_id
                  link.catalog_provisioned_at = timezone.now()
                  link.save(...)                     # ← запись №1
                  link.master.catalog_specialist_id = dto.specialist_id
                  link.master.save(...)              # ← запись №2

До правки транзакции не было (``atomic`` — 0 вхождений): обрыв между №1 и №2
оставлял связь со значением, а зеркало — пустым.

### Почему это было хуже, чем «две копии разошлись»

Вход охраняется (``:64``)::

    if link.catalog_provisioned_at is not None:

``catalog_provisioned_at`` ставился записью №1 — значит обрыв между №1 и №2
делал расхождение **самозапирающимся**: повтор видел «уже подготовлено» и
возвращался, не тронув зеркала. Правка ответила двумя частями:

1. ``:120-135`` — обе записи в одной транзакции: обрыв откатывает и №1, и
   повтор идёт в каталог заново. Это стерегут узлы первого класса.
2. ``:64-86`` — ранний возврат **досылает** зеркалу id из связи, если зеркало
   пусто, и **не зовёт каталог** (второй вызов завёл бы второй workspace под
   тот же ``external_user_id``). Это чинит строки, сломанные ДО правки, — и
   узлы первого класса эту ветку **не трогают**: под ``atomic`` до неё не
   доходит. Её стережёт четвёртый узел ниже. В тестах #1799 этой ветки нет:
   ``TestHealRepairsWhatTheMirrorLost`` проверяет heal новой операции, а не
   ранний возврат старой.

### Чем это НЕ дублирует ``TestTheOperatorRetriesTheCatalogWorkspace``

Тот класс проверяет повтор **после ОТКАЗА**: ``catalog_provisioning_refusal``
выставлен, ``catalog_provisioned_at is None``, ранний возврат не срабатывает.
Здесь — повтор **после ЧАСТИЧНОГО УСПЕХА**: ``catalog_provisioned_at`` стоит.
Предметы противоположные: там страж пропускает, тут запирал.

### Чего эти узлы не утверждают

* не проверяют ``dto.created`` — провижининг возвращает «причина или ``None``»
  и 201 от 200 отличить не может;
* не требуют атомарности вокруг **отказа** — причина пишется на связь
  намеренно (``:145-146``), откат стёр бы ровно её (третий класс —
  положительный контроль этой границы, зелёный в обеих точках);
* не проверяют дубли — их держит схема (два партиальных ``UniqueConstraint``),
  это предмет E2E-04;
* не проверяют ``ensure_catalog_specialist_identity`` — у неё свои тесты в
  ``apps/catalog/tests/``, и на ``e8f1570e`` её не зовёт ни один нетестовый
  модуль.

### Ожидания объявлены заранее и сняты по junit на независимом стенде

``dev 13656b6d`` (без #1799): failures 2, errors 0 — красные оба узла первого
класса, зелёный третий. Четвёртый узел там тоже красный: ранний возврат
уходил с ``None``, не тронув зеркала.
``dev e8f1570e`` (с #1799): failures 0, errors 0 — 4/4.
Дельта между точками — ровно #1799 (2 коммита, 3 файла).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.utils import timezone

from apps.identity.services import solo_catalog_provisioning
from apps.identity.services import solo_identity_link as link_svc
from apps.identity.services.solo_onboarding import create_solo_provider

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "solo-e2e06"
SPECIALIST_ID = uuid.UUID("5a1c0000-0000-4000-8000-0000000e2e06")


class _FakeCatalog:
    """Тот же двойник, что в ``test_solo_catalog_provisioning_1830.py``."""

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome

    def __enter__(self) -> "_FakeCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_solo_workspace(self, **kwargs: Any):
        from apps.catalog.services.http_client import ProvisionedSoloWorkspaceDTO

        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return ProvisionedSoloWorkspaceDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            slug=kwargs["slug"],
            specialist_id=SPECIALIST_ID,
            user_id=uuid.UUID("5a1c0000-0000-4000-8000-000000009999"),
            status="draft",
            created=True,
        )


@pytest.fixture
def catalog(monkeypatch) -> _FakeCatalog:
    fake = _FakeCatalog()
    monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)
    return fake


@pytest.fixture
def solo():
    """Соло-кабинет с открытой связью — как его строит 1830."""
    result = create_solo_provider(
        channel="max", channel_user_id=CHANNEL_USER_ID, display_name="Ольга", city="Пенза"
    )
    link = link_svc.open_link(result.master, bot_user=result.bot_user, tenant=result.tenant)
    return result, link


def _provision(result, link):
    return solo_catalog_provisioning.provision_catalog_workspace(
        link, tenant=result.tenant, bot_user=result.bot_user, display_name="Ольга"
    )


def _crash_on_mirror(monkeypatch, link) -> None:
    """Обрыв РОВНО между двумя записями — и ровно ОДИН раз.

    ``monkeypatch.undo()`` здесь нельзя: он снял бы и подмену
    ``CatalogHttpClient`` из фикстуры ``catalog``, и повтор ушёл бы в сеть —
    узел падал бы по среде, а не по предмету. Тот же довод записан в
    ``apps/channels/tests/test_marketplace_menu_drf1491.py``.

    Поэтому подмена снимает себя сама после первого срабатывания: обрыв
    случается один раз, дальше ``save`` работает как обычно.
    """
    original = type(link.master).save
    fired = False

    def _boom(self, *a: Any, **kw: Any):
        nonlocal fired
        if not fired and self.pk == link.master_id:
            fired = True
            raise RuntimeError("процесс умер между двумя save()")
        return original(self, *a, **kw)

    monkeypatch.setattr(type(link.master), "save", _boom)


class TestPartialSuccessDoesNotSurviveACrash:
    def test_the_mirror_and_the_link_never_disagree(self, catalog, monkeypatch, solo) -> None:
        """Обрыв после записи №1 не оставляет связь с id при пустом зеркале."""
        result, link = solo
        _crash_on_mirror(monkeypatch, link)

        with pytest.raises(RuntimeError):
            _provision(result, link)

        link.refresh_from_db()
        link.master.refresh_from_db()

        assert (link.catalog_specialist_id is None) == (
            link.master.catalog_specialist_id is None
        ), (
            "частичный успех: связь и зеркало разошлись — "
            f"link={link.catalog_specialist_id!r} master={link.master.catalog_specialist_id!r}"
        )
        assert link.catalog_provisioned_at is None, (
            "catalog_provisioned_at выставлен при незаписанном зеркале: "
            "повтор уйдёт в ранний возврат и расхождение запрётся"
        )

    def test_a_repeat_after_a_crash_finishes_the_job(self, catalog, monkeypatch, solo) -> None:
        """Повтор после обрыва доводит до конца: зеркало заполнено, отказа нет."""
        result, link = solo
        _crash_on_mirror(monkeypatch, link)

        with pytest.raises(RuntimeError):
            _provision(result, link)

        # Подмена сняла себя сама после единственного срабатывания —
        # ``undo()`` здесь нельзя, он убрал бы и фикстуру ``catalog``.
        link.refresh_from_db()

        refusal = _provision(result, link)

        link.refresh_from_db()
        link.master.refresh_from_db()

        assert refusal is None, f"повтор отказал: {refusal!r}"
        assert link.master.catalog_specialist_id is not None, (
            "повтор не довёл операцию до конца: зеркало пусто, а ранний возврат "
            "по catalog_provisioned_at считает работу сделанной"
        )
        assert link.master.catalog_specialist_id == link.catalog_specialist_id


class TestARowBrokenBeforeTheFixIsHealedOnRepeat:
    """Ветка ``:64-86``: строка в состоянии, которое оставлял обрыв ДО правки.

    Транзакция такую строку больше не создаст — но уже созданные живут в базе,
    и повтор обязан их починить **без похода в каталог**. Узлы первого класса
    до этой ветки не доходят (``atomic`` откатывает запись №1), поэтому у неё
    нужен свой сторож. В тестах #1799 его нет.
    """

    def test_the_mirror_is_backfilled_without_asking_the_catalog(self, catalog, solo) -> None:
        result, link = solo
        assert link.master.catalog_specialist_id is None, "предпосылка: зеркало пусто"

        link.catalog_specialist_id = SPECIALIST_ID
        link.catalog_provisioned_at = timezone.now()
        link.save(update_fields=["catalog_specialist_id", "catalog_provisioned_at"])

        refusal = _provision(result, link)

        link.master.refresh_from_db()
        assert refusal is None, f"повтор на уже подготовленной связи отказал: {refusal!r}"
        assert link.master.catalog_specialist_id == SPECIALIST_ID, (
            "ранний возврат ушёл с «сделано», не досыпав зеркалу id из связи"
        )
        assert catalog.calls == [], (
            "каталог вызван при уже выданном id — второй workspace под тот же external_user_id"
        )


class TestTheRefusalPathIsDeliberatelyNotAtomic:
    """Положительный контроль ГРАНИЦЫ требования — обязан быть ЗЕЛЁНЫМ всегда.

    Без него первый класс читался бы как «обернуть весь модуль транзакцией»,
    а это стёрло бы машинную причину, ради которой оператор в связь и смотрит.
    """

    def test_transport_error_leaves_its_reason_written(self, catalog, solo) -> None:
        from apps.catalog.services.http_client import CatalogTransportError

        result, link = solo
        catalog.outcome = CatalogTransportError("сеть легла")

        refusal = _provision(result, link)

        link.refresh_from_db()
        assert refusal == "transport_error"
        assert link.catalog_provisioning_refusal == "transport_error", (
            "причина отказа не сохранилась — откат стёр запись, "
            "ради которой оператор смотрит в связь"
        )
        assert link.catalog_provisioned_at is None
