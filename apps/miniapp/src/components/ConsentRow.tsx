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
 */

import type { ReactNode } from "react";
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

interface ToggleVariantProps extends BaseProps {
  variant: "toggle";
  checked: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
  /** Pending write disables interaction. */
  busy?: boolean;
}

type Props = InfoVariantProps | ToggleVariantProps;

export function ConsentRow(props: Props) {
  const titleId = `consent-row-title-${props.title
    .toLowerCase()
    .replace(/[^а-яa-z0-9]+/g, "-")}`;
  return (
    <div className="profile-consent-row" role="group" aria-labelledby={titleId}>
      <div className="profile-consent-row__head">
        <dt className="profile-consent-row__title" id={titleId}>
          {props.title}
        </dt>
        <dd className="profile-consent-row__status">
          {props.variant === "info" ? (
            <span className="profile-consent-row__status-text">
              {props.statusText}
            </span>
          ) : (
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
