"""Долг тупиков на 21.09 (DRF-2267): ``файл::функция`` → число ответов без кнопок.

Снят сторожем ``test_no_dead_ends_guard_2267`` по коду после первого среза
(топ по пути нового клиента). Красный в обе стороны: новый ответ без кнопок
— красный; ответ получил кнопки — число здесь уменьшить (иначе тоже красный).
Уходит сценарий за сценарием: дневник и вода → ошибки → проактив → салон.
"""

DEBT: dict[str, int] = {
    "apps/bookings/callbacks.py::_dispatch_cancel": 3,
    "apps/bookings/callbacks.py::_dispatch_confirm": 6,
    "apps/bookings/callbacks.py::_dispatch_reschedule": 5,
    "apps/bookings/callbacks.py::_handle_cancel": 1,
    "apps/bookings/callbacks.py::_handle_cancel_tap": 5,
    "apps/bookings/callbacks.py::_handle_confirm": 1,
    "apps/bookings/callbacks.py::_handle_confirm_tap": 6,
    "apps/bookings/callbacks.py::_handle_reschedule": 1,
    "apps/bookings/callbacks.py::handle": 6,
    "apps/channels/max/global_onboarding.py::run_onboarding_turn": 1,
    "apps/channels/max/handler.py::_handle_global_max_event_inner": 11,
    "apps/orchestrator/concierge.py::_concierge_turn._reply": 1,
    "apps/orchestrator/concierge.py::_execute_start_booking": 1,
    "apps/orchestrator/concierge.py::generate_concierge_reply": 1,
    "apps/orchestrator/discovery.py::_render_ask_clarification": 1,
    "apps/orchestrator/discovery.py::execute_catalog_callback": 1,
    "apps/orchestrator/discovery.py::execute_clarify_callback": 3,
    "apps/orchestrator/discovery.py::execute_show_more": 2,
    "apps/orchestrator/discovery.py::generate_discovery_reply": 3,
    "apps/orchestrator/discovery.py::render_multiselect_clarification": 1,
    "apps/orchestrator/memory_ask.py::try_handle_answer": 3,
    "apps/orchestrator/nutrition_global.py::execute_nutrition_tool": 1,
    "apps/orchestrator/personal_surface.py::_reply": 1,
    "apps/orchestrator/visits.py::route_visit_cancel_ask": 1,
    "apps/orchestrator/visits.py::route_visit_cancel_do": 2,
    "apps/orchestrator/visits.py::route_visit_card": 1,
    "apps/orchestrator/visits.py::route_visit_move": 1,
    "apps/orchestrator/visits.py::route_visits": 1,
    "apps/recommendation/taps.py::_pick_alternative": 2,
    "apps/recommendation/taps.py::route_recommendation_callback": 4,
    "apps/skills/cross_domain/skill.py::_on_convert": 1,
    "apps/skills/cross_domain/skill.py::_on_dismiss": 2,
    "apps/skills/cross_domain/skill.py::_on_seen": 1,
    "apps/skills/cross_domain/skill.py::handle": 2,
    "apps/skills/echo/skill.py::handle": 4,
    "apps/skills/faq/skill.py::_build_skill_result": 1,
    "apps/skills/food_clarify/skill.py::handle": 2,
    "apps/skills/food_clarify/text_entry.py::_log": 3,
    "apps/skills/food_clarify/text_entry.py::_on_fix_grams_answer": 1,
    "apps/skills/food_clarify/text_entry.py::_on_grams_answer": 1,
    "apps/skills/food_clarify/text_entry.py::diary_entry_refusal": 1,
    "apps/skills/food_clarify/text_entry.py::on_callback": 3,
    "apps/skills/food_clarify/text_entry.py::on_diary_tap": 1,
    "apps/skills/food_clarify/text_entry.py::on_entry_callback": 3,
    "apps/skills/food_clarify/text_entry.py::on_text": 1,
    "apps/skills/food_clarify/text_entry.py::show_estimate": 3,
    "apps/skills/food_correction/skill.py::_handle_answer": 7,
    "apps/skills/food_correction/skill.py::_handle_keep_or_change": 2,
    "apps/skills/food_correction/skill.py::_handle_prompt": 5,
    "apps/skills/food_correction/skill.py::handle": 1,
    "apps/skills/food_scanner/skill.py::_check_gates": 2,
    "apps/skills/food_scanner/skill.py::_handle_callback": 6,
    "apps/skills/nutrition_anketa/skill.py::_manual_stale": 1,
    "apps/skills/nutrition_anketa/skill.py::_on_complete": 2,
    "apps/skills/nutrition_anketa/skill.py::_on_confirm_targets": 2,
    "apps/skills/nutrition_anketa/skill.py::_on_consent_declined": 1,
    "apps/skills/nutrition_anketa/skill.py::_on_consent_granted": 1,
    "apps/skills/nutrition_anketa/skill.py::_on_edit": 2,
    "apps/skills/nutrition_anketa/skill.py::_on_withdraw_ask": 1,
    "apps/skills/nutrition_anketa/skill.py::_on_withdraw_confirm": 2,
    "apps/skills/nutrition_anketa/skill.py::_on_withdraw_keep": 1,
    "apps/skills/nutrition_anketa/skill.py::_route_update_weight": 1,
    "apps/skills/nutrition_anketa/skill.py::_update_weight_over_manual": 4,
    "apps/skills/nutrition_anketa/skill.py::_update_weight_with": 7,
    "apps/skills/nutrition_anketa/skill.py::handle": 1,
    "apps/skills/payment_failed/skill.py::_build_reply": 1,
    "apps/skills/privacy_consent/skill.py::handle": 3,
    "apps/skills/registry.py::dispatch": 1,
    "apps/skills/water/skill.py::handle": 1,
    "apps/skills/welcome/skill.py::handle": 3,
}

#: Без кнопок по правилу (§72) — с причиной. Любое число ответов в этих функциях.
EXCEPTIONS: dict[str, str] = {
    "apps/channels/max/handler.py::_discovery_handoff_reply": (
        "B24/F5 — ответ «переключаю на оператора»; бот молчит, пока задача открыта"
    ),
    "apps/orchestrator/discovery.py::render_no_salons": (
        "B13 — «Подключённых салонов пока нет»: кнопка нарисовала бы это же сообщение (петля), §72 оставляет без кнопок"
    ),
    "apps/orchestrator/handoff.py::route_global_human_handoff": (
        "B24/F5 — передача оператору на глобальном пути; бот молчит, пока задача открыта"
    ),
    "apps/skills/booking/skill.py::_handoff": (
        "B24 — запись сорвалась → передача менеджеру; бот молчит, пока задача открыта"
    ),
    "apps/skills/faq/skill.py::_handoff": (
        "B24/F5 — передача оператору из FAQ; бот молчит, пока задача открыта"
    ),
    "apps/skills/health_screening/skill.py::handle": (
        "F4 — медицинские ответы (G4, красные флаги с 103/112, «где болит»): кнопок записи нет по решению §72"
    ),
    "apps/skills/health_screening/skill.py::_g7_turn": (
        "F4 — медицинские ответы G7 (#1982): красный флаг, ограничение S1, вопросы G4/G7; "
        "кнопок записи нет по решению §72"
    ),
    "apps/skills/human_handoff/skill.py::handle": (
        "B24/F5 — передача оператору: пока задача открыта, бот молчит, любая кнопка упадёт в молчание"
    ),
}
