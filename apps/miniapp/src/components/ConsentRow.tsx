/**
 * Consent row — info variant (locked) or toggle variant (editable).
 *
 * Spec: `docs/screens/customer-profile-flow.md` §4 R2 + §4.1 (locked
 * vs editable) + §13.3 (use `<dl>` definition list semantics).
 *
 * # Why two variants in one component
 *
 * 4 rows total: 3 locked info + 1 editable marketing toggle. They
 * share identical layout (title + status + description) but differ in
 * the trailing slot (info text vs ToggleSwitch). One component keeps
 * the visual cadence stable.
 *
 * Anti-pattern avoided per spec §14: locked controls rendered as
 * disabled toggle. Pilot uses an info row with «Включено · нужно для
 * записи» status string instead — no toggle widget at all.
 *
 * # Третий вариант: «action» (DRF-1453)
 *
 * Согласие на медданные — специальная категория по 152-ФЗ ст. 10, и оно
 * обязано быть осознанным: тумблер здесь был бы ровно тем «принимаю всё
 * одной галочкой», которого закон в этом случае не допускает. Поэтому
 * вариант `action` не переключает состояние сам — он ведёт в лист с
 * раскрытием, где человек читает, что именно разрешает и зачем, и уже там
 * подтверждает. Строка остаётся той же по вёрстке: визуальный ритм списка
 * согласий не ломается ради одного особого случая.
 */

import { useId, type ReactNode, type RefObject } from "react";
import { ToggleSwitch } from "./ToggleSwitch";

interface BaseProps {
  title: string;
  description: ReactNode;
}

interface InfoVariantProps extends BaseProps {
  variant: "info";
  /** «Включено · нужно для записи» — verbatim from spec §4. */
  statusText: string;
}

interface ActionVariantProps extends BaseProps {
  variant: "action";
  /** «Разрешено · с 3 сентября» / «Не разрешено» — состояние словами. */
  statusText: string;
  /** Подпись кнопки, ведущей в лист с раскрытием. */
  actionLabel: string;
  /** Полная подпись для скринридера — кнопка «Разрешить» вне контекста немая. */
  actionAriaLabel: string;
  onAction: () => void;
  /** Куда вернуть фокус после закрытия листа (WCAG 2.4.3). */
  triggerRef?: RefObject<HTMLButtonElement>;
  /** Состояние ещё грузится или запись в полёте — нажатие заблокировано. */
  busy?: boolean;
}

interface ToggleVariantProps extends BaseProps {
  variant: "toggle";
  checked: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
  /** Pending write disables interaction. */
  busy?: boolean;
}

type Props = InfoVariantProps | ActionVariantProps | ToggleVariantProps;

export function ConsentRow(props: Props) {
  // #953: stable id via useId — the old slug-from-title scheme could
  // collide on repeated titles and broke on punctuation.
  const titleId = useId();
  return (
    <div className="profile-consent-row" role="group" aria-labelledby={titleId}>
      <div className="profile-consent-row__head">
        <dt className="profile-consent-row__title" id={titleId}>
          {props.title}
        </dt>
        <dd className="profile-consent-row__status">
          {props.variant === "info" && (
            <span className="profile-consent-row__status-text">
              {props.statusText}
            </span>
          )}
          {props.variant === "action" && (
            <span className="profile-consent-row__action">
              <span className="profile-consent-row__status-text">
                {props.statusText}
              </span>
              <button
                ref={props.triggerRef}
                type="button"
                className="btn-secondary profile-consent-row__action-btn"
                onClick={props.onAction}
                disabled={props.busy}
                aria-label={props.actionAriaLabel}
              >
                {props.actionLabel}
              </button>
            </span>
          )}
          {props.variant === "toggle" && (
            <ToggleSwitch
              checked={props.checked}
              onChange={props.onChange}
              ariaLabel={props.ariaLabel}
              disabled={props.busy}
            />
          )}
        </dd>
      </div>
      <p className="profile-consent-row__description">{props.description}</p>
    </div>
  );
}
