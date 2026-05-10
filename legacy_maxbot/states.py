"""FSM-состояния для booking-флоу.

Используется встроенный maxapi.context.MemoryContext (per-process in-memory).
См. docs/plans/maxbot-phase1-research.md §5.
"""
from maxapi.context.state_machine import State, StatesGroup


class BookingStates(StatesGroup):
    awaiting_name = State()
    awaiting_phone = State()
    awaiting_confirm = State()


class AskStates(StatesGroup):
    """FSM для свободного диалога с AI-помощником (T-06c).

    awaiting_question — клиент кликнул «❓ Задать вопрос», ждём текст вопроса.
    После получения ответа state очищается (one-shot).
    """
    awaiting_question = State()


class NutritionAnketaStates(StatesGroup):
    """FSM TIER-A анкеты дневника питания (Phase 3.1 Part 1).

    Запускается из callback PAYLOAD_NUTRITION_START_ANKETA (entry handler).
    После complete_tier_a → BotUser.nutrition_onboarded_at установлен,
    Ayla профиль создан. TIER-B (health screening) — отдельный sub-flow,
    НЕ в этом FSM (запускается lazy перед первым advice-моментом из
    ai_assistant, см. Design Doc v2 §4.5).

    Все шаги шлют PATCH в Ayla `POST /profile/` с complete=false; последний
    (complete_tier_a) — с complete=true.
    """
    awaiting_consent = State()       # дисклеймер 152-ФЗ обработки ПД для анкеты
    awaiting_gender = State()
    awaiting_age = State()           # text-input или Skip-кнопка
    awaiting_height = State()        # text-input или Skip
    awaiting_weight = State()        # text-input или Skip
    awaiting_goal = State()          # 3 кнопки lose/maintain/gain
    awaiting_pace = State()          # для goal=lose: gentle/moderate
    awaiting_gain_clarify = State()  # для goal=gain: gain/tone
    awaiting_bmi_ladder = State()    # если BMI<18.5 + goal=lose
    complete = State()               # рендер финального экрана с нормой

    # ─── TIER-B (Phase 3.2A, lazy on-demand) ─────────────────────────
    awaiting_health_consent = State()
    awaiting_pregnancy = State()
    awaiting_breastfeeding = State()
    awaiting_diabetes = State()
    awaiting_chronic = State()           # multi-select toggle
    awaiting_allergies = State()         # 3-option choice
    awaiting_allergies_text = State()    # free-text
    awaiting_meds = State()
    awaiting_menopause = State()         # conditional: ж 45+ only
