/**
 * Универсальные системные состояния мастерских экранов — макет DRF-1181 п.10
 * (DRF-2157, М-6).
 *
 * Один компонент и один словарь текстов на все экраны `/master/*` и `/solo/*`:
 * первая загрузка · обновление · нет данных · нет интернета · данные могли
 * устареть · ошибка с данными · нет прав · результат неизвестен · конфликт.
 * Тексты — дословно из макета; сторог на дословность — `SystemState.test.tsx`,
 * сторож на «второй словарь» в экранах — `systemStateVocabulary.test.ts`.
 *
 * Что здесь НЕ решается: смысл состояния. Экран сам знает, что у него
 * «нет данных» и когда результат «неизвестен»; компонент только рисует.
 *
 * Троттл «Проверить снова» (10 с, блок на время запроса) — не из макета:
 * решение главного окна 20.09 по readiness (`AdminReadinessScreen`) и по
 * М-4. Живёт здесь, чтобы экраны его не дублировали.
 *
 * Отказ транспорта / отказ входа внутри `load_error` — ПЕРЕНОС пути
 * `StateError` (DRF-1893 / DRF-1319), не новый обработчик: те же
 * `isTransportRefusalSlug` → `OpenFromMaxBody` и `isAuthRefusalSlug` →
 * `authErrorCopy`. Сторож 1893 (`App.openFromMax.test`) видит тот же
 * `OpenFromMaxBody`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../../lib/api";
import {
  authErrorCopy,
  isAuthRefusalSlug,
  isTransportRefusalSlug,
} from "../../lib/auth-error-copy";
import { OpenFromMaxBody } from "../OpenFromMaxScreen";

/** Не чаще раза в 10 с — как у readiness (решение главного окна 20.09). */
export const RECHECK_MIN_INTERVAL_MS = 10_000;

/** Словарь макета DRF-1181 п.10 — дословно. Константы, не props. */
export const SYSTEM_STATE_COPY = {
  refreshing: "Обновляем…",
  offline: { title: "Нет подключения", body: "Показаны последние данные" },
  stale: { title: "Расписание могло измениться" },
  load_error: {
    // Ruling владельца §61 (М-6 б): одной фразой, с точкой.
    withData: { text: "Не удалось обновить. Показаны последние данные" },
    // Ruling §61 (М-6 б/е): «Не удалось загрузить <предмет экрана>» →
    // «Попробовать снова». «Проверить снова» — только у «Проверяем результат».
    initial: { title: "Не удалось загрузить", retry: "Попробовать снова" },
    subjects: { today: "сегодняшний день", schedule: "расписание", booking: "запись" },
  },
  forbidden: { title: "Недостаточно прав", body: "Это действие недоступно" },
  pending: {
    title: "Проверяем результат",
    body: "Не удалось подтвердить, сохранилось ли изменение.",
    recheck: "Проверить снова",
  },
  conflict: { title: "Это время занято", cta: "Выбрать другое время" },
} as const;

export type SystemStateProps =
  | { kind: "loading"; lines?: number }
  | { kind: "refreshing" }
  | { kind: "empty"; text: string }
  | { kind: "offline" }
  | { kind: "stale" }
  | {
      kind: "load_error";
      err: unknown;
      onRetry: () => void;
      /** Есть что показывать — полоса «Не удалось обновить», не экран ошибки. */
      hasData?: boolean;
      /** Запрос в полёте — кнопка заблокирована. */
      busy?: boolean;
      /** Предмет экрана в заголовке: «Не удалось загрузить расписание». */
      what?: keyof typeof SYSTEM_STATE_COPY.load_error.subjects;
    }
  | { kind: "forbidden" }
  | {
      kind: "pending";
      onRecheck: () => void;
      /** Текст тела другого макета (DRF-1185: «Не удалось получить актуальное состояние записи.»). */
      body?: string;
      busy?: boolean;
    }
  | { kind: "conflict"; onPickAnother: () => void };

export function SystemState(props: SystemStateProps) {
  switch (props.kind) {
    case "loading":
      return <LoadingSkeleton lines={props.lines ?? 3} />;
    case "refreshing":
      return (
        <div className="system-state system-state--inline" role="status">
          <span className="system-state__dot" aria-hidden="true" />
          <span className="system-state__title">{SYSTEM_STATE_COPY.refreshing}</span>
        </div>
      );
    case "empty":
      return (
        <div className="system-state system-state--card" role="status">
          <span className="system-state__icon" aria-hidden="true">
            <IconCalendar />
          </span>
          <p className="system-state__body">{props.text}</p>
        </div>
      );
    case "offline":
      return (
        <Banner
          icon={<IconOffline />}
          title={SYSTEM_STATE_COPY.offline.title}
          body={SYSTEM_STATE_COPY.offline.body}
        />
      );
    case "stale":
      return <Banner icon={<IconClock />} title={SYSTEM_STATE_COPY.stale.title} />;
    case "load_error":
      return <LoadError {...props} />;
    case "forbidden":
      return <Forbidden />;
    case "pending":
      return <Pending onRecheck={props.onRecheck} body={props.body} busy={props.busy} />;
    case "conflict":
      return (
        <div className="system-state system-state--card system-state--danger" role="alert">
          <span className="system-state__icon" aria-hidden="true">
            <IconWarning />
          </span>
          <p className="system-state__title">{SYSTEM_STATE_COPY.conflict.title}</p>
          <button type="button" className="system-state__link" onClick={props.onPickAnother}>
            {SYSTEM_STATE_COPY.conflict.cta}
          </button>
        </div>
      );
    default:
      return null;
  }
}

// ----------------------------------------------------------------------------
// Pieces
// ----------------------------------------------------------------------------

function LoadingSkeleton({ lines }: { lines: number }) {
  return (
    <div className="system-state system-state--skeleton" role="status" aria-busy="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="m-card m-card--skel">
          <div className="skeleton" style={{ width: i % 2 ? "70%" : "55%", height: "1.1em" }} />
          <div
            className="skeleton"
            style={{ width: i % 2 ? "45%" : "65%", height: "0.9em", marginTop: 8 }}
          />
        </div>
      ))}
    </div>
  );
}

function Banner({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="system-state system-state--banner" role="status">
      <span className="system-state__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="system-state__text">
        <span className="system-state__title">{title}</span>
        {body ? <span className="system-state__body">{body}</span> : null}
      </span>
      {action}
    </div>
  );
}

function Forbidden() {
  return (
    <div className="system-state system-state--card" role="alert">
      <span className="system-state__icon" aria-hidden="true">
        <IconLock />
      </span>
      <p className="system-state__title">{SYSTEM_STATE_COPY.forbidden.title}</p>
      <p className="system-state__body">{SYSTEM_STATE_COPY.forbidden.body}</p>
    </div>
  );
}

function LoadError({
  err,
  onRetry,
  hasData = false,
  busy = false,
  what,
}: Extract<SystemStateProps, { kind: "load_error" }>) {
  if (err instanceof ApiError) {
    // Перенос пути StateError (DRF-1893): отказ транспорта — возврат в MAX.
    if (isTransportRefusalSlug(err.slug)) return <OpenFromMaxBody />;
    // Перенос пути StateError (DRF-1319): отказ входа — та же копия, что на HelloScreen.
    if (isAuthRefusalSlug(err.slug)) {
      const copy = authErrorCopy(err.slug);
      return (
        <div className="system-state system-state--card system-state--danger" role="alert">
          {copy.title ? <p className="system-state__title">{copy.title}</p> : null}
          <p className="system-state__body">{copy.body}</p>
        </div>
      );
    }
    if (err.status === 403) return <Forbidden />;
  }
  const { initial, subjects, withData } = SYSTEM_STATE_COPY.load_error;
  if (hasData) {
    return (
      <Banner
        icon={<IconCloudOff />}
        title={withData.text}
        action={
          <button
            type="button"
            className="system-state__link"
            onClick={onRetry}
            disabled={busy}
          >
            {initial.retry}
          </button>
        }
      />
    );
  }
  const title = what ? `${initial.title} ${subjects[what]}` : initial.title;
  return (
    <div className="system-state system-state--card system-state--danger" role="alert">
      <span className="system-state__icon" aria-hidden="true">
        <IconCloudOff />
      </span>
      <p className="system-state__title">{title}</p>
      <button type="button" className="btn-secondary system-state__cta" onClick={onRetry} disabled={busy}>
        {initial.retry}
      </button>
    </div>
  );
}

function Pending({
  onRecheck,
  body,
  busy = false,
}: {
  onRecheck: () => void;
  body?: string;
  busy?: boolean;
}) {
  const [cooldown, setCooldown] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const click = useCallback(() => {
    if (busy || cooldown) return;
    setCooldown(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCooldown(false), RECHECK_MIN_INTERVAL_MS);
    onRecheck();
  }, [busy, cooldown, onRecheck]);
  return (
    <div className="system-state system-state--card system-state--accent" role="status">
      <span className="system-state__icon" aria-hidden="true">
        <IconQuestion />
      </span>
      <p className="system-state__title">{SYSTEM_STATE_COPY.pending.title}</p>
      <p className="system-state__body">{body ?? SYSTEM_STATE_COPY.pending.body}</p>
      <button
        type="button"
        className="btn-secondary system-state__cta"
        onClick={click}
        disabled={busy || cooldown}
      >
        {SYSTEM_STATE_COPY.pending.recheck}
      </button>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Icons — line icons per mockup, decorative
// ----------------------------------------------------------------------------

const ICON = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8 } as const;

function IconCalendar() {
  return (
    <svg {...ICON}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}
function IconClock() {
  return (
    <svg {...ICON}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
function IconOffline() {
  return (
    <svg {...ICON}>
      <path d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8.5 15.5a5 5 0 0 1 7 0" />
      <path d="M3 3l18 18" />
      <circle cx="12" cy="19" r="1" fill="currentColor" />
    </svg>
  );
}
function IconCloudOff() {
  return (
    <svg {...ICON}>
      <path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.3 1.5A3.5 3.5 0 0 0 7 18z" />
      <path d="M4 4l16 16" />
    </svg>
  );
}
function IconLock() {
  return (
    <svg {...ICON}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}
function IconQuestion() {
  return (
    <svg {...ICON}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01" />
    </svg>
  );
}
function IconWarning() {
  return (
    <svg {...ICON}>
      <path d="M12 3l10 18H2L12 3z" />
      <path d="M12 10v4M12 17h.01" />
    </svg>
  );
}
