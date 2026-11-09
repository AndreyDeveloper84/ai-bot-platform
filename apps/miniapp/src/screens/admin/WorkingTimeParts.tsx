/**
 * Общие части экранов «Рабочее время мастеров» (DRF-2936, макет DRF-2865).
 *
 * Не экран: шапка, карточка состояния в раскладке макета и значки. Слова
 * системных состояний — из общего словаря (`SYSTEM_STATE_COPY`), раскладка —
 * как на постере: значок крупно, заголовок, тело, кнопки столбиком.
 */
import { useState } from "react";
import type { ReactNode } from "react";

import { SYSTEM_STATE_COPY, SystemState } from "../../components/OperationalSystemState";
import { MasterPhoto } from "../../components/MasterPhoto";
import { ApiError } from "../../lib/api";
import { isAuthRefusalSlug, isTransportRefusalSlug } from "../../lib/auth-error-copy";
import { initialsOf } from "../../lib/avatar-sheet";

export function WtAvatar({
  name,
  photoUrl,
  size = "md",
}: {
  name: string;
  photoUrl: string | null | undefined;
  size?: "sm" | "md";
}) {
  return (
    <span className={`wt-avatar wt-avatar--${size}`} aria-hidden="true">
      <MasterPhoto
        src={photoUrl}
        alt=""
        className="wt-avatar__img"
        fallback={<span className="wt-avatar__initials">{initialsOf(name)}</span>}
      />
    </span>
  );
}

/**
 * Шапка вложенного экрана: «‹», заголовок по центру. На кадре 2 под
 * заголовком — мастер (аватар, имя, специализация); на кадрах 3–9 — дата, и
 * мастер подписью: правка дня не должна случиться не у того человека.
 */
export function WtHeader({
  title,
  subtitle,
  onBack,
  master,
}: {
  title?: string;
  subtitle?: string | null;
  onBack?: () => void;
  master?: { name: string; specialization: string; photoUrl: string | null } | null;
}) {
  return (
    <header className="wt-header">
      {onBack ? (
        <button type="button" className="wt-header__back" aria-label="Назад" onClick={onBack}>
          <IconBack />
        </button>
      ) : (
        <span className="wt-header__side" aria-hidden="true" />
      )}
      {master ? (
        <span className="wt-header__master">
          <WtAvatar name={master.name} photoUrl={master.photoUrl} size="sm" />
          <span className="wt-header__master-text">
            <h1 className="wt-header__title wt-header__title--start">{master.name}</h1>
            {master.specialization ? (
              <span className="wt-header__subtitle">{master.specialization}</span>
            ) : null}
          </span>
        </span>
      ) : (
        <span className="wt-header__center">
          <h1 className="wt-header__title">{title}</h1>
          {subtitle ? <span className="wt-header__subtitle">{subtitle}</span> : null}
        </span>
      )}
      <span className="wt-header__side" aria-hidden="true" />
    </header>
  );
}

export type WtTone = "neutral" | "accent" | "success" | "warning" | "danger";

/** Карточка состояния в раскладке макета (кадры 4, 7–10). */
export function WtStateCard({
  tone,
  icon,
  title,
  body,
  role = "status",
  children,
}: {
  tone: WtTone;
  icon: ReactNode;
  title: string;
  body?: string | null;
  role?: "status" | "alert";
  children?: ReactNode;
}) {
  return (
    <section className={`wt-state wt-state--${tone}`} role={role}>
      <span className="wt-state__icon" aria-hidden="true">
        {icon}
      </span>
      <h2 className="wt-state__title">{title}</h2>
      {body ? <p className="wt-state__body">{body}</p> : null}
      {children ? <div className="wt-state__actions">{children}</div> : null}
    </section>
  );
}

/**
 * Ошибка загрузки — общими словами («Не удалось загрузить рабочие часы»,
 * «Попробовать снова»), раскладкой макета. Отказ входа, транспорта и прав —
 * общий путь `SystemState`: там уже есть свои слова и свои кнопки.
 */
export function WtLoadError({
  err,
  busy = false,
  onRetry,
}: {
  err: unknown;
  busy?: boolean;
  onRetry: () => void;
}) {
  if (
    err instanceof ApiError &&
    (isTransportRefusalSlug(err.slug) || isAuthRefusalSlug(err.slug) || err.status === 403)
  ) {
    return <SystemState kind="load_error" what="workingHours" err={err} busy={busy} onRetry={onRetry} />;
  }
  const { initial, subjects } = SYSTEM_STATE_COPY.load_error;
  return (
    <WtStateCard tone="danger" role="alert" icon={<IconAlert />} title={`${initial.title} ${subjects.workingHours}`}>
      <button type="button" className="btn-secondary wt-btn" disabled={busy} onClick={onRetry}>
        {initial.retry}
      </button>
    </WtStateCard>
  );
}

// ---------------------------------------------------------------------------
// Значки макета — контурные, 1.8 px, цвет от текста.
// ---------------------------------------------------------------------------

function Svg({ size = 24, children }: { size?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export function IconBack() {
  return (
    <Svg size={20}>
      <path d="M15 5l-7 7 7 7" />
    </Svg>
  );
}

export function IconChevron() {
  return (
    <Svg size={18}>
      <path d="M9 5l7 7-7 7" />
    </Svg>
  );
}

export function IconSearch() {
  return (
    <Svg size={18}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4 4" />
    </Svg>
  );
}

/** «Мишень» у выбранного мастера (кадр 1). */
export function IconTarget() {
  return (
    <Svg size={18}>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function IconPlus() {
  return (
    <Svg size={20}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function IconPencil() {
  return (
    <Svg size={20}>
      <path d="M4 20h4L19 9l-4-4L4 16v4z" />
      <path d="M13.5 6.5l4 4" />
    </Svg>
  );
}

export function IconTrash() {
  return (
    <Svg size={20}>
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
    </Svg>
  );
}

export function IconCalendar() {
  return (
    <Svg size={40}>
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
      <path d="M14 15.5l1.5 1.5 3-3" />
    </Svg>
  );
}

export function IconPeople() {
  return (
    <Svg size={40}>
      <circle cx="12" cy="8" r="3" />
      <circle cx="5.5" cy="9.5" r="2.2" />
      <circle cx="18.5" cy="9.5" r="2.2" />
      <path d="M6.5 19c.5-3 2.8-5 5.5-5s5 2 5.5 5M1.5 18c.3-2 1.8-3.6 3.9-3.8M22.5 18c-.3-2-1.8-3.6-3.9-3.8" />
    </Svg>
  );
}

export function IconAlert() {
  return (
    <Svg size={40}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.5h.01" />
    </Svg>
  );
}

export function IconWarning() {
  return (
    <Svg size={40}>
      <path d="M12 3.5l9.5 16.5h-19L12 3.5z" />
      <path d="M12 10v4.5M12 17.5h.01" />
    </Svg>
  );
}

export function IconCheck() {
  return (
    <Svg size={40}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.8 2.8L16.5 9.5" />
    </Svg>
  );
}

export function IconHourglass() {
  return (
    <Svg size={40}>
      <path d="M7 3h10M7 21h10M8 3c0 4 8 5 8 9s-8 5-8 9M16 3c0 4-8 5-8 9s8 5 8 9" />
    </Svg>
  );
}

export function IconInfo() {
  return (
    <Svg size={20}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </Svg>
  );
}

/**
 * Меню «⋮» «Расписания» — ровно один пункт, «Рабочее время» (DRF-1235). Мастер,
 * открытый в «Расписании», уходит в список отмеченным (`state.masterId`).
 */
export function WorkingTimeMenu({
  masterId,
  onOpen,
}: {
  masterId: string | null;
  onOpen: (state: { masterId: string } | undefined) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="wt-menu">
      <button
        type="button"
        className="wt-menu__trigger"
        aria-label="Ещё"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <IconMore />
      </button>
      {open ? (
        <div className="wt-menu__list" role="menu">
          <button
            type="button"
            role="menuitem"
            className="wt-menu__item"
            onClick={() => {
              setOpen(false);
              onOpen(masterId ? { masterId } : undefined);
            }}
          >
            Рабочее время
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function IconMore() {
  return (
    <Svg size={22}>
      <circle cx="5" cy="12" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="19" cy="12" r="1.3" fill="currentColor" stroke="none" />
    </Svg>
  );
}

/** «⌃⌄» у выбора времени (кадры 3, 5, 6). */
export function IconUpDown() {
  return (
    <Svg size={16}>
      <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />
    </Svg>
  );
}
