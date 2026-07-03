# Agent Operating Rules — MVP streams (2026-07-02)

> Обязательно для каждого код-агента (Claude/Codex) в любом стриме. Вставлять ссылку/выжимку в каждый запускающий промпт. Источник дисциплины: CLAUDE.md + memory (`feedback_pr_workflow_code_reviewer`, `feedback_h3_waiver_pattern`, `feedback_severity_discipline_rubric`, `project_worktrees_per_stream`, `feedback_parallel_agent_branch_race`, `feedback_ci_fail_fast_masks_mypy`).

## A. Написание кода
1. **Read-first.** Сначала прочитать релевантный код, потом менять. Match surrounding style (именование, комментарии, идиомы). Никаких drive-by рефакторингов.
2. **Минимальный диф.** Только то, что просит задача. Нашёл лишнюю работу → заведи FOLLOW_UP issue, не делай в этом PR.
3. **TDD где уместно:** тест до/вместе с реализацией; тесты зелёные перед PR.
4. **Строго allowed/forbidden dirs** своего стрима. Тронул чужой файл — неверная задача.
5. **Freeze rule:** нет новых transactional-доменов в bot-platform; нет новых фич; **нет флипов флагов** (`BOOKING_VIA_AYLA_REST`, `OUTBOX_EXTERNAL_DELIVERY_TOPICS`, `CERTIFICATE_PAYMENT_ENABLED`, `GLOBAL_BOT_ONBOARDING` остаются default); нет изменений контракта `booking_client` без approval.
6. **New-scope правило:** любое увеличение pilot-scope сначала фиксируется в `MVP_DELIVERY_TRACKER` как New Scope SP, иначе не берётся.
7. **mypy:** прогнать `uv run mypy` локально перед PR на ВСЕХ изменённых/добавленных файлах — **включая тесты** (CI гоняет `mypy apps config tests` по всему дереву; проверка только source маскирует ошибки в тестах). Ловили дважды: #1067 добавил контракт-тест с `get_field(...).max_length` (`ForeignObjectRel` не имеет `max_length` → `union-attr`) — покраснил dev и ВСЕ ветки от него, пока #1073 не пофиксил. Особое внимание: novel-вызовы внешних либ (redis-py/httpx/celery) и Django `_meta`/`get_field()` (union-типы → `getattr(field, "attr", None)`).
8. **Не обходить pre-commit** (`--no-verify`) без явного разрешения tech-lead.

## B. Git / ветки (parallel-agent safe)
1. **Один worktree на стрим** (sibling-директория). Ветка на стрим (имя — в промпте).
2. **Selective staging:** называть файлы явно. **НИКОГДА `git add .` / `-A`** (в дереве параллельные WIP).
3. **Conventional Commits.** Хвост коммита: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
4. **Push:** `git push origin HEAD:refs/heads/<branch>` (устойчиво к branch-race).
5. **PR → `dev`, НИКОГДА `main`.** Тело PR: acceptance-чеклист (из issue) + test plan + out-of-scope + `Closes #NNN` + ID агентов-ревьюеров.

## C. Ревью (обязательно)
1. **Code Reviewer agent на каждый PR-диф** — §H.3 double-pass: **friendly, затем adversarial** (adversarial-фрейминг = несущая защита; empirically ловит то, что friendly пропускает).
2. **4-tier severity:** MUST_FIX_PRE_MERGE / MUST_FIX_PRE_PILOT / FOLLOW_UP / NICE_TO_HAVE. Блокируют merge только 7 категорий: exploitable-today · billing miscompute · cross-tenant leak · data corruption · unsafe migration · JWT bypass · hard ADR-0009 violation.
3. **Wave 1 = contract/safety-critical** (external-API + migration триггеры §H.3) → **финальное ревью tech-lead (founder) ДО merge**; self-review агента не даёт авто-merge.
4. **PRE_PILOT / FOLLOW_UP** находки → заводить как issues, merge не блокируют.
5. **ShiroPy (фронт): всю работу ревьюим мы** — self-merge запрещён.

## D. Definition of Done (на PR)
- DoD задачи выполнен (из AGENT_QUEUE / issue).
- Тесты добавлены и зелёные; CI зелёный.
- Свой friendly+adversarial ревью проведён, блокеры устранены.
- Тело PR полное; `Closes #NNN`.

## E. Worktree-карта Волны 1A
| Стрим | Worktree (директория запуска Claude) | Branch |
|---|---|---|
| S0-A | `C:\Users\user\PycharmProjects\ai-bot-platform-s0a` | `fix/s0a-ayla-url-auth` |
| S0.5 | `C:\Users\user\PycharmProjects\ai-bot-platform-s05` | `fix/s05-event-id-width` |

Каждый агент работает ТОЛЬКО в своём worktree. Пуш — `git push origin HEAD:refs/heads/<branch>`.
