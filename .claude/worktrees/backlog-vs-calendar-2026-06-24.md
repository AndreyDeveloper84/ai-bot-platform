# Backlog vs Calendar — реальное сравнение

Сегодня **2026-06-24**, пилот **2026-07-15** → осталось **21 день** (15 рабочих).

## 1. Velocity reality check

| Метрика | Значение | Сигнал |
|---|---|---|
| Последний production-merge на dev | **2026-06-11** (#1042 Redis settings hotfix) | **13 дней без merge** |
| PR #1015 (State doc) open | **20 дней**, CI green, ждёт tech-lead sign-off | заморожен |
| PR #1041 (booking flip — это P0/G1) open | **14 дней**, CI green, mergeable, no review decision | заморожен |
| PR #1040 (M0 MAX provisioning) open | **14 дней**, CI green | заморожен |
| Assignees на #1016/#1017/#1018/#1019/#1020/#1026/#1034 | **0/7** — никто не взял | стримы не запустились |

**Перевод:** проект де-факто стоит 2 недели. Все три ключевых PR ждут tech-lead — не CI, не code review, не блокеров.

---

## 2. Estimate vs календарь (источник — §7 + §8 MVP doc)

```
            T-21d  T-14d  T-7d   PILOT
            (now)                (2026-07-15)
            │      │      │      │
M0    ────► ▓▓▓▓▓                            3-5 дней (+ MAX external lead time ?)
FOUND ────► ░░░░░░░░░▓▓▓▓                   1-2 недели
P0    ────► .........▓▓▓▓▓▓▓▓▓▓▓▓           2-3 недели (depends FOUND)
P1    ────► ............▓▓▓▓▓▓▓             1-1.5 недели (∥ P2)
P2    ────► ............▓▓▓▓▓▓▓             1-1.5 недели (∥ P1)
P3    ────► .....................▓▓▓▓▓      1 неделя (depends P0+P1+P2)
            └──────── 4-6 недель ────────┘
                                          └─► ① Technical Go-Live
                                              ≈ 2026-07-22 .. 2026-08-05
```

**Gap: 1-3 недели сверх календаря пилота, даже если стримы стартанут СЕГОДНЯ параллельно.**

---

## 3. Posted PRs vs Phase mapping

| Phase | Issue | PR in-flight | Open since | CI | Risk |
|---|---|---|---|---|---|
| M0 ops/provisioning | #1040 | ✅ PR #1040 (runbook + webhook fix) | 14d | 🟢 | mergeable, ждёт sign-off; внешний lead time MAX app registration не оценён |
| FOUNDATION REST client | **#1016** | ❌ none | — | — | **Не начато**. ~1-2 нед чистого времени |
| P0 reground + walk-in | **#1017** + **PR #1041** (flip) | flip готов; walk-in ❌ | flip 14d | 🟢 | flip готов к merge; walk-in (G5) не начат — double-booking risk если не успеть |
| P1 cross-tenant marketplace | **#1018** | ❌ none | — | — | **Не начато**. ~1-1.5 нед |
| P2 tenant-less bot | **#1019** + #1026 (seam) | ❌ none | — | — | **Не начато**. ~1-1.5 нед |
| P3 discovery→booking handoff | **#1020** | ❌ none | — | — | depends P0+P1+P2 |
| Доп. PR-3 поддержка | #1034 (health-screening grounding) | OPEN | — | — | gate для PR #1041 |

**Вывод по PRs:** только M0 и P0-flip близки к мерджу. Остальные **5 из 7 фаз даже не имеют первого PR**.

---

## 4. Параллельные стримы — кто на критическом пути

По памяти (`project_ayla_active_streams`): W1=Delta, W2=Epsilon, W3=Zeta, W4=coordinator, Alpha=Ayla djangoproject, Gamma=bot-platform contracts/events.

**Распределение для ①:**
- M0 (#1040) → **ops + Alpha** (Ayla djangoproject) для подтверждения REST endpoints
- FOUNDATION (#1016) → **bot-side stream** + **Alpha** (cross-repo: bot строит client, Alpha экспонирует/подтверждает endpoints + `TenantUserRelationship` grant-on-first-booking)
- P0 (#1017) → **bot-side stream** (reground skill resolvers) + **Alpha** (walk-in create path)
- P1 (#1018) → **bot-side stream** (новый `apps/marketplace/`)
- P2 (#1019) → **bot-side stream** (ingress + sentinel BotUser + decorator refactor)
- P3 (#1020) → **bot-side stream** (depends P0+P1+P2)

**Реальная нагрузка:** ~5 из 6 phases требуют bot-side stream → **одна команда в реальности не справится за 21 день параллельно**, даже если все стримы свободны.

---

## 5. Pre-deploy-lock blockers — отдельный календарь

| Issue | Что | Внешняя зависимость | Estimate |
|---|---|---|---|
| #947 | Profile cross-border disclosure legal review | **юрист** | unknown lead time |
| #956 | 152-ФЗ consent server-side audit trail | bot + Alpha | ~3-5 дней |
| #949 | SUPPORT_DEEPLINK на реальный канал | ops | ~1 день |
| #500 | STRICT_TENANT_REFUSE pre-flip operator ceilings (PEL alert, rate budget, audit baseline, alert dedup) | ops + W3 | ~2-3 дня (готовится с 2026-05-21) |
| #560 | Q12-α status exclude→allow-list | bot | ~1-2 дня |
| #478 | Q12-α billing founder-ACK 5 edge cases | **founder decision** | блокировано — нет ACK |
| #246 | User.tenant_id → TenantUserRelationship миграция | merged via #154; остался #716 fix | ~1-2 ч |

**#947 и #478** — внешние блокеры (юрист, founder). Без них нельзя catapult в пилот.

---

## 6. Возможные сценарии для встречи дедлайна

| Сценарий | Что отрезаем | Реалистичность 2026-07-15 |
|---|---|---|
| **A. Ship full ① как описано** | ничего | ❌ невозможно при 21 дне + 2-недельной заморозке |
| **B. Single-tenant пилот** (откатить marketplace decision 2026-06-04) | P1+P2+P3 cut → 1 салон в Пензе | ✅ возможно: M0+FOUND+P0+walk-in (~2-3 нед) |
| **C. Двухсалонный пилот без cross-tenant** | P1+P3 cut, P2 cut, ручной выбор салона | ⚠ грязно но возможно: M0+FOUND+P0+P2-lite |
| **D. Move pilot date** | ничего | ✅ календарь даст 4-6 нед; ← рекомендация если marketplace = strict requirement |
| **E. Live с only flip (#1041) + #1040** | вырезать walk-in, всё кроме М0+PR #1041 | ⚠ **double-booking risk** (G5 не закрыт) — опасно |

---

## 7. Что нужно решить сегодня (founder/tech-lead level)

1. **Merge gate**: PRs #1015 / #1040 / #1041 ждут sign-off 14-20 дней. **Это и только это блокирует разогнаться.** Один час review → unfreeze.
2. **Scope decision**: marketplace из P1-P3 в пилоте остаётся или режется?
   - Если остаётся → **пилот двигается** к ≥2026-08-05.
   - Если режется до single-tenant → пилот 2026-07-15 реалистичен (сценарий B).
3. **#947 (cross-border legal) — где юрист?** Это hard external blocker.
4. **#478 (Q12-α billing founder-ACK)** — ждёт твоего решения.
5. **MAX app registration external lead time** — на сколько дней? Это влияет на M0.

---

## TL;DR

**Backlog говорит:** 4-6 недель критического пути на ①.
**Календарь даёт:** 3 недели.
**Реальная velocity:** 0 merges в P0-critical работе за последние 14 дней — стримы простаивают, ожидая sign-off на already-CI-green PRs.

**Главный bottleneck сейчас не код, а review-throughput tech lead.** Три PR (#1015 + #1040 + #1041) разблокируют ~70% пилотного scope.
