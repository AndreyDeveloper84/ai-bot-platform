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
    # DRF-1872 — память сказанного (said_memory): те же зелёные строки, тот же
    # экспорт `read_green_entries` без фильтра по ключу — перечислены, чтобы
    # таблица оставалась полной описью.
    "memory_key:city": "memory",
    "memory_key:visit_context": "memory",
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
        "решение владельца. После «удалить всё» колонка обнуляется на месте, "
        "а обезличенное тело переезжает в conversations.ArchivedMessage "
        "(DRF-1369) — см. следующую строку."
    ),
    "conversations.ArchivedMessage.body": (
        "Обезличенная переписка, оставшаяся ПОСЛЕ «удалить всё». Решение "
        "владельца (OD_MEMORY.md §4): переписка обезличивается, а не "
        "удаляется — это единственная запись того, что бот на самом деле "
        "сказал человеку, и она нужна при разборе инцидента и спора о брони. "
        "Прямые идентификаторы (телефон, почта, карта) вырезаны при переносе; "
        "слова остаются. Не выгружается по тем же двум причинам, что и живая "
        "переписка — объём и данные третьих лиц, — и названа здесь отдельной "
        "строкой, потому что это хранилище, которое человек не ожидает: он "
        "просил удалить, и часть текста осталась. Срок — 90 дней "
        "(ANONYMIZED_DIALOGUE_RETENTION_DAYS), подметает "
        "apps.conversations.tasks.purge_expired_archived_messages; сам срок "
        "выведен из яруса аудита и стоит вопросом к владельцу."
    ),
    "conversations.Conversation.skill_state": (
        "Состояние незавершённых пошаговых сценариев, в том числе анкеты "
        "питания и коррекции блюда. Живёт до конца сценария и пересобирается "
        "заново; итог, если человек его подтвердил, оседает зелёной записью "
        "памяти и выгружается в разделе memory. После «удалить всё» "
        "опустошается целиком тем же обновлением, что обезличивает переписку "
        "(DRF-2181)."
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
    # DRF-2183 — три хранилища, о которых выгрузка молчала. Решение по
    # черновикам взято по прецеденту `Message.content`, до слова владельца:
    # это та же переписка плюс работа мастера, и если владелец решит
    # выгружать переписку, черновики поедут вместе с ней.
    "conversations.AiDraft.content": (
        "Неотправленный черновик ответа мастеру, который ассистент собирает "
        "из реплик клиента и который может цитировать их дословно. Не "
        "выгружается по тем же двум причинам, что и живая переписка — объём "
        "и данные третьих лиц: черновик — рабочая поверхность мастера, а не "
        "клиента. Текст очищается, когда мастер отправил черновик сам, "
        "передал ответ ИИ или черновик заменён более новым. Неиспользованный "
        "черновик хранится до следующего сообщения в диалоге. При «удалить "
        "всё» очищается сразу вместе с обезличиванием переписки (DRF-1369), "
        "а если чатовый путь не сработал — самое позднее в течение часа."
    ),
    "redis.short_term": (
        "Кратковременная память диалога в Redis — последние реплики человека "
        "и ответы Ayla, те же, что в самой переписке. Живёт сутки "
        "(SHORT_TERM_MEMORY_TTL_SECONDS, продлевается на каждой реплике) и "
        "нужна, чтобы Ayla помнила ход разговора. Не выгружается по тем же "
        "причинам, что и переписка, чьей копией на сутки она является. При "
        "«удалить всё» очищается сразу, не дожидаясь срока, а если чатовый "
        "путь не сработал — самое позднее в течение часа."
    ),
    "redis.pii_tokenmap": (
        "Техническая обратная карта «токен → исходное значение» для "
        "маскировки в запросах к языковой модели: модель видит токен, а не "
        "значение. Туда попадают телефоны, адреса почты, номера карт, "
        "одноразовые коды и ссылки с токенами, встреченные в переписке и "
        "промптах этого диалога, — в том числе принадлежащие третьим лицам "
        "(мастеру, салону, тому, чей номер человек прислал). Не выгружается "
        "по тем же причинам, что и переписка. В Postgres не пишется; живёт в "
        "Redis до 25 часов после последнего появления такого значения в "
        "диалоге. При «удалить всё» очищается сразу, а если чатовый путь не "
        "сработал — самое позднее в течение часа, следующим прогоном свипа."
    ),
    # DRF-2220 — очередь входящих вебхуков. До листа тела лежали бессрочно:
    # потребитель делал только XACK, удаления не было. Срок в тексте обязан
    # совпадать с settings.INGRESS_RAW_RETENTION_HOURS — это держит тест.
    "redis.ingress_stream": (
        "Входящие сообщения из MAX в том виде, в каком их прислал мессенджер, — "
        "текст, имя, присланный контакт, — в очереди Redis между приёмом и "
        "обработкой. Обработанное сообщение удаляется из очереди сразу после "
        "обработки. Сообщение, обработка которого не удалась, остаётся в "
        "очереди для разбора, а если включён разбор зависших — переносится в "
        "отдельную очередь разбора; в обоих местах оно хранится не дольше 72 "
        "часов и затем удаляется. Не выгружается: это техническая копия тех же "
        "сообщений, что в переписке, на время доставки. При «удалить всё» "
        "сообщения человека, пришедшие до запроса, удаляются из обеих очередей "
        "сразу, а если чатовый путь не сработал — самое позднее в течение часа. "
        "Сообщение, отправителя которого не удалось прочитать, этим шагом не "
        "удаляется — оно может быть чужим — и уходит по сроку в 72 часа. Если "
        "в момент запроса очередь была недоступна, остальное удаление "
        "выполняется, а сообщения из очереди уходят по тому же сроку в 72 часа."
    ),
    # DRF-2214 — три хранилища бота, которые «забудь всё» не трогало и о
    # которых выгрузка молчала (замер PR-3).
    "redis.dre_state": (
        "Состояние текущего разговора у движка подбора: какие сведения для "
        "выбора (бюджет, район, время) уже названы в этом разговоре и чем "
        "подтверждены. Хранится в Redis не дольше двух часов после последней "
        "реплики и затем исчезает само. Не выгружается: это рабочая копия "
        "сказанного в переписке на время одного разговора. При «удалить всё» "
        "удаляется сразу вместе с остальными служебными копиями разговора, а "
        "если чатовый путь не сработал — самое позднее в течение часа."
    ),
    "nutrition_proactive:observations": (
        "Наблюдения для напоминаний о питании: сколько воды вы выпили за день "
        "к моменту последнего напоминания, сколько напоминаний отправлено "
        "сегодня и сколько подряд осталось без ответа, и день последнего "
        "вечернего отчёта. Это счётчики на один день для расписания, а не "
        "дневник: сам дневник воды выгружается в разделе ayla. При «удалить "
        "всё» стираются; настройки напоминаний остаются."
    ),
    "nutrition_proactive:journal": (
        "Журнал отправленных напоминаний о питании: только время отправки и "
        "вид сообщения (отчёт, вода), без текста сообщения и без ваших "
        "ответов. Нужен, чтобы бот не писал чаще недельного лимита и "
        "замолкал, если напоминания остаются без ответа. Не выгружается как "
        "служебный журнал расписания; при «удалить всё» остаётся, иначе "
        "лимит обнулился бы и бот мог бы написать раньше, чем написал бы."
    ),
}

#: Stores that hold personal data, have no slot in the registry, and ARE
#: carried in the export — under the named section (DRF-2183).
#:
#: ``NON_REGISTRY_STORES`` renders into ``withheld``, so before this table the
#: coverage could only say «not exported» about a non-registry store. Consents
#: ARE exported (the ``consents`` section) — declaring them withheld would have
#: told the person «we do not give you this» about data two paragraphs up in
#: the same file: a false statement in a legal document. ``test_export_coverage_
#: undeclared_2183`` pins that every section named here is a real top-level key
#: of the export, so the declaration cannot promise what the file does not hold.
#:
#: ``consent.ConsentRecord`` is carried in substance — type, granted, document
#: version, source and both dates. Not carried: the internal id, the FK back to
#: the person, and the tenant (which salon the consent was given to) — the last
#: one is a known gap of the ``consents`` section itself, tracked separately.
NON_REGISTRY_SECTIONS: Mapping[str, str] = {
    "consent.ConsentRecord": "consents",
    # DRF-2214 — что Ayla показала человеку как направление и почему (К-3).
    "recommendation.Recommendation": "recommendations",
    # DRF-2214 — тумблеры проактивных сообщений о питании, которые человек
    # ведёт сам (как ``preferences``); наблюдения и журнал — невыгруженными.
    "nutrition_proactive:settings": "nutrition_notification_settings",
    # DRF-2214 — что каталог запомнил вне профиля (beautygo_backend #544):
    # разделы внутри ``ayla``, которые Ayla отдаёт дословно.
    "catalog.goals": "ayla",
    "catalog.wellness_plan": "ayla",
    "catalog.nutrition_profile": "ayla",
    "catalog.food_diary": "ayla",
    "catalog.shown_hints": "ayla",
    # DRF-2307 — beautygo_backend #545 и #548.
    "catalog.notification_history": "ayla",
    "catalog.app_ai_chat": "ayla",
    "catalog.favorite_specialists": "ayla",
}

#: DRF-2307 — зеркало верхних ключей ответа C5.1 каталога
#: (``users.personal_data_api.InternalPersonalDataExportView``), вручную, как
#: ``ROUTE_TABLE`` в ``apps/integrations/ayla/tests/test_contract_route_table``.
#: Каталог добавил раздел — впиши сюда, в ``NON_REGISTRY_SECTIONS`` (для
#: разделов запомненного) и в строку ``KNOWN_LIMITS`` про ``ayla``, и дай ему
#: исход в матрице «забудь всё». Сверку «зеркало ↔ объявления» держит
#: ``test_catalog_sections_declared_2307``; «зеркало ↔ настоящий каталог» —
#: ночной живой узел ``tests/e2e/test_ayla_integration.py``.
CATALOG_EXPORT_SECTIONS: tuple[str, ...] = (
    "user_id",
    "exported_at",
    "profile",
    "personal_context",
    "specialist_profile",
    "goals",
    "wellness_plan",
    "nutrition_profile",
    "food_diary",
    "shown_hints",
    "notification_history",
    "app_ai_chat",
    "favorite_specialists",
    "linked_identities",
)

#: Known incompleteness of the export ITSELF — not a store, a behaviour.
#: Declared for the same reason as everything else here: it is better written
#: down than discovered by a regulator.
KNOWN_LIMITS: tuple[str, ...] = (
    # DRF-2183 — пробел раздела `consents`, найденный замером: назван в самом
    # документе, а не только в комментарии кода (отдельный лист у главного
    # окна).
    "Раздел consents перечисляет каждое согласие — тип, дано или отозвано, "
    "версию документа, источник и обе даты, — но не указывает салон, которому "
    "оно дано. Если вы пользовались несколькими салонами, строки согласий в "
    "этом разделе по салонам не различить.",
    "Разделы memory и personal_context читаются через тот же гейт, что и "
    "промпт. Поэтому в окне между «забудь всё» и развёрткой "
    "(apps.identity.services.forget_all_sweep, ежечасно) выгрузка покажет "
    "пусто, хотя строки ещё физически лежат с надгробием впереди. Это "
    "недо-отчёт длиной не больше часа, и он назван здесь, а не подразумевается.",
    "Раздел ayla отдаётся вышестоящей системой дословно: его состав "
    "определяет владелец декларированного профиля (users.UserPersonalContext "
    "в Ayla), а не этот файл. Сегодня в нём — профиль, профиль предпочтений, "
    "профиль мастера и то, что каталог запомнил о вас вне профиля: цели и "
    "ответы анкеты цели (goals), план и отметки прогресса (wellness_plan), "
    "профиль питания (nutrition_profile), дневник питания с фото сканера "
    "(food_diary), история показанных подсказок (shown_hints), история "
    "уведомлений (notification_history), переписка с ИИ-чатом приложения "
    "(app_ai_chat) и избранные мастера из приложения BeautyGO "
    "(favorite_specialists) — их «забудь всё» оставляет. Полнота того "
    "раздела — обязательство Ayla, и проверяется на её стороне; здесь он не "
    "переписывается и не фильтруется, чтобы выгрузка не расходилась с тем, "
    "что реально хранит владелец.",
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

    for store, section in NON_REGISTRY_SECTIONS.items():
        included.setdefault(section, []).append(store)

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
