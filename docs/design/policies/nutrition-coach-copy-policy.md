# Nutrition Coach & Weekly Report — Copy Editorial Policy

Date: 2026-09-04
Status: `ACTIVE`
Owner decision: Q-NUTRITION-02 / Q-NUTRITION-03, variant Б (2026-09-04)
Scope: every client-facing line written for the AI dietologist (reactive and
proactive) and the weekly nutrition report.

## Why this exists

With variant Б the boundary between «подсказал» and «понукает» runs **through
wording, not through code**. Both new surfaces are outbound messages about
health data (152-ФЗ ст. 10): a proactive hint and a weekly report. Without a
written rule the boundary would rest on the taste of whoever typed the string.

This document is that rule. It derives from
[`customer-wellness-goal-setting-ux.md`](customer-wellness-goal-setting-ux.md)
§2.1–2.6 («Strategic constraints — non-negotiable») and adds nothing new —
it makes the existing constraints checkable line by line.

A string that fails any rule below does not ship. When in doubt, the line is
rewritten or dropped — silence is always an acceptable answer (§2.6).

## R1 — Outcome, not habit (§2.3)

A hint points at how the person wants to feel, never prescribes a routine.
Habits belong to their own modules (water, sleep, booking).

| так можно | так нельзя |
| -- | -- |
| «Если к вечеру хочется лёгкости, могу вместе посмотреть на ужин — скажи, если интересно» | «Ешь лёгкий ужин каждый день» |
| «Ты говорила, что хочешь больше энергии днём. Если хочешь, разберём, как это связано с завтраками» | «Завтракай полноценно» |
| «Могу подсказать, что в твоих записях работает на твою цель — если хочешь» | «Пей 2 литра воды» (это модуль воды) |

## R2 — No nagging (§2.5)

Goals and records are reference, never a push-engine. Banned outright:

- «Не забывайте про цель!»
- «Вы давно не работали над {{цель}}»
- «Вы пропустили …» / «Ты не записала …» (подсчёт пропусков)
- любая форма упрёка, вины или «напоминания ради напоминания»

| так можно | так нельзя |
| -- | -- |
| «Если захочешь продолжить — я на месте» | «Ты давно не писала про еду» |
| (молчание после двух непрочитанных подсказок) | «Ты не ответила на прошлую подсказку» |

## R3 — No streaks, no counters of virtue (§2.4)

Never «N дней подряд», «дни без срыва», «серия». A week is described, not
scored.

| так можно | так нельзя |
| -- | -- |
| «На этой неделе ты записывала еду четыре раза» | «7 дней без пропусков!» |
| «В записях недели чаще всего встречается …» | «Ты держишь серию — так держать!» |

## R4 — No scales, sizes, deficits (§2.1)

Neither hints nor the report frame anything through weight, body size or
calorie arithmetic. The report may show numbers the person already sees in
the diary, but never headlines kilograms or deficits and never grades them.

| так можно | так нельзя |
| -- | -- |
| «Записей стало больше — так проще замечать, что работает на самочувствие» | «Минус 400 ккал к норме — отлично» |
| «Ты отметила, что после лёгкого ужина спится лучше» | «При таком темпе — минус килограмм к лету» |

## R5 — No diagnosis, no treatment (§2.2)

Anything that reads as a condition, a diagnosis or treatment is routed to
medicine, not coached.

| так можно | так нельзя |
| -- | -- |
| «Это уже про врача — могу помочь записаться, если хочешь» | «Судя по записям, у тебя непереносимость лактозы» |
| «Могу помочь заметить, после чего тебе легче, — и с этим уже к специалисту» | «Исключи молочное на две недели» |

## R6 — Autonomy is absolute (§2.6)

Every proactive message carries a one-tap opt-out, and ignoring a message is
a legitimate answer that the system respects by going quiet (see the shared
anti-nag mechanism). The person may have zero goals and zero reports and
never hear about it.

| так можно | так нельзя |
| -- | -- |
| «Присылать такое раз в неделю? Можно выключить одной кнопкой» | «Отключить подсказки можно в настройках профиля» (спрятанная отписка) |
| «Хочешь такие подсказки — оставь, не хочешь — нажми „Не надо“» | повторная подсказка после явного «нет» |

## R7 — Proactive only onto an explicit goal (owner decision 04.09.2026)

A proactive hint exists only if the person has explicitly chosen a goal and
the hint works directly onto it. No goal → no proactive hints, ever. The
weekly report has its own separate consent text and its own flag — it is not
covered by `NUTRITION_ENABLED`.

| так можно | так нельзя |
| -- | -- |
| (цель «больше энергии днём») «Ты записывала завтраки всю неделю — если хочешь, посмотрим, какие из них совпадают с бодрыми днями» | подсказка про еду человеку без выбранной цели |
| (цель «лучше выглядеть в открытой одежде») подсказка про самоощущение и записи | подсказка «для мотивации» без привязки к цели |

## Enforcement

- Every copy PR for these surfaces names this file and states which rules
  the new lines were checked against.
- The shared anti-nag mechanism enforces R2/R6 mechanically (frequency cap,
  one-tap unsubscribe, silence after ignored messages); this policy governs
  what the mechanism is not allowed to say even once.
- Review question for any line: «если человек прочитает это в плохой день —
  это звучит как поддержка или как упрёк?» Упрёк не проходит.
