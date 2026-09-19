"""Ворота ФОТО еды — один предикат на всех, кто зовёт распознаватель (DRF-2109).

``FOOD_PHOTO_SCAN_ENABLED`` — отдельный от ``NUTRITION_ENABLED`` выключатель:
фотография уходит за границу (OpenAI Vision в каталоге), и до решения
владельца о cross-border флаг по умолчанию выключен (``config/settings/
base.py:943-978``). До этого листа флаг спрашивал только чат
(``skills/food_scanner/skill.py::_check_gates``); прокси Mini App
``customer_food_scan`` (DRF-2098) стоял за тремя воротами ДНЕВНИКА
(:mod:`apps.consent.diary_gate`) и флага фото не видел — при выключенном
флаге фото из чата отказывало, из Mini App уходило в распознаватель.

Здесь — единственный предикат :func:`photo_scan_refusal`, и класс, на который
он обязан стоять, — **вызовы ``scan_photo`` клиента питания**: перепись по AST
в ``apps/consent/tests/test_photo_gate_2109.py`` находит вызывающих и
требует, чтобы каждый их модуль спрашивал этот предикат. Ворота дневника
(:mod:`apps.consent.diary_gate`) остаются рядом и отдельно: там — согласие и
флаг питания, здесь — только cross-border фото. Порядок в вызывающих:
питание → фото → согласия (как в чате до этого листа: выключенное фото
отвечает раньше, чем спрашивается согласие).
"""

from __future__ import annotations

PHOTO_SCAN_DISABLED = "photo_scan_disabled"


def photo_scan_refusal() -> str | None:
    """Почему фотографию сейчас нельзя отдать распознавателю — или ``None``."""
    from django.conf import settings

    if not getattr(settings, "FOOD_PHOTO_SCAN_ENABLED", False):
        return PHOTO_SCAN_DISABLED
    return None


__all__ = ["PHOTO_SCAN_DISABLED", "photo_scan_refusal"]
