"""Закрытый реестр body-care ``reason_codes`` — контракт §21 (BOT-9 DRF-2790, BOT-9b DRF-2800).

Источник — ``AYLA_BODY_CARE_RUNTIME_CONTRACT_v0.2`` §21 (рабочая версия,
подтверждена владельцем 06.10), двадцать шесть кодов, дословно: двадцать v0.1
и шесть юридических и лицензионных, добавленных в v0.2. Устроен как
каталожный ``recommendation/_reason_codes.py``:

* **закрытый** — наружу уходят члены перечисления и ничего больше; решения
  и аналитика ключуются по кодам, никогда по текстам;
* **версионируемый** — добавление, удаление или смена смысла кода меняет
  :data:`REGISTRY_VERSION`. Версия уезжает в запись решения (контракт §20:
  ``reason_codes`` + ``runtime_version``), иначе решение невоспроизводимо
  задним числом;
* **значение равно имени** — коды видны в логах и в grep.

Здесь только словарь. Что код значит для решения (стоп, уточнить, медицинская
проверка), какие пороги его включают и кто его выставляет — скрининг, роутинг
и LIM, и они ждут клинику (D-2, D-4): §18 запрещает рантайму выдумывать
пороги. Группировка ниже — порядок чтения, не правило.

Шесть юридических и лицензионных кодов (``LEGAL_CLASSIFICATION_REQUIRED`` …
``MEDICAL_AD_CLAIM_REVIEW_REQUIRED``) — тоже только словарь. Кто и когда их
выставляет (легальная классификация услуги, проверка лицензии и
квалификации, ревью рекламных утверждений), решает §7A v0.2 вместе с юристом
(D-1) и клиникой (D-2); здесь этого нет.

Пространство имён не пересекается с реестром трека A бота
(``apps.orchestrator.decision_readiness.reason_codes``) и с префиксами
каталожного реестра рекомендаций — проверяется тестом.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

#: Версия реестра — по версии контракта, из которой взяты коды. 0.1.0 — двадцать
#: кодов v0.1 (DRF-2790); 0.2.0 — плюс шесть юридических и лицензионных v0.2.
REGISTRY_VERSION: Final = "0.2.0"


class ReasonCode(StrEnum):
    """Двадцать шесть кодов §21 v0.2. Значение равно имени."""

    # -- состояние кожи сейчас ------------------------------------------------
    OPEN_WOUND = "OPEN_WOUND"
    SUNBURN = "SUNBURN"
    ACTIVE_RASH = "ACTIVE_RASH"
    ACTIVE_IRRITATION = "ACTIVE_IRRITATION"

    # -- реакции в прошлом и аллергия -----------------------------------------
    PRODUCT_REACTION_HISTORY = "PRODUCT_REACTION_HISTORY"
    KNOWN_ALLERGY = "KNOWN_ALLERGY"
    RESPIRATORY_REACTION_HISTORY = "RESPIRATORY_REACTION_HISTORY"
    CURRENT_RESPIRATORY_EMERGENCY = "CURRENT_RESPIRATORY_EMERGENCY"

    # -- протокол требует проверки --------------------------------------------
    MEDICATION_PROTOCOL_REVIEW = "MEDICATION_PROTOCOL_REVIEW"
    PREVIOUS_PROCEDURE_RESTRICTION = "PREVIOUS_PROCEDURE_RESTRICTION"
    PREGNANCY_PROTOCOL_REVIEW = "PREGNANCY_PROTOCOL_REVIEW"
    LACTATION_PROTOCOL_REVIEW = "LACTATION_PROTOCOL_REVIEW"

    # -- конфигурация и источники ---------------------------------------------
    CONFIGURATION_INCOMPLETE = "CONFIGURATION_INCOMPLETE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    SPA_TRANSITION_UNAPPROVED = "SPA_TRANSITION_UNAPPROVED"
    AC_CLASS_UNKNOWN = "AC_CLASS_UNKNOWN"
    AC_NOT_IN_INITIAL_CANON = "AC_NOT_IN_INITIAL_CANON"

    # -- нежелательная реакция и эскалация ------------------------------------
    ADVERSE_REACTION_R1 = "ADVERSE_REACTION_R1"
    ADVERSE_REACTION_R2 = "ADVERSE_REACTION_R2"
    S1_ESCALATION = "S1_ESCALATION"

    # -- юридическая классификация и лицензии (v0.2, §7A) ---------------------
    LEGAL_CLASSIFICATION_REQUIRED = "LEGAL_CLASSIFICATION_REQUIRED"
    MEDICAL_LICENSE_NOT_VERIFIED = "MEDICAL_LICENSE_NOT_VERIFIED"
    LICENSE_SCOPE_MISMATCH = "LICENSE_SCOPE_MISMATCH"
    LICENSE_ADDRESS_MISMATCH = "LICENSE_ADDRESS_MISMATCH"
    PRACTITIONER_QUALIFICATION_NOT_VERIFIED = "PRACTITIONER_QUALIFICATION_NOT_VERIFIED"
    MEDICAL_AD_CLAIM_REVIEW_REQUIRED = "MEDICAL_AD_CLAIM_REVIEW_REQUIRED"


#: Все коды реестра — чтобы тест мог сказать «реестр закрыт».
ALL_CODES: Final[frozenset[str]] = frozenset(code.value for code in ReasonCode)


__all__ = ["ALL_CODES", "REGISTRY_VERSION", "ReasonCode"]
