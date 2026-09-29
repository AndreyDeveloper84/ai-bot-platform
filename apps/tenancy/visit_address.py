"""Адрес салона для человека — двойник ``apps/miniapp/src/lib/visit-address.ts`` (DRF-1952).

Бот (подтверждение записи, напоминания T-24/T-2, уведомление клиенту) и Mini App
говорят про адрес одно и то же. Источник один — зеркало ``Tenant.address``
(его пишет синк каталога из ``tenant_address``, DRF-1587). Правило трёх
состояний — то же, что в TS, и тест паритета сверяет фразы с тем файлом:

* ``None`` — источник промолчал → «Уточните адрес в салоне» (исправимо: человеку
  есть у кого спросить);
* ``""`` — салон ответил, что адреса нет → «Адрес не указан»;
* иначе — адрес как есть.

Выдуманного адреса и прочерка «—» не бывает.
"""

from __future__ import annotations

from typing import Any

#: Салон ответил, что адреса нет.
ADDRESS_SAID_NONE = "Адрес не указан"

#: Источник промолчал — человеку есть у кого спросить.
ADDRESS_UNKNOWN = "Уточните адрес в салоне"


def visit_address_text(address: str | None) -> str:
    """Фраза для показа человеку — ровно по правилу ``visit-address.ts``."""
    if address is None:
        return ADDRESS_UNKNOWN
    if address == "":
        return ADDRESS_SAID_NONE
    return address


def tenant_address_line(tenant: Any) -> str:
    """Строка адреса для текста человеку: «Адрес: …» или «Адрес не указан».

    Салон ответил, что адреса нет (``""``), — строка сама говорит это, без
    «Адрес: » перед ней: «Адрес: Адрес не указан» было бы тавтологией.
    """
    if getattr(tenant, "address", None) == "":
        return ADDRESS_SAID_NONE
    return f"Адрес: {tenant_address_text(tenant)}"


def tenant_address_text(tenant: Any) -> str:
    """Фраза адреса салона ``tenant`` — тенанта ЗАПИСИ, не контекста разговора."""
    return visit_address_text(getattr(tenant, "address", None))
