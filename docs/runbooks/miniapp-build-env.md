# Runbook: Mini App — переменные сборки (VITE_*)

> Status: **complete**
> Last exercised: _29.09.2026 — замер бандла пилота и локальная `vite build` (DRF-2654)_
> Owner: _тот, кто выкладывает dev / пилот_

## Purpose

Какие переменные мини-приложение читает **при сборке**, где их задают для
выкладки и что будет, если не задать. Значения `VITE_*` вшиваются в бандл при
`vite build`; после выкладки их не поменять без новой сборки.

## Переменные

| переменная | где читается | production | dev (`npm run dev`) |
|---|---|---|---|
| `VITE_SUPPORT_DEEPLINK` | `src/lib/customer-profile.ts` (`SUPPORT_DEEPLINK`) — ссылка «поддержка» в шторках личных данных | **задавать обязательно**: не задана или пуста → в бандл вшивается заглушка `https://max.me/aylasupport` (так и собран пилот 29.09) | не обязательна: пусто → та же заглушка |
| `VITE_DEV_*` (`DEV_INIT_DATA`, `DEV_START_PARAM`, `DEV_BYPASS_USER_ID`, `DEV_BYPASS_TENANT_SLUG`) | `src/lib/max-sdk.ts`, `src/lib/dev-bypass.ts` | не используются: ветки срезаются, `import.meta.env.DEV === false` | локальный вход без MAX |
| `VITE_RECOMMENDATION_SHELF` | `src/lib/feature-flags.ts` | `"1"` — полка рекомендаций включена, иначе выключена | так же |

## Где задаётся для выкладки

- **Пилот / dev (`deploy-dev.yml`, шаг «Build Mini App on the runner»)** —
  сегодня шаг **не передаёт в сборку ни одной `VITE_*`**, поэтому пилот собран
  с заглушкой. Передавать её предлагается из переменной репозитория GitHub
  (*Settings → Secrets and variables → Actions → Variables* →
  `VITE_SUPPORT_DEEPLINK`) — это отдельная правка вместе с отказом сборки без
  переменной, она ждёт решения владельца (значение канала и сама переменная).

## Step-by-step procedure

1. Узнать у владельца настоящий адрес канала поддержки (вопрос в реестре).
2. Задать `VITE_SUPPORT_DEEPLINK` в переменных Actions репозитория (делает
   владелец) и передать её в шаг сборки выкладки.
3. Запустить выкладку (`deploy-dev`).
4. Проверка на машине: в `apps/miniapp/dist/assets/*.js` есть заданный адрес и
   **нет** `max.me/aylasupport`:
   `grep -l '<адрес>' dist/assets/*.js` → ≥ 1 файл;
   `grep -l 'max.me/aylasupport' dist/assets/*.js` → 0 файлов.

## Если не задать

Сегодня — сборка проходит молча, и кнопка «поддержка» ведёт на заглушку из
кода. После правки с отказом сборки (ждёт решения владельца) выкладка будет
краснеть на шаге сборки с текстом `DRF-2654: production build needs
VITE_SUPPORT_DEEPLINK`.
