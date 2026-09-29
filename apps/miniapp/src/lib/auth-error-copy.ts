/**
 * Копия для отказов входа — один источник на всё мини-приложение (DRF-1319 D-1).
 *
 * Слаги приходят из `apps/miniapp_api/views.py::require_init_data`:
 *  - bad_signature (401)
 *  - stale (401)        — auth_date старше 60 минут
 *  - malformed (400)    — заголовок Authorization отсутствует или испорчен;
 *                         внутри MAX это «канал не передал данные для входа»,
 *                         НЕ «пришёл гость» (решение владельца §124)
 *  - user_deleted (403) — повторный вход после удаления аккаунта
 *  - user_not_registered
 *  - server_misconfigured (500) — MAX_BOT_TOKEN / MAX_BOT_TENANT_SLUG не заданы
 *  - http_error / network — клиентские запасные варианты
 *
 * До этого модуля копия жила только в `HelloScreen`, а остальные экраны на
 * тот же 400 показывали серверную строку «missing Authorization header»
 * через `StateError` (инвентарь 1319-C: четыре экрана). Одно состояние —
 * одно имя, поэтому карта здесь, а экраны только читают.
 */

import { ApiError } from "./api";

export type ErrorCopy = {
  title: string;
  body: string;
  /** Текст кнопки повтора. Нет — кнопки нет. */
  retryLabel?: string;
};

/**
 * DRF-1893 — отказ транспорта: сервер отвечает 401 `no_init_data` на пустой,
 * испорченный, чужой или просроченный initData. Старые слаги (`malformed`,
 * `bad_signature`, `stale`) читаются так же — закэшированный бандл может
 * встретить новый сервер или наоборот. Решение владельца (раздел U):
 * Mini App работает только из MAX; повтор здесь не поможет, поэтому кнопки
 * повтора нет — только возврат в MAX.
 *
 * Заголовок — формулировка владельца; тело и кнопка — на подтверждение
 * владельцу (обращение на «вы», как во всей копии Mini App).
 */
export const OPEN_FROM_MAX_COPY = {
  title: "Открой Ayla из MAX",
  body: "Мини-приложение работает только внутри MAX. Вернитесь в чат с Ayla и откройте его оттуда.",
  action: "Вернуться в MAX",
} as const;

export const TRANSPORT_REFUSAL_SLUGS: ReadonlySet<string> = new Set([
  "no_init_data",
  "malformed",
  "bad_signature",
  "stale",
]);

export function isTransportRefusalSlug(slug: string | undefined): boolean {
  return slug !== undefined && TRANSPORT_REFUSAL_SLUGS.has(slug);
}

const OPEN_FROM_MAX: ErrorCopy = { title: OPEN_FROM_MAX_COPY.title, body: OPEN_FROM_MAX_COPY.body };

export const AUTH_ERROR_COPY: Record<string, ErrorCopy> = {
  no_init_data: OPEN_FROM_MAX,
  bad_signature: OPEN_FROM_MAX,
  stale: OPEN_FROM_MAX,
  malformed: OPEN_FROM_MAX,
  user_deleted: {
    title: "Аккаунт удалён",
    body: "Вы попросили удалить данные ранее. Чтобы восстановить профиль, напишите боту студии — мы поможем.",
  },
  user_not_registered: {
    title: "Сейчас откроем",
    body: "Создаём ваш профиль в студии — это может занять секунду. Если страница не обновится сама — нажмите кнопку.",
    retryLabel: "Попробовать снова",
  },
  server_misconfigured: {
    title: "Что-то у нас не так",
    body: "Не получилось войти из-за временной проблемы на нашей стороне. Уже разбираемся — попробуйте чуть позже.",
    retryLabel: "Попробовать снова",
  },
  http_error: {
    title: "Не удалось загрузить",
    body: "Проверьте интернет и попробуйте ещё раз.",
    retryLabel: "Попробовать снова",
  },
  network: {
    title: "Нет связи",
    body: "Mini App не получилось подключиться к интернету. Проверьте соединение и попробуйте снова.",
    retryLabel: "Попробовать снова",
  },
};

/**
 * Слаги, которые означают «вход не состоялся» — их копия обязана быть одной
 * на всех экранах. `http_error` / `network` сюда не входят: это не отказ
 * входа, а сеть, и у списковых экранов для неё своя общая фраза.
 */
export const AUTH_REFUSAL_SLUGS: ReadonlySet<string> = new Set([
  "no_init_data",
  "bad_signature",
  "stale",
  "malformed",
  "user_deleted",
  "user_not_registered",
  "server_misconfigured",
]);

export function isAuthRefusalSlug(slug: string | undefined): boolean {
  return slug !== undefined && AUTH_REFUSAL_SLUGS.has(slug);
}

const FALLBACK: ErrorCopy = {
  title: "Не удалось войти",
  body: "Попробуйте закрыть Mini App и открыть заново. Если повторится — напишите в студию.",
  retryLabel: "Попробовать снова",
};

/** Копия по слагу; неизвестный слаг — общий «не удалось войти». */
export function authErrorCopy(slug: string): ErrorCopy {
  return AUTH_ERROR_COPY[slug] ?? FALLBACK;
}

/**
 * Причина ошибки загрузки для экранов со своей копией по месту
 * (Records, блоки дашборда). До этого три одинаковых тернарника
 * `status >= 500 → server, ApiError → other, иначе network` жили в трёх
 * местах и ни один не отличал отказ входа от «прочего 4xx»: на пустой
 * `initData` человек читал «Что-то пошло не так, попробуй через минуту»
 * — а минута тут не поможет. Правило то же, что у `StateError` (D-1).
 */
export type LoadErrorReason =
  | { kind: "auth"; slug: string }
  | { kind: "server" }
  | { kind: "network" }
  | { kind: "other" };

export function loadErrorReason(e: unknown): LoadErrorReason {
  if (e instanceof ApiError) {
    if (isAuthRefusalSlug(e.slug)) return { kind: "auth", slug: e.slug };
    if (e.status >= 500) return { kind: "server" };
    return { kind: "other" };
  }
  return { kind: "network" };
}
