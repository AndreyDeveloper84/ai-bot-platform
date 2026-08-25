"""What the 152-ФЗ export carries, what it does not, and why — per slot.

DRF-1370. ``privacy.export_personal_data`` returned four sections and said
nothing about the rest. Its own comment (``privacy.py``, the DRF-1262 block)
had already settled the principle —

    «152-ФЗ ст. 14 gives the subject (and the regulator) the composition of
    the data actually PROCESSED. […] dropping it would under-report what we
    hold, which is the worse failure for a legal document.»

— and applied it only inside the one section it was written about. Across the
whole document the export under-reported the composition, which is the failure
that comment names, in the file that names it.

# The two honest answers, and which one each slot got

The task allows either: widen the export, or declare in the export itself
what is left out and why. Both are answers; only silence is not. So this
module is the declaration, and the declaration is *machine-checked against
the registry* rather than written once and left to rot:

``apps.identity.personal_fields.PERSONAL_FIELDS`` is already the list of every
declared personal slot in this repository, enforced by
``tools/lint/personal_field_guard.py`` — which discovers slots FROM THE CODE
and fails on any it cannot find a line for. That guard makes the registry a
complete inventory of what is stored. This module maps every entry of that
inventory to an export decision, and
``test_export_coverage.py`` fails when the two disagree in either direction:

* a slot in the registry with no coverage line — a personal field was added
  and nobody decided whether the subject gets to see it;
* a coverage line for a slot the registry no longer has — the reason is stale
  and the reader is being told about a column that is gone.

That is the same ratchet, and the same reason, as the registry's own
``POLICY_DEBT``: a blanket «and some other things are not exported» is a way
of not looking at them.

# Reading the table

``SECTIONS`` maps an exported slot to the key it appears under in the export
JSON. ``EXCLUSIONS`` maps a withheld slot to a reason slug, and ``REASONS``
holds the prose. Reasons are grouped because the honest answer for eighteen
computed booking aggregates is genuinely one answer — but the slot list under
each is explicit, so a reviewer can see exactly which columns it covers.
"""

from __future__ import annotations

from typing import Mapping

#: Slot → the export JSON key its VALUE appears under.
SECTIONS: Mapping[str, str] = {
    # The bot-side memory profile row. Bot-owned, surfaced into the system
    # prompt, and free prose in the case of `summary` — precisely the thing a
    # person means when they ask what Ayla thinks it knows about them.
    "identity.UserPersonalContext.display_name_preferred": "personal_context",
    "identity.UserPersonalContext.language_preferred": "personal_context",
    "identity.UserPersonalContext.summary": "personal_context",
    "identity.UserPersonalContext.minor_lock": "personal_context",
    # Green MemoryEntry rows, one per stated/inferred fact. Already exported
    # before DRF-1370; listed so the table is the whole inventory, not the
    # additions.
    "memory_key:diet": "memory",
    "memory_key:preferred_time_slots": "memory",
    "memory_key:preferred_districts": "memory",
    "memory_key:price_range": "memory",
    "memory_key:favorite_masters": "memory",
    # The Mini App profile screen's own values. Added by DRF-1370: a person
    # who exported their data did not see the preferences they had set
    # themselves, on our own screen, minutes earlier.
    "identity.UserPreferences.notify_reminders": "preferences",
    "identity.UserPreferences.notify_retention": "preferences",
    "identity.UserPreferences.notify_promo": "preferences",
    "identity.UserPreferences.notify_birthday": "preferences",
    "identity.UserPreferences.birthday_date": "preferences",
}

#: Slot → why its value is withheld. Every key here is deliberate.
EXCLUSIONS: Mapping[str, str] = {
    "identity.UserPreferences.allergies": "special_category",
    "identity.BotUser.avatar_url": "channel_shell",
    "identity.BotUser.phone": "channel_shell",
    "identity.BotUser.display_name": "channel_shell",
    "identity.BotUser.client_name": "channel_shell",
    "identity.BotUser.proactive_messages_opt_out": "channel_shell",
    "identity.BotUser.timezone": "channel_shell",
    "identity.BotUser.context": "unschematised",
    "identity.ClientProfile.recency_days": "salon_observation",
    "identity.ClientProfile.frequency_visits": "salon_observation",
    "identity.ClientProfile.monetary_total": "salon_observation",
    "identity.ClientProfile.rfm_segment": "salon_observation",
    "identity.ClientProfile.ltv": "salon_observation",
    "identity.ClientProfile.predicted_ltv_12m": "salon_observation",
    "identity.ClientProfile.churn_risk": "salon_observation",
    "identity.ClientProfile.lifecycle_stage": "salon_observation",
    "identity.ClientProfile.avg_visit_interval_days": "salon_observation",
    "identity.ClientProfile.favorite_service_id": "salon_observation",
    "identity.ClientProfile.favorite_category_id": "salon_observation",
    "identity.ClientProfile.preferred_master_id": "salon_observation",
    "identity.ClientProfile.loyalty_tier": "salon_observation",
    "identity.ClientProfile.last_review_rating": "salon_observation",
    "identity.ClientProfile.last_review_at": "salon_observation",
    "identity.ClientProfile.low_rating_flag": "salon_observation",
    "identity.ClientProfile.sentiment_score": "salon_observation",
    "loyalty.LoyaltyAccount.balance": "transactional_ledger",
    "loyalty.LoyaltyAccount.tier": "transactional_ledger",
    "loyalty.LoyaltyAccount.tier_changed_at": "transactional_ledger",
    "loyalty.LoyaltyAccount.tier_reset_at": "transactional_ledger",
    "loyalty.LoyaltyAccount.enrolled": "transactional_ledger",
    "loyalty.LoyaltyAccount.opted_out_at": "transactional_ledger",
}

#: Reason slug → the sentence the export hands the subject, and the reviewer.
REASONS: Mapping[str, str] = {
    "special_category": (
        "Свободный текст о противопоказаниях и аллергиях — специальная "
        "категория персональных данных (152-ФЗ ст. 10). Состав выгрузки для "
        "таких данных — отдельное решение владельца, а не техническое; до "
        "него значение не выгружается, но сам факт хранения объявлен здесь. "
        "Поле выводится из системы отдельной задачей (DRF-1371)."
    ),
    "channel_shell": (
        "Контактные и профильные значения на «оболочке» пользователя в "
        "мессенджере: телефон, имя, аватар, часовой пояс, отказ от "
        "проактивных сообщений. Человек видит и правит их сам на экране "
        "профиля, и они приходят из мессенджера, а не из разговора с Ayla. "
        "Выгрузка их состава объявлена; включение значений в JSON — "
        "расширение охвата, которое стоит делать одним решением вместе с "
        "остальными строками этого раздела, а не по одной."
    ),
    "unschematised": (
        "JSON-мешок «флагов персонализации» без схемы. У него нет списка "
        "полей, поэтому нет и способа объявить его состав по строкам — "
        "выгружать его целиком значило бы выдать за состав данных то, что "
        "состава не имеет. Разбор мешка на именованные слоты числится в "
        "personal_fields.POLICY_DEBT и делается отдельно."
    ),
    "salon_observation": (
        "Вычисленный снимок RFM/LTV/риска: не то, что человек о себе сказал, "
        "а то, как салон прочитал историю его визитов. Пересчитывается "
        "ежедневно из бронирований и платежей, которые сами хранятся по "
        "закону о сроках хранения. Это данные о человеке, и право знать их "
        "состав здесь исполнено; выдача значений — отдельное решение, потому "
        "что часть из них (churn_risk, sentiment_score, low_rating_flag) — "
        "коммерческая оценка салона, а не факт о человеке."
    ),
    "transactional_ledger": (
        "Баланс и уровень программы лояльности — учётная запись операций. "
        "Как бронирования и платежи, она следует установленным законом "
        "срокам хранения и не стирается по «забудь всё»; человек видит "
        "баланс на своём экране лояльности. Состав объявлен здесь."
    ),
}

#: Stores that hold personal data but have no slot in the registry — the
#: registry declares MODEL COLUMNS, and these are not columns of that shape.
#: Named explicitly because the task asked for the divergence line by line,
#: and «what the registry happens to cover» is not the same list as «what we
#: hold». Each key is a store, each value a reason in the same voice.
NON_REGISTRY_STORES: Mapping[str, str] = {
    "conversations.Message.content": (
        "Переписка целиком: каждое сообщение человека и каждый ответ Ayla. "
        "Хранится как форензика (разбор жалоб, восстановление хода записи) и "
        "не выгружается: объём делает выгрузку нечитаемой, а совместные "
        "сообщения содержат данные третьих лиц — мастеров, администраторов. "
        "Выдача переписки по запросу — отдельная процедура и отдельное "
        "решение владельца."
    ),
    "conversations.Conversation.skill_state": (
        "Состояние незавершённых пошаговых сценариев, в том числе анкеты "
        "питания и коррекции блюда. Живёт до конца сценария и пересобирается "
        "заново; итог, если человек его подтвердил, оседает зелёной записью "
        "памяти и выгружается в разделе memory."
    ),
    "identity.MemoryEntry:yellow": (
        "Жёлтая зона — личные факты с обязательным согласием при записи и "
        "TTL 365 дней. В выгрузку не добавлена молча: состав выгрузки для "
        "неё — вопрос к владельцу, потому что рядом может лежать здоровье."
    ),
    "identity.MemoryEntry:red": (
        "Красная зона — специальная категория (152-ФЗ ст. 10). Читается "
        "только через аудируемый red_zone_reader, каждое чтение пишет "
        "RedZoneAccessLog. Добавление её в выгрузку — отдельное решение "
        "владельца, а не техническое."
    ),
    "conversations.StaffAssistantMessage.content": (
        "Диктовки сотрудника салонному ассистенту. Каскад удаления их уже "
        "стирает (DRF-1276); в выгрузку клиента они не входят, потому что "
        "это рабочая поверхность сотрудника, а не клиента."
    ),
}

#: Known incompleteness of the export ITSELF — not a store, a behaviour.
#: Declared for the same reason as everything else here: it is better written
#: down than discovered by a regulator.
KNOWN_LIMITS: tuple[str, ...] = (
    "Разделы memory и personal_context читаются через тот же гейт, что и "
    "промпт. Поэтому в окне между «забудь всё» и развёрткой "
    "(apps.identity.services.forget_all_sweep, ежечасно) выгрузка покажет "
    "пусто, хотя строки ещё физически лежат с надгробием впереди. Это "
    "недо-отчёт длиной не больше часа, и он назван здесь, а не подразумевается.",
    "Раздел ayla отдаётся вышестоящей системой дословно: его состав "
    "определяет владелец декларированного профиля (users.UserPersonalContext "
    "в Ayla), а не этот файл. Полнота того раздела — обязательство Ayla, и "
    "проверяется на её стороне; здесь он не переписывается и не фильтруется, "
    "чтобы выгрузка не расходилась с тем, что реально хранит владелец.",
)


def _all_registry_sites() -> list[str]:
    """Every declared personal slot, imported lazily to keep this stdlib-only."""

    from apps.identity.personal_fields import PERSONAL_FIELDS

    return [f.site for f in PERSONAL_FIELDS]


def build_coverage_section() -> dict:
    """The ``coverage`` block of the export: the composition, declared.

    Built from the registry at call time, so a personal field added tomorrow
    appears in tomorrow's export — as ``included`` or as an exclusion with a
    reason, and never as silence. (A slot with neither is a test failure, not
    a missing dict key: see ``test_export_coverage.py``.)
    """

    included: dict[str, list[str]] = {}
    withheld: list[dict[str, str]] = []

    for site in _all_registry_sites():
        section = SECTIONS.get(site)
        if section is not None:
            included.setdefault(section, []).append(site)
            continue
        reason_key = EXCLUSIONS.get(site)
        withheld.append(
            {
                "field": site,
                "reason": REASONS.get(reason_key or "", "")
                # A slot with no decision must not read as a decision. The
                # test forbids this state; the string exists so that if it
                # ever ships, it announces itself instead of looking normal.
                or "СОСТАВ НЕ ОБЪЯВЛЕН — поле добавлено без решения о выгрузке.",
            }
        )

    for store, reason in NON_REGISTRY_STORES.items():
        withheld.append({"field": store, "reason": reason})

    return {
        "explanation": (
            "152-ФЗ ст. 14 даёт право знать состав обрабатываемых данных. "
            "Ниже — полный перечень: что вошло в эту выгрузку значениями и "
            "что не вошло, с причиной по каждой строке. Пустых мест нет: "
            "поле, о котором никто не принял решения, ломает сборку."
        ),
        "included": {section: sorted(sites) for section, sites in sorted(included.items())},
        "withheld": sorted(withheld, key=lambda row: row["field"]),
        "known_limits": list(KNOWN_LIMITS),
    }
