"""Routers handler'ов MAX-бота.

main.py импортит get_routers() и регистрирует через dp.include_routers(*).
Каждый сценарий — свой Router в отдельном файле.
"""
from __future__ import annotations

from .ai_assistant import router as ai_assistant_router
from .ai_callbacks import router as ai_callbacks_router
from .booking import router as booking_router
from .contacts import router as contacts_router
from .cross_domain import router as cross_domain_router
from .fallback import router as fallback_router
from .faq import router as faq_router
from .food_clarify import router as food_clarify_router
from .food_correction import router as food_correction_router
from .food_scanner import router as food_scanner_router
from .health_screening import router as health_screening_router
from .nutrition_anketa import router as nutrition_anketa_router
from .nutrition_entry import router as nutrition_entry_router
from .reminders import router as reminders_router
from .services import router as services_router
from .start import router as start_router
from .water import router as water_router
from maxbot.nudges.mute_handlers import router as nudge_mute_router


def get_routers():
    """Возвращает список всех Router'ов в порядке регистрации.

    Порядок важен для фильтров — более специфичные handler'ы должны быть
    зарегистрированы раньше общих fallback'ов.

    Структура (T-06c):
    - start/services/booking/contacts/faq — кнопочные сценарии (specific
      callbacks или state-фильтры BookingStates.X для FSM-вводов)
    - ai_assistant — БЕЗ state-фильтра, ловит всё остальное (free-text,
      AskStates.awaiting_question)
    - fallback — резервный для edge-case'ов (системные сообщения без sender)
    """
    return [
        start_router,
        services_router,
        booking_router,
        contacts_router,
        faq_router,
        # reminders ПЕРЕД ai_callbacks/ai_assistant — cb:rem:* должны матчиться
        # раньше generic message_created (N2).
        reminders_router,
        # ai_callbacks ПЕРЕД ai_assistant — специфичные cb:ai:* callbacks
        # должны матчиться раньше общего message_created handler'а.
        ai_callbacks_router,
        # nutrition_entry ПЕРЕД food_scanner — специфичные cb:menu:nutrition
        # и cb:nutrition:try_now/start_anketa должны матчиться раньше широких
        # food_scanner callback-фильтров (Phase 3 T01).
        nutrition_entry_router,
        # nutrition_anketa ПОСЛЕ nutrition_entry, ПЕРЕД food_scanner и ai_assistant —
        # FSM обрабатывает cb:anketa:* callbacks и state-filtered free-text для
        # ввода age/height/weight (Phase 3.1 Part 1).
        nutrition_anketa_router,
        # health_screening — TIER-B consent + screens (Phase 3.2A T03+).
        # Регистрируется после nutrition_anketa, перед food_scanner — cb:tier_b:*
        # callbacks должны матчиться раньше generic message_created.
        health_screening_router,
        # nudge_mute — cb:nudge:mute:off:* / cb:nudge:mute:less:* callbacks
        # (Phase 3.2B T09). Регистрируется ПЕРЕД food_scanner/ai_assistant
        # чтобы перехватить mute-кнопки до generic message_created.
        nudge_mute_router,
        # food_clarify — DRF-358 friendly food/drink fallback card callbacks
        # (cb:ux:food_to_diary / cb:ux:food_typo). Регистрируется ПЕРЕД
        # food_scanner чтобы перехватить специфичные cb:ux:food_* раньше
        # widе food_scanner-фильтров.
        food_clarify_router,
        # food_scanner ПЕРЕД ai_assistant — оба ловят message_created, но
        # food_scanner early-return'ит если у сообщения нет фото-вложения,
        # передавая управление дальше. cb:nutrition:consent:* / cb:nutrition:log:*
        # тоже здесь.
        food_scanner_router,
        # water — обработка [💧 Добавить воду] footer + /вода command
        # + cb:water:add/undo/more callbacks (Part 2B). Регистрируется
        # ДО food_correction чтобы перехватить PAYLOAD_NUTRITION_ADD_WATER
        # вместо stub'а.
        water_router,
        # food_correction — обработка cb:scan:correct:* callbacks из
        # render_food_scan_v2 [✏️ Поправить] (Part 2A T07-T11).
        food_correction_router,
        # cross_domain ПОСЛЕ food_scanner (его hook вызывает render отсюда)
        # и ПЕРЕД ai_assistant — cb:cross:* callbacks должны матчиться
        # раньше generic message_created (DRF-274).
        cross_domain_router,
        ai_assistant_router,
        fallback_router,
    ]
