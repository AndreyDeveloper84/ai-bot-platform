"""Фабрики InlineKeyboard для MAX-бота.

Callback payload convention (см. tests/maxbot/test_keyboards.py docstring):
- cb:menu:{section}  — переход из главного меню
- cb:svc:{id}        — выбор услуги (id из services_app.Service)
- cb:faq:{id}        — выбор FAQ (id из services_app.HelpArticle)
- cb:back            — возврат в главное меню
- cb:confirm:{yes|no}— подтверждение/отмена заявки в FSM booking
"""
from __future__ import annotations

from typing import Iterable

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# Префиксы — единственное место где они объявлены, handlers импортят отсюда
PAYLOAD_MENU_BOOK = "cb:menu:book"
PAYLOAD_MENU_SERVICES = "cb:menu:services"
PAYLOAD_MENU_CONTACTS = "cb:menu:contacts"
PAYLOAD_MENU_FAQ = "cb:menu:faq"
PAYLOAD_BACK = "cb:back"
PAYLOAD_MENU_ASK = "cb:menu:ask"  # Кнопка «❓ Задать вопрос» (T-06c)
PAYLOAD_MENU_MY_BOOKINGS = "cb:menu:my_bookings"  # Кнопка «📋 Мои записи»
PAYLOAD_MENU_NUTRITION = "cb:menu:nutrition"  # Кнопка «🍎 Дневник питания» (Phase 3 T01)
PAYLOAD_NUTRITION_TRY_NOW = "cb:nutrition:try_now"  # «📸 Попробовать сразу» (Phase 3)
PAYLOAD_NUTRITION_START_ANKETA = "cb:nutrition:start_anketa"  # «📝 Настроить под себя» (Phase 3)

# ─── Phase 3.1 Part 1: TIER-A anketa keyboards ─────────────────────────────

PAYLOAD_ANKETA_CONSENT_OK = "cb:anketa:consent:ok"
PAYLOAD_ANKETA_CONSENT_DECLINE = "cb:anketa:consent:decline"

PAYLOAD_ANKETA_GENDER_FEMALE = "cb:anketa:gender:female"
PAYLOAD_ANKETA_GENDER_MALE = "cb:anketa:gender:male"

PAYLOAD_ANKETA_SKIP = "cb:anketa:skip"

PAYLOAD_ANKETA_GOAL_LOSE = "cb:anketa:goal:lose"
PAYLOAD_ANKETA_GOAL_MAINTAIN = "cb:anketa:goal:maintain"
PAYLOAD_ANKETA_GOAL_GAIN = "cb:anketa:goal:gain"

PAYLOAD_ANKETA_PACE_GENTLE = "cb:anketa:pace:gentle"
PAYLOAD_ANKETA_PACE_MODERATE = "cb:anketa:pace:moderate"

PAYLOAD_ANKETA_GAIN_MASS = "cb:anketa:gain:mass"
PAYLOAD_ANKETA_GAIN_TONE = "cb:anketa:gain:tone"

PAYLOAD_ANKETA_BMI_DOCTOR = "cb:anketa:bmi:doctor"
PAYLOAD_ANKETA_BMI_SWITCH_MAINTAIN = "cb:anketa:bmi:switch_maintain"
PAYLOAD_ANKETA_BMI_OVERRIDE = "cb:anketa:bmi:override"

PAYLOAD_NUTRITION_FIRST_MEAL = "cb:nutrition:first_meal"

# ─── Phase 3.1 Part 2A: photo refactor + footer + correction ───────────────

PAYLOAD_NUTRITION_ADD_WATER = "cb:nutrition:water:add"
PAYLOAD_NUTRITION_VIEW_DAY = "cb:nutrition:view_day"

PAYLOAD_SCAN_CORRECT_MENU = "cb:scan:correct:menu"
PAYLOAD_SCAN_PORTION_SMALLER = "cb:scan:correct:portion:smaller"
PAYLOAD_SCAN_PORTION_NORMAL = "cb:scan:correct:portion:normal"
PAYLOAD_SCAN_PORTION_LARGER = "cb:scan:correct:portion:larger"
PAYLOAD_SCAN_PORTION_OPEN_MENU = "cb:scan:correct:portion:menu"
PAYLOAD_SCAN_OTHER_DISH = "cb:scan:correct:other_dish"
PAYLOAD_SCAN_ADD_INGREDIENT = "cb:scan:correct:add_ingredient"
PAYLOAD_SCAN_DELETE = "cb:scan:correct:delete"
PAYLOAD_SCAN_RETAKE = "cb:scan:retake"
PAYLOAD_SCAN_MANUAL_INPUT = "cb:scan:manual"

# ─── Phase 3.1 Part 2B: water flow ─────────────────────────────────────────

PAYLOAD_WATER_AMOUNT_200 = "cb:water:add:200"
PAYLOAD_WATER_AMOUNT_250 = "cb:water:add:250"
PAYLOAD_WATER_AMOUNT_500 = "cb:water:add:500"
PAYLOAD_WATER_AMOUNT_1000 = "cb:water:add:1000"

PAYLOAD_WATER_MORE = "cb:water:more"

PAYLOAD_WATER_EXTENDED_150 = "cb:water:add:150"
PAYLOAD_WATER_EXTENDED_300 = "cb:water:add:300"
PAYLOAD_WATER_EXTENDED_350 = "cb:water:add:350"
PAYLOAD_WATER_EXTENDED_750 = "cb:water:add:750"
PAYLOAD_WATER_EXTENDED_1500 = "cb:water:add:1500"

PAYLOAD_WATER_UNDO_PREFIX = "cb:water:undo:"  # + entry_id

# ─── Phase 3.1 Part 2C: daily report footer ────────────────────────────────

PAYLOAD_REPORT_WEEKLY = "cb:report:weekly"
PAYLOAD_REPORT_TIME_SETTINGS = "cb:report:time"

# ─── Phase 3.1 Part 2D.2: settings + water reminders ───────────────────────

PAYLOAD_REPORT_TIME_18 = "cb:report:time:18"
PAYLOAD_REPORT_TIME_21 = "cb:report:time:21"
PAYLOAD_REPORT_TIME_23 = "cb:report:time:23"
PAYLOAD_REPORT_TIME_OFF = "cb:report:time:off"

PAYLOAD_WATER_REMINDERS_FOOTER = "cb:water:reminders:open"  # вход из footer
PAYLOAD_WATER_REMINDERS_TOGGLE = "cb:water:reminders:toggle"  # tap в settings menu

PAYLOAD_WATER_DISMISS = "cb:water:dismiss"  # «Уже пью» в reminder card

PAYLOAD_CONFIRM_YES = "cb:confirm:yes"
PAYLOAD_CONFIRM_NO = "cb:confirm:no"
PAYLOAD_CONFIRM_OTHER = "cb:confirm:other"  # «Указать другие данные» — сбросить FSM

PAYLOAD_SVC_PREFIX = "cb:svc:"
PAYLOAD_FAQ_PREFIX = "cb:faq:"
PAYLOAD_CAT_PREFIX = "cb:cat:"

# N2 — напоминания (BookingReminder.id в payload)
PAYLOAD_REM_CONFIRM_PREFIX = "cb:rem:confirm:"
PAYLOAD_REM_RESCHEDULE_PREFIX = "cb:rem:reschedule:"
PAYLOAD_REM_CANCEL_PREFIX = "cb:rem:cancel:"

# MAX API лимит: 30 рядов в inline keyboard (см. dev.max.ru/docs-api).
# Резервируем 1 ряд под кнопку «Назад» → 29 кнопок-контента max.
MAX_KEYBOARD_ROWS = 29


def main_menu_keyboard(*, bot_user=None):
    """Главное меню — 6-7 кнопок в 4-5 рядов.

    Кнопка «🍎 Дневник питания» появляется через
    ``maxbot.segmentation.in_phase3_segment(bot_user)`` (DRF-287 + DRF-292).
    Decision tree:

      - ``NUTRITION_ENABLED=True``                      → button shown
      - ``bot_user.max_user_id ∈ PHASE3_INTERNAL_ACCOUNTS`` → shown
      - ``PHASE3_AB_ENABLED=True`` + segment A           → shown
      - всё остальное (включая ``bot_user=None``)        → hidden

    ``bot_user=None`` default keeps backward compatibility for callers
    that don't have a resolved user yet — fail-closed (button hidden).
    Inline keyboard через ``attachments=`` в каждом ответе — «floating
    menu» паттерн. Persistent reply-keyboard в MAX SDK не поддерживается.
    """
    from maxbot.segmentation import in_phase3_segment

    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📅 Записаться", payload=PAYLOAD_MENU_BOOK),
        CallbackButton(text="ℹ️ Услуги", payload=PAYLOAD_MENU_SERVICES),
    )
    builder.row(
        CallbackButton(text="📋 Мои записи", payload=PAYLOAD_MENU_MY_BOOKINGS),
    )
    if in_phase3_segment(bot_user):
        builder.row(
            CallbackButton(text="🍎 Дневник питания", payload=PAYLOAD_MENU_NUTRITION),
        )
        # B24: «💧 Вода» — quick-access peer кнопка под тем же gate.
        # Reuse existing PAYLOAD_NUTRITION_ADD_WATER (handlers/water.py уже
        # подписан). Без отдельного payload — клик ведёт в тот же flow что
        # и footer-кнопка после food_scanner.
        builder.row(
            CallbackButton(text="💧 Вода", payload=PAYLOAD_NUTRITION_ADD_WATER),
        )
    builder.row(
        CallbackButton(text="📞 Контакты", payload=PAYLOAD_MENU_CONTACTS),
        CallbackButton(text="❓ Вопросы", payload=PAYLOAD_MENU_FAQ),
    )
    builder.row(
        CallbackButton(text="💬 Задать вопрос", payload=PAYLOAD_MENU_ASK),
    )
    return builder.as_markup()


def nutrition_welcome_keyboard():
    """Welcome screen дневника питания — 2 кнопки.

    [📸 Попробовать сразу]   — degraded mode без анкеты (defaults норма)
    [📝 Настроить под себя]  — анкета FSM (T04)

    Phase 3 T01: обе кнопки сейчас ведут на заглушки «Скоро будет»,
    реальные flow в T03 (food scanner refactor) и T04 (анкета).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📸 Попробовать сразу", payload=PAYLOAD_NUTRITION_TRY_NOW),
    )
    builder.row(
        CallbackButton(
            text="📝 Настроить под себя (30 сек)",
            payload=PAYLOAD_NUTRITION_START_ANKETA,
        ),
    )
    builder.row(
        CallbackButton(text="← Назад в меню", payload=PAYLOAD_BACK),
    )
    return builder.as_markup()


def categories_keyboard(categories: Iterable) -> object:
    """Список категорий по 1 в ряду + «Назад в главное меню».

    categories — iterable ServiceCategory-инстансов; используем .id и .name.
    Лимит MAX_KEYBOARD_ROWS = 29 → молча обрезаем (8 категорий — норма).
    """
    builder = InlineKeyboardBuilder()
    for cat in list(categories)[:MAX_KEYBOARD_ROWS]:
        builder.row(
            CallbackButton(text=cat.name, payload=f"{PAYLOAD_CAT_PREFIX}{cat.id}"),
        )
    builder.row(CallbackButton(text="← Назад в меню", payload=PAYLOAD_BACK))
    return builder.as_markup()


def services_keyboard(services: Iterable) -> object:
    """Список услуг внутри категории + «Назад к категориям».

    Лимит: MAX_KEYBOARD_ROWS=29 услуг + 1 ряд под «Назад» = 30 рядов
    (хард-лимит MAX API). Излишек обрезается.
    """
    builder = InlineKeyboardBuilder()
    for svc in list(services)[:MAX_KEYBOARD_ROWS]:
        builder.row(
            CallbackButton(text=f"💆 {svc.name}", payload=f"{PAYLOAD_SVC_PREFIX}{svc.id}"),
        )
    builder.row(CallbackButton(text="← Категории", payload=PAYLOAD_MENU_SERVICES))
    return builder.as_markup()


def faq_keyboard(articles: Iterable) -> object:
    """Список FAQ-статей по 1 в ряду + «Назад».

    articles — iterable HelpArticle, используем .id и .question.
    Лимит MAX_KEYBOARD_ROWS=29 + 1 ряд под Назад = 30 (хард-лимит MAX API).
    На 168 статьях обрезается; полный список ищется через RAG (T-05+).
    """
    builder = InlineKeyboardBuilder()
    for art in list(articles)[:MAX_KEYBOARD_ROWS]:
        # MAX лимит на text кнопки — обрезаем длинные question (>64 chars)
        text = art.question if len(art.question) <= 64 else art.question[:61] + "…"
        builder.row(
            CallbackButton(text=text, payload=f"{PAYLOAD_FAQ_PREFIX}{art.id}"),
        )
    builder.row(CallbackButton(text="← Назад в меню", payload=PAYLOAD_BACK))
    return builder.as_markup()


def back_to_menu_keyboard() -> object:
    """Одна кнопка «← Назад в меню»."""
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="← Назад в меню", payload=PAYLOAD_BACK))
    return builder.as_markup()


def reminder_24h_keyboard(reminder_id: str) -> object:
    """3 кнопки для T-24h напоминания: Подтверждаю / Перенести / Отменить."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(
            text="✅ Подтверждаю",
            payload=f"{PAYLOAD_REM_CONFIRM_PREFIX}{reminder_id}",
        ),
    )
    builder.row(
        CallbackButton(
            text="🔄 Перенести",
            payload=f"{PAYLOAD_REM_RESCHEDULE_PREFIX}{reminder_id}",
        ),
        CallbackButton(
            text="❌ Отменить",
            payload=f"{PAYLOAD_REM_CANCEL_PREFIX}{reminder_id}",
        ),
    )
    return builder.as_markup()


def confirm_booking_keyboard(*, with_other: bool = False) -> object:
    """Подтверждение/отмена заявки.

    with_other=True добавляет кнопку «📝 Указать другие данные» — для сценария
    повторной записи где бот предлагает использовать сохранённые имя/телефон.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="✅ Да, всё верно", payload=PAYLOAD_CONFIRM_YES),
        CallbackButton(text="❌ Отмена", payload=PAYLOAD_CONFIRM_NO),
    )
    if with_other:
        builder.row(
            CallbackButton(text="📝 Указать другие данные", payload=PAYLOAD_CONFIRM_OTHER),
        )
    return builder.as_markup()


# ─── Phase 3.1 Part 1: TIER-A anketa keyboard builders ─────────────────────


def anketa_consent_keyboard():
    """TIER-A T04: дисклеймер 152-ФЗ для базовой обработки ПД анкеты."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="✓ Понятно, продолжаем",
                       payload=PAYLOAD_ANKETA_CONSENT_OK),
    )
    builder.row(
        CallbackButton(text="Не сейчас",
                       payload=PAYLOAD_ANKETA_CONSENT_DECLINE),
    )
    return builder.as_markup()


def anketa_gender_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Женский", payload=PAYLOAD_ANKETA_GENDER_FEMALE),
        CallbackButton(text="Мужской", payload=PAYLOAD_ANKETA_GENDER_MALE),
    )
    builder.row(
        CallbackButton(text="⏭ Пропустить", payload=PAYLOAD_ANKETA_SKIP),
    )
    return builder.as_markup()


def anketa_skip_keyboard():
    """Универсальный 1-кнопочный keyboard для text-input шагов
    (age/height/weight). Юзер пишет число или жмёт пропустить."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="⏭ Пропустить", payload=PAYLOAD_ANKETA_SKIP),
    )
    return builder.as_markup()


def anketa_goal_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="⬇ Похудеть",
                       payload=PAYLOAD_ANKETA_GOAL_LOSE),
    )
    builder.row(
        CallbackButton(text="➡ Держать вес",
                       payload=PAYLOAD_ANKETA_GOAL_MAINTAIN),
    )
    builder.row(
        CallbackButton(text="⬆ Набрать / подтянуть фигуру",
                       payload=PAYLOAD_ANKETA_GOAL_GAIN),
    )
    return builder.as_markup()


def anketa_pace_keyboard():
    """Темп для goal=lose. Fast (-20%) скрыт за /настройки (Design §4.4)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="🐢 Спокойный (-10%)",
                       payload=PAYLOAD_ANKETA_PACE_GENTLE),
        CallbackButton(text="⚖️ Средний (-15%)",
                       payload=PAYLOAD_ANKETA_PACE_MODERATE),
    )
    return builder.as_markup()


def anketa_gain_clarify_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="💪 Набрать вес",
                       payload=PAYLOAD_ANKETA_GAIN_MASS),
    )
    builder.row(
        CallbackButton(text="🌸 Подтянуть фигуру",
                       payload=PAYLOAD_ANKETA_GAIN_TONE),
    )
    return builder.as_markup()


def anketa_bmi_ladder_keyboard():
    """BMI<18.5 + goal=lose. «К врачу» НЕ ведёт в салон (Design §4.4)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Хочу к врачу",
                       payload=PAYLOAD_ANKETA_BMI_DOCTOR),
    )
    builder.row(
        CallbackButton(text="Поменять на «держать»",
                       payload=PAYLOAD_ANKETA_BMI_SWITCH_MAINTAIN),
    )
    builder.row(
        CallbackButton(text="Всё равно худеть",
                       payload=PAYLOAD_ANKETA_BMI_OVERRIDE),
    )
    return builder.as_markup()


def anketa_complete_keyboard():
    """Финал TIER-A: одна next-step кнопка."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📸 Сфоткать первый приём",
                       payload=PAYLOAD_NUTRITION_FIRST_MEAL),
    )
    return builder.as_markup()


def action_row_keyboard():
    """Узкий action-bar для footer'а scan-карточки и daily_summary.

    `[📸 Фото][💧 Вода][📋 Меню]` — минимум CTA-кнопок без полного
    main_menu. Photo button переиспользует `cb:menu:nutrition` payload
    (jump в entry-screen дневника). Water — заглушка Part 2B.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📸 Фото", payload=PAYLOAD_MENU_NUTRITION),
        CallbackButton(text="💧 Вода", payload=PAYLOAD_NUTRITION_ADD_WATER),
        CallbackButton(text="📋 Меню", payload=PAYLOAD_MENU_BOOK),
    )
    return builder.as_markup()


def food_scan_correct_menu_keyboard():
    """Открывается по клику [✏️ Поправить] на scan-карточке.

    4 опции по Design Doc §5.4: размер порции / другое блюдо / добавить
    ингредиент / удалить. Последние 3 — заглушки до Phase 3.2 (требуют
    новых Ayla endpoints для override scan и delete FoodLog).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📦 Размер порции",
                       payload=PAYLOAD_SCAN_PORTION_OPEN_MENU),
    )
    builder.row(
        CallbackButton(text="🔄 Это другое блюдо",
                       payload=PAYLOAD_SCAN_OTHER_DISH),
    )
    builder.row(
        CallbackButton(text="➕ Добавить ингредиент",
                       payload=PAYLOAD_SCAN_ADD_INGREDIENT),
    )
    builder.row(
        CallbackButton(text="⏭ Удалить",
                       payload=PAYLOAD_SCAN_DELETE),
    )
    return builder.as_markup()


def food_scan_portion_keyboard():
    """[Меньше] [Норм] [Больше] — выбор portion_multiplier 0.7/1.0/1.3."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Меньше", payload=PAYLOAD_SCAN_PORTION_SMALLER),
        CallbackButton(text="Норм", payload=PAYLOAD_SCAN_PORTION_NORMAL),
        CallbackButton(text="Больше", payload=PAYLOAD_SCAN_PORTION_LARGER),
    )
    return builder.as_markup()


def food_scan_low_confidence_keyboard():
    """Low confidence (<0.5) или FoodNotRecognizedError → 2 кнопки:
    переснять или ввести вручную (заглушка manual = Phase 3.2)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📸 Переснять", payload=PAYLOAD_SCAN_RETAKE),
        CallbackButton(text="✏️ Напишу сама", payload=PAYLOAD_SCAN_MANUAL_INPUT),
    )
    return builder.as_markup()


def water_amount_keyboard():
    """Главный selector воды — 4 quick-add + [Другое]."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="+200 мл", payload=PAYLOAD_WATER_AMOUNT_200),
        CallbackButton(text="+250 мл · стакан",
                       payload=PAYLOAD_WATER_AMOUNT_250),
    )
    builder.row(
        CallbackButton(text="+500 мл · бутылка",
                       payload=PAYLOAD_WATER_AMOUNT_500),
        CallbackButton(text="+1000 мл · литр",
                       payload=PAYLOAD_WATER_AMOUNT_1000),
    )
    builder.row(
        CallbackButton(text="✏️ Другое", payload=PAYLOAD_WATER_MORE),
    )
    return builder.as_markup()


def water_extended_keyboard():
    """Atypical volumes (после клика [✏️ Другое])."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="+150 мл", payload=PAYLOAD_WATER_EXTENDED_150),
        CallbackButton(text="+300 мл", payload=PAYLOAD_WATER_EXTENDED_300),
    )
    builder.row(
        CallbackButton(text="+350 мл", payload=PAYLOAD_WATER_EXTENDED_350),
        CallbackButton(text="+750 мл", payload=PAYLOAD_WATER_EXTENDED_750),
    )
    builder.row(
        CallbackButton(text="+1500 мл", payload=PAYLOAD_WATER_EXTENDED_1500),
    )
    return builder.as_markup()


def water_undo_keyboard(*, entry_id: str):
    """1-button [↩️ Отменить] inline после успешного add_water.

    Payload format: `cb:water:undo:{entry_id}` — handler парсит entry_id
    и вызывает undo_water. После клика Ayla soft-delete'ит запись (15-мин
    restore window server-side, restore по UI — backlog Phase 3.2).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(
            text="↩️ Отменить",
            payload=f"{PAYLOAD_WATER_UNDO_PREFIX}{entry_id}",
        ),
    )
    return builder.as_markup()


def daily_report_footer_keyboard():
    """Footer для дневного отчёта (Design §6.2 + 2D.2): 3 кнопки.

    [📊 Неделя] — Phase 3.3 backlog (stub-handler).
    [⚙️ Время] — settings: выбор времени дневного отчёта (Part 2D.2).
    [🔔 Напоминания] — settings: toggle adaptive water reminders (Part 2D.2).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📊 Неделя", payload=PAYLOAD_REPORT_WEEKLY),
        CallbackButton(text="⚙️ Время", payload=PAYLOAD_REPORT_TIME_SETTINGS),
        CallbackButton(text="🔔 Напоминания", payload=PAYLOAD_WATER_REMINDERS_FOOTER),
    )
    return builder.as_markup()


def daily_report_time_keyboard():
    """Settings menu: выбор времени дневного отчёта (Design §6.1)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="🌇 18:00", payload=PAYLOAD_REPORT_TIME_18),
        CallbackButton(text="🌙 21:00", payload=PAYLOAD_REPORT_TIME_21),
    )
    builder.row(
        CallbackButton(text="🌃 23:00", payload=PAYLOAD_REPORT_TIME_23),
        CallbackButton(text="🔕 Выкл", payload=PAYLOAD_REPORT_TIME_OFF),
    )
    return builder.as_markup()


def water_reminders_settings_keyboard(*, currently_enabled: bool):
    """Settings menu: toggle adaptive water reminders (Design §7.7).

    Default OFF (opt-in). Кнопка показывает противоположный action — чтобы
    клик вёл к change.
    """
    label = "🔕 Выключить" if currently_enabled else "🔔 Включить"
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text=label, payload=PAYLOAD_WATER_REMINDERS_TOGGLE),
    )
    return builder.as_markup()


def water_reminder_buttons_keyboard():
    """Inline keyboard под текстом adaptive reminder (Design §7.7).

    [+250 мл][+500 мл] реюзают on_water_add_quick (Part 2B T03 PAYLOAD_WATER_AMOUNT_*).
    [Уже пью] → on_water_dismiss_reminder — silent ack, до завтра молчим.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="+250 мл", payload=PAYLOAD_WATER_AMOUNT_250),
        CallbackButton(text="+500 мл", payload=PAYLOAD_WATER_AMOUNT_500),
    )
    builder.row(
        CallbackButton(text="Уже пью", payload=PAYLOAD_WATER_DISMISS),
    )
    return builder.as_markup()


# ─── Phase 3.2A: TIER-B health screening ───────────────────────────────────

PAYLOAD_TIER_B_CONSENT_OK = "cb:tier_b:consent:ok"
PAYLOAD_TIER_B_CONSENT_DECLINE = "cb:tier_b:consent:decline"

# Универсальные yes/no/skip — handler определяет current state и значение
PAYLOAD_TIER_B_YES = "cb:tier_b:yes"
PAYLOAD_TIER_B_NO = "cb:tier_b:no"
PAYLOAD_TIER_B_SKIP = "cb:tier_b:skip"

PAYLOAD_TIER_B_DIABETES_NO = "cb:tier_b:diabetes:no"
PAYLOAD_TIER_B_DIABETES_T1 = "cb:tier_b:diabetes:t1"
PAYLOAD_TIER_B_DIABETES_T2 = "cb:tier_b:diabetes:t2"
PAYLOAD_TIER_B_DIABETES_PRE = "cb:tier_b:diabetes:pre"

PAYLOAD_TIER_B_CHRONIC_DONE = "cb:tier_b:chronic:done"
PAYLOAD_TIER_B_CHRONIC_NONE = "cb:tier_b:chronic:none"

PAYLOAD_TIER_B_ALLERGIES_NONE = "cb:tier_b:allergies:none"
PAYLOAD_TIER_B_ALLERGIES_TEXT = "cb:tier_b:allergies:text"
PAYLOAD_TIER_B_ALLERGIES_VAGUE = "cb:tier_b:allergies:vague"

PAYLOAD_TIER_B_MENOPAUSE_NO = "cb:tier_b:menopause:no"
PAYLOAD_TIER_B_MENOPAUSE_YES = "cb:tier_b:menopause:yes"
PAYLOAD_TIER_B_MENOPAUSE_UNSURE = "cb:tier_b:menopause:unsure"


CHRONIC_SLUG_LABELS: dict[str, str] = {
    "hypertension": "Гипертония",
    "gi_problems": "ЖКТ",
    "thyroid": "Щитовидная",
    "menopause": "Менопауза",
    "eating_disorder": "РПП в прошлом или сейчас",
}


def tier_b_consent_keyboard():
    """Screen 0 — consent (Design §4.5)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="✓ Понятно, продолжаем", payload=PAYLOAD_TIER_B_CONSENT_OK),
    )
    builder.row(
        CallbackButton(text="Не сейчас, без советов", payload=PAYLOAD_TIER_B_CONSENT_DECLINE),
    )
    return builder.as_markup()


def tier_b_yes_no_keyboard():
    """Screen 1 — pregnancy + Screen 2 — diabetes (БЕЗ skip per Design §4.5)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Да", payload=PAYLOAD_TIER_B_YES),
        CallbackButton(text="Нет", payload=PAYLOAD_TIER_B_NO),
    )
    return builder.as_markup()


def tier_b_yes_no_skip_keyboard():
    """Screen 1b — breastfeeding + Screen 3b — meds."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Да", payload=PAYLOAD_TIER_B_YES),
        CallbackButton(text="Нет", payload=PAYLOAD_TIER_B_NO),
    )
    builder.row(
        CallbackButton(text="⏭ Пропустить", payload=PAYLOAD_TIER_B_SKIP),
    )
    return builder.as_markup()


def tier_b_diabetes_keyboard():
    """Screen 2 — diabetes (4 опции, БЕЗ skip)."""
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="Нет", payload=PAYLOAD_TIER_B_DIABETES_NO))
    builder.row(
        CallbackButton(text="1 типа", payload=PAYLOAD_TIER_B_DIABETES_T1),
        CallbackButton(text="2 типа", payload=PAYLOAD_TIER_B_DIABETES_T2),
    )
    builder.row(CallbackButton(text="Преддиабет", payload=PAYLOAD_TIER_B_DIABETES_PRE))
    return builder.as_markup()


def tier_b_chronic_keyboard(*, selected: set):
    """Screen 2b — multi-select toggle. selected — set of chosen slugs.

    Shows ✓ префикс на selected items для visual feedback. Click toggle'ит
    the slug. «✓ Готово» → save. «Ничего» → clear + save.
    """
    builder = InlineKeyboardBuilder()
    for slug, label in CHRONIC_SLUG_LABELS.items():
        prefix = "✓ " if slug in selected else ""
        builder.row(
            CallbackButton(
                text=f"{prefix}{label}",
                payload=f"cb:tier_b:chronic:toggle:{slug}",
            ),
        )
    builder.row(
        CallbackButton(text="✓ Готово", payload=PAYLOAD_TIER_B_CHRONIC_DONE),
        CallbackButton(text="Ничего", payload=PAYLOAD_TIER_B_CHRONIC_NONE),
    )
    return builder.as_markup()


def tier_b_allergies_choice_keyboard():
    """Screen 3 — allergies (3 опции)."""
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="❌ Нет", payload=PAYLOAD_TIER_B_ALLERGIES_NONE))
    builder.row(CallbackButton(text="📝 Напишу какие", payload=PAYLOAD_TIER_B_ALLERGIES_TEXT))
    builder.row(CallbackButton(text="🤷 Не уверена", payload=PAYLOAD_TIER_B_ALLERGIES_VAGUE))
    return builder.as_markup()


def tier_b_meds_keyboard():
    """Screen 3b — meds (yes/no/skip)."""
    return tier_b_yes_no_skip_keyboard()


def tier_b_menopause_keyboard():
    """Screen 4 — menopause (conditional ж 45+)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Нет", payload=PAYLOAD_TIER_B_MENOPAUSE_NO),
        CallbackButton(text="Да", payload=PAYLOAD_TIER_B_MENOPAUSE_YES),
    )
    builder.row(
        CallbackButton(text="Не уверена", payload=PAYLOAD_TIER_B_MENOPAUSE_UNSURE),
    )
    builder.row(
        CallbackButton(text="⏭ Пропустить", payload=PAYLOAD_TIER_B_SKIP),
    )
    return builder.as_markup()


# ─── DRF-358: food/drink clarification card ────────────────────────────────

PAYLOAD_FOOD_TO_DIARY = "cb:ux:food_to_diary"
PAYLOAD_FOOD_TYPO = "cb:ux:food_typo"


def food_drink_clarify_keyboard():
    """DRF-358: 2-button card для friendly food/drink fallback.

    Used когда parse_beverage miss но text похож на еду/напиток
    (looks_like_food_drink → True). Показывается ВМЕСТО передачи в AI
    Concierge (экономит $0.0001 + ~1.5s latency).
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📝 В дневник", payload=PAYLOAD_FOOD_TO_DIARY),
    )
    builder.row(
        CallbackButton(text="Это была опечатка", payload=PAYLOAD_FOOD_TYPO),
    )
    return builder.as_markup()
