/**
 * Состояние на весь экран в разделе «Записи»: значок в круге, заголовок,
 * пояснение, необязательная карточка и кнопки. Макет «Записи.png» (DRF-2952),
 * кадры 2, 7–10, 14–16 — одна раскладка на все.
 *
 * # Утверждение о сделанном — только с доказательством
 *
 * «Запись отменена» и «Перенесла запись» — слова о выполненном действии.
 * Экран передаёт `claim`, и он сверяется с прочитанным ответом сервера прямо
 * при отрисовке (`assertClaim`, DRF-2347) — там же, где слова рождаются, как
 * это делает `Snackbar`. В тестах несоответствие бросает; перед человеком
 * сторож молчит, поэтому экран обязан ветвиться по вердикту сам, до вызова.
 *
 * # Доступность
 *
 * Состояние заменяет содержимое экрана целиком, и читающий с экрана должен
 * узнать об этом: фокус уходит на заголовок, блок объявлен живой областью.
 * `tone="danger"` — `alert`, остальное — `status`.
 */
import { useEffect, useRef, type ReactNode } from "react";

import { assertClaim, type Claim } from "../../lib/claims";
import { RecordIcon, type RecordIconName } from "./RecordIcons";

export type RecordsStateTone = "accent" | "success" | "danger" | "info";

interface Props {
  icon: RecordIconName;
  tone?: RecordsStateTone;
  title: string;
  /** Пояснение под заголовком; строки — абзацами. */
  body?: ReactNode;
  /** Карточка под пояснением (о какой записи речь). */
  children?: ReactNode;
  actions?: ReactNode;
  /** Утверждение о сделанном, которое несёт заголовок. */
  claim?: Claim<unknown>;
  /**
   * Не уводить фокус на заголовок. Для состояний, которые показаны с первого
   * кадра экрана (пусто, не загрузилось): там фокус ещё ничей.
   */
  quiet?: boolean;
  /**
   * Состояние стоит внутри диалога (подтверждение отмены): диалог объявляет
   * себя сам, вторая живая область прочитала бы текст дважды.
   */
  inDialog?: boolean;
}

export function RecordsState({
  icon,
  tone = "accent",
  title,
  body,
  children,
  actions,
  claim,
  quiet = false,
  inDialog = false,
}: Props) {
  if (claim) assertClaim(claim);

  const titleRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => {
    if (!quiet) titleRef.current?.focus();
  }, [quiet, title]);

  return (
    <section
      className="cr-state"
      role={inDialog ? undefined : tone === "danger" ? "alert" : "status"}
      aria-live={inDialog ? undefined : tone === "danger" ? "assertive" : "polite"}
    >
      <span className={`cr-state__badge cr-state__badge--${tone}`}>
        <RecordIcon name={icon} size={44} />
      </span>
      <h2 ref={titleRef} tabIndex={-1} className="cr-state__title">
        {title}
      </h2>
      {body && <div className="cr-state__body">{body}</div>}
      {children}
      {actions && <div className="cr-state__actions">{actions}</div>}
    </section>
  );
}

type ButtonVariant = "primary" | "soft" | "danger" | "text";

interface ButtonProps {
  variant: ButtonVariant;
  children: ReactNode;
  onClick?: () => void;
  /** Внешняя ссылка (поддержка) — тогда это `<a>`, а не кнопка. */
  href?: string;
  icon?: RecordIconName;
  disabled?: boolean;
  /** Подсказка, почему кнопка выключена (нет сети). */
  title?: string;
}

/** Кнопка раздела: основная, мягкая, опасная контурная и текстовая. */
export function RecordButton({ variant, children, onClick, href, icon, disabled, title }: ButtonProps) {
  const className = `cr-btn cr-btn--${variant}`;
  const content = (
    <>
      {icon && <RecordIcon name={icon} />}
      <span>{children}</span>
    </>
  );
  if (href) {
    return (
      <a className={className} href={href} target="_blank" rel="noreferrer">
        {content}
      </a>
    );
  }
  return (
    <button type="button" className={className} onClick={onClick} disabled={disabled} title={title}>
      {content}
    </button>
  );
}
