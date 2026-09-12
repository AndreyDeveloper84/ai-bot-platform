/**
 * Pilot feature flags (pilot 2026-08-15, W4).
 *
 * `STUB_SURFACES_ENABLED` — DEV-only stub-backed surfaces and sections.
 *
 * The pilot honesty rule (orchestrator): NOTHING fake in prod — a hidden
 * surface is more honest than invented data, and a real surface must
 * never fall back to a stub. In production builds
 * (`import.meta.env.DEV === false`, statically replaced — the stub
 * branch tree-shakes out) a gated surface renders
 * `PilotComingSoonScreen` instead; DEV builds keep the stubs for local
 * development and QA.
 *
 * # Кто гейтится сегодня: НИКТО
 *
 * Флаг остался без потребителей — и это состояние, а не забытая
 * уборка, поэтому оно записано:
 *
 * * `/customer/main` (домашний экран) — гейт снят с DRF-1546. Решение
 *   владельца §24.2: снимать после того, как (1) подключены настоящие
 *   цели и (2) `weekly_progress` либо подключён, либо честно скрыт.
 *   Оба условия были выполнены раньше — DRF-1476 подключила цели, а
 *   бэкенд опускает `weekly_progress` вместо нулей. Экран стоял
 *   закрытым только потому, что его никто не открыл, и человек на
 *   «Главной» видел список записей вместо главной.
 *   Блоки без живой ручки с главной СНЯТЫ, а не заглушены (§33 /
 *   DRF-1543): «📸 Сфотографируй еду» (ручек `customer/food/*` не
 *   существует — 404 на боевом контуре) и строка «Добрать белок»
 *   (бэкенд не шлёт `protein_target_g` и источника под него не имеет).
 * * `/customer/catalog` — этим флагом не гейтился уже к моменту
 *   DRF-1546, хотя прежний комментарий здесь так утверждал.
 * * `CustomerProfileScreen` — с DRF-1475 (часть Б, решение владельца
 *   05.09) имя и маркетинговое согласие читают/пишут реальный
 *   `/customer/me`, а секции без backend («Подсказки от Ayla»,
 *   «Хранение данных») убраны из рендера до DRF-1520.
 * * `/customer/records` — не гейтился намеренно: он получил настоящие
 *   данные как phase 3 item 2.
 *
 * Флаг и `PilotComingSoonScreen` оставлены на месте: механизм честного
 * сокрытия рабочий и понадобится следующей поверхности без ручек.
 * Удалять его — отдельная уборка, а не побочный эффект этой правки.
 */
export const STUB_SURFACES_ENABLED = import.meta.env.DEV;

/**
 * Полка рекомендаций (WHAT + WHY) — OD-PILOT-9 (`docs/OWNER_DECISIONS_2026-09-12.md`
 * §3): первый Controlled Pilot запускается БЕЗ полки; включение — через
 * feature flag / pilot cohort после Stage 2 gate (human review mappings,
 * `VERIFIED > 0`, candidate-level Safety DRF-1627, единственный
 * Recommendation Authority, grounded WHY, честные empty states,
 * `recommendation_id` attribution).
 *
 * Выключено по умолчанию. `VITE_RECOMMENDATION_SHELF=1` включает. Функция,
 * а не константа: читается при рендере, чтобы тест мог включить флаг
 * на один случай без перезагрузки модуля.
 * Пока флаг выключен, блок не рендерится даже если резолвер прислал
 * picks с WHY — data-gate (25.08) остаётся вторым условием, не первым.
 */
export function recommendationShelfEnabled(): boolean {
  return (import.meta.env.VITE_RECOMMENDATION_SHELF as string | undefined) === "1";
}
