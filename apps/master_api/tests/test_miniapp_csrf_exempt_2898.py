"""DRF-2898 — изменяющие ручки Mini App не стоят за проверкой CSRF.

Живой дефект владельца на пилоте (08.10): «Сохранить» на фото мастера —
и ничего. ``PATCH /api/v1/master/profile`` отвечал 403: сначала «Origin
checking failed», а после добавления источника в доверенные —
«CSRF cookie not set».

Mini App авторизуется заголовком initData. Кук он не носит и CSRF-токена не
шлёт, так что проверка CSRF для него не защита, а закрытая дверь. Поэтому
изменяющим ручкам Mini App ставят ``@csrf_exempt`` — у 88 из 93 оно стояло, у
пяти ручек мастера его забыли: «О себе» и фото, загрузка и удаление работы в
портфолио, оплата долга, привязка карты.

Узлы этого не ловили: тестовый клиент Django CSRF по умолчанию не проверяет.
Здесь проверка включена (``enforce_csrf_checks=True``).

* e1 — каждая из пяти ручек с чужого источника и без токена доходит до
  своей авторизации (401), а не падает на CSRF (403);
* e2 — положительный контроль того же клиента: ручка вне Mini App без
  исключения на том же запросе получает именно отказ CSRF;
* c1 — перепись: каждая изменяющая ручка трёх API Mini App несёт
  ``@csrf_exempt``;
* c2 — ручка, не назвавшая свои методы, принимает любые — значит, тоже
  обязана нести исключение;
* c3 — перепись не пуста и видит известные ручки.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

APPS = Path(__file__).resolve().parents[2]
MINIAPP_APIS = ("master_api", "miniapp_api", "admin_api")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
#: Источник, с которого отдаётся Mini App: не тот, что у API.
MINIAPP_ORIGIN = "https://miniapp.example"

FIVE = [
    ("PATCH", "master_api:profile", {}),
    ("POST", "master_api:profile_portfolio", {}),
    (
        "DELETE",
        "master_api:profile_portfolio_item",
        {"item_id": "11111111-1111-4111-8111-111111111111"},
    ),
    ("POST", "master_api:billing_pay_debt", {}),
    ("POST", "master_api:billing_card_setup", {}),
]


def _send(method: str, url: str):
    client = Client(enforce_csrf_checks=True)
    return client.generic(
        method,
        url,
        data=b"{}",
        content_type="application/json",
        secure=True,
        HTTP_ORIGIN=MINIAPP_ORIGIN,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(("method", "name", "kwargs"), FIVE, ids=[name for _, name, _ in FIVE])
def test_e1_the_request_reaches_the_views_own_auth(method: str, name: str, kwargs: dict) -> None:
    answer = _send(method, reverse(name, kwargs=kwargs))

    # Без initData ручка отказывает сама — 401. CSRF ответил бы 403 раньше неё.
    assert answer.status_code == 401, answer.content[:200]


@pytest.mark.django_db
def test_e2_positive_control_a_non_exempt_view_is_refused_by_csrf() -> None:
    """Тот же клиент и тот же запрос на ручку вне Mini App: проверка включена
    и срабатывает — значит, 401 выше добыт не выключенной проверкой."""
    answer = _send("POST", reverse("admin:login"))

    assert answer.status_code == 403
    assert b"CSRF" in answer.content


# --------------------------------------------------------------------- #
# Перепись
# --------------------------------------------------------------------- #


def _routed_view_names(app: str) -> set[str]:
    """Имена функций, названные в ``urls.py`` приложения как ``views*.<имя>``."""
    names: set[str] = set()
    tree = ast.parse((APPS / app / "urls.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id.startswith("views")
        ):
            names.add(node.attr)
    return names


def _declared_methods(func: ast.FunctionDef) -> set[str] | None:
    """Методы из ``require_http_methods`` / ``require_POST``; ``None`` — не названы."""
    methods: set[str] | None = None
    for decorator in func.decorator_list:
        text = ast.dump(decorator)
        if "require_POST" in text:
            methods = (methods or set()) | {"POST"}
        elif "require_GET" in text or "require_safe" in text:
            methods = (methods or set()) | {"GET"}
        elif isinstance(decorator, ast.Call) and "require_http_methods" in ast.dump(decorator.func):
            found = {
                node.value
                for node in ast.walk(decorator)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            methods = (methods or set()) | found
    return methods


def _census() -> tuple[list[str], list[str], int]:
    """(изменяющие без исключения, безымянные по методам без исключения, всего изменяющих)."""
    unsafe_unexempt: list[str] = []
    bare_unexempt: list[str] = []
    unsafe_total = 0
    for app in MINIAPP_APIS:
        routed = _routed_view_names(app)
        for path in sorted((APPS / app).glob("*.py")):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.FunctionDef) or node.name not in routed:
                    continue
                exempt = any("csrf_exempt" in ast.dump(d) for d in node.decorator_list)
                methods = _declared_methods(node)
                where = f"apps/{app}/{path.name}:{node.lineno} {node.name}"
                if methods is None:
                    if not exempt:
                        bare_unexempt.append(where)
                elif methods & UNSAFE:
                    unsafe_total += 1
                    if not exempt:
                        unsafe_unexempt.append(f"{where} {sorted(methods & UNSAFE)}")
    return unsafe_unexempt, bare_unexempt, unsafe_total


def test_c1_every_state_changing_miniapp_view_is_csrf_exempt() -> None:
    unsafe_unexempt, _, _ = _census()

    assert unsafe_unexempt == [], (
        "Изменяющая ручка Mini App без @csrf_exempt: Mini App кук и токена не шлёт, "
        "и запрос получит 403 раньше, чем дойдёт до ручки (DRF-2898)."
    )


def test_c2_a_view_that_names_no_methods_is_exempt_too() -> None:
    _, bare_unexempt, _ = _census()

    assert bare_unexempt == []


def test_c3_the_census_is_not_empty_and_sees_the_known_views() -> None:
    _, _, unsafe_total = _census()
    routed = _routed_view_names("master_api")

    # На dev e9d466f5 изменяющих ручек 93; порог ниже, чтобы узел не краснел от удаления одной.
    assert unsafe_total >= 80
    assert {"onboarding_profile", "billing_pay_debt", "billing_card_setup"} <= routed
