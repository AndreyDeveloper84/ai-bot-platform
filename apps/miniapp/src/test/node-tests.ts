/**
 * Тесты, которым НЕ НУЖЕН браузер — список явный и конечный (DRF-2389).
 *
 * ### Зачем
 *
 * Полный прогон Mini App тратит на построение окружения больше, чем на сами
 * тесты: замер 24.09.2026 на 209 файлах — окружение 1151,5 с рабочего
 * времени (60 %) против 365,0 с на тестах (19 %). Файлы ниже не касаются
 * DOM вовсе, но платили за jsdom наравне со всеми.
 *
 * Замер по этому списку: под jsdom окружение 261,2 с, под node — **20 мс**.
 * Это 23 % всей стоимости окружения, снятые **без изменения изоляции**:
 * каждый файл по-прежнему получает своё свежее окружение, просто дешёвое.
 *
 * ### Почему список, а не шаблон по каталогу
 *
 * Потому что шаблон уже ошибся. Первая попытка отобрала файлы поиском по
 * признакам DOM (`document.`, `render(`, `window.`) и дала 36 файлов в
 * `lib`. Прогон под node показал, что **три из них DOM всё-таки трогают** —
 * через вспомогательные функции, где признака в тексте теста нет:
 *
 * * `personal-data.test.ts` — `triggerDownload()` создаёт якорь `<a>`;
 * * `returnToChat2266.test.ts`, `returnToChatRemembered2268.test.ts` —
 *   мост возврата в чат ходит в `window`.
 *
 * Шаблон по имени папки положил бы эти три в node-набор и сломал бы их.
 * Поэтому правило: **в список попадают только файлы, которые прогнаны под
 * node и зелены**. Пополнение — через тех-лида, и только после прогона.
 *
 * ### Что будет, если файл попадёт сюда ошибочно
 *
 * Он покраснеет сразу и громко: в node-окружении нет ни `document`, ни
 * `window`. Это не тихий отказ — именно поэтому список безопаснее, чем
 * кажется, и именно поэтому его нельзя пополнять «на глаз».
 */

/** Пути от корня `apps/miniapp`. Отсортированы; повторов нет (сторож). */
export const NODE_ENVIRONMENT_TESTS: readonly string[] = [
  "src/App.legacyRoutes.test.ts",
  "src/App.recommendationRoute1769.test.tsx",
  "src/components/master/systemStateVocabulary.test.ts",
  "src/lib/admin-api.staff-role.test.ts",
  "src/lib/admin-tabs.test.ts",
  "src/lib/booking-draft.test.ts",
  "src/lib/booking-outcome.test.ts",
  "src/lib/booking-status.test.ts",
  "src/lib/booking-time.test.ts",
  "src/lib/claims-debt.test.ts",
  "src/lib/claims.test.ts",
  "src/lib/customer-booking.test.ts",
  "src/lib/customer-memory.test.ts",
  "src/lib/customer-profile.test.ts",
  "src/lib/customer-records.test.ts",
  "src/lib/dev-init-data.build.test.ts",
  "src/lib/diary-days.test.ts",
  "src/lib/food-scanner.scan.test.ts",
  "src/lib/identity.test.ts",
  "src/lib/image-crop.test.ts",
  "src/lib/master-api.bookings2155.test.ts",
  "src/lib/master-billing.test.ts",
  "src/lib/masterDateFormat.test.ts",
  "src/lib/max-sdk.test.ts",
  "src/lib/noBareCloseApp.guard.test.ts",
  "src/lib/open-from-max.test.ts",
  "src/lib/payment-status.test.ts",
  "src/lib/payments.test.ts",
  "src/lib/plan-lite.test.ts",
  "src/lib/rating.test.ts",
  "src/lib/recommendation-wire-contract.test.ts",
  "src/lib/restore-window.test.ts",
  "src/lib/salon-readiness.test.ts",
  "src/lib/salon-today.test.ts",
  "src/lib/salonOwnerHint.test.ts",
  "src/lib/saved-meals.test.ts",
  "src/no-person-names.guard.test.ts",
  "src/screens/admin/adminBackDeclared2368.test.tsx",
  "src/screens/backContract.test.ts",
  "src/screens/masterDoors2150.build.test.ts",
  "src/screens/masterLinksResolve.guard.test.ts",
  "src/screens/navNeverHidden.guard.test.ts",
  "src/screens/quickActionLabels2266.build.test.ts",
  "src/screens/scheduleFixturesFollowClock.guard.test.ts",
  "src/state/booking.test.ts",
  "src/styles/cssVarsDeclared.build.test.ts",
  "src/test/nodeEnvironmentList.guard.test.ts",
];
