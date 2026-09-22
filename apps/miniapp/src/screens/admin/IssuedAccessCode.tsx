/**
 * A freshly issued access code, shown ONCE — the one block every surface
 * that mints a code renders (DRF-2275 moved it out of
 * `AddPersonAccessCodeSection` so «Отправить заново» on the invites screen
 * shows a resent code exactly as a new one, warning and all).
 *
 * Only the code's hash is stored; nothing can produce it again. The
 * warning therefore sits ABOVE the code, and copying is a convenience,
 * never the only way out — the code stays selectable on screen.
 */
import { useState } from "react";

import { ShareableLink } from "../../components/ShareableLink";
import type { StaffInviteResponse } from "../../lib/admin-api";
import { formatDateLong } from "../../lib/masterDateFormat";
import { hapticSelection } from "../../lib/max-sdk";

interface Props {
  issued: StaffInviteResponse;
  roleLabel: string;
  masterName?: string;
}

export function IssuedAccessCode({ issued, roleLabel, masterName }: Props) {
  const [copied, setCopied] = useState(false);

  async function onCopy(code: string) {
    // Clipboard is a convenience, never the only way out: the code stays
    // selectable on screen, so a refusal (older webview, denied
    // permission) costs nothing but the button's feedback.
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      hapticSelection();
    } catch {
      setCopied(false);
    }
  }

  return (
    <>
  {/* Above the code on purpose: a warning under it is read after the
      reader has already decided what to do. */}
  <div className="callout callout--danger" role="alert">
    <p className="staff-access__once">
      Код и ссылка показываются один раз.
    </p>
    <p className="staff-access__once-detail">
      Мы храним только отпечаток кода — восстановить его нельзя ни здесь,
      ни в поддержке. Передайте до того, как закроете экран.
    </p>
  </div>

  <p className="staff-access__code" aria-label={`Код доступа ${issued.code}`}>
    {issued.code}
  </p>

  <button
    type="button"
    className="btn-secondary"
    onClick={() => void onCopy(issued.code)}
  >
    {copied ? "Скопировано" : "Скопировать код"}
  </button>
  {/* Слышимый итог: смена подписи кнопки скринридеру не событие. */}
  {copied && (
    <p className="shareable__copied" role="status">
      Код скопирован.
    </p>
  )}

  {issued.invite_link ? (
    <ShareableLink
      url={issued.invite_link}
      label="Ссылка вместо кода"
      hint={
        "Открывшему её доступ откроется сразу — код вводить не нужно. " +
        "Это тот же самый код: кто перейдёт по ссылке, тот его и " +
        "потратит, поэтому отправляйте её только тому человеку."
      }
    />
  ) : (
    <p className="admin-hint">
      Ссылки нет: в этом контуре не настроен салонный бот. Код по-прежнему
      работает, если ввести его в диалоге с ботом.
    </p>
  )}

  <dl className="staff-access__meta">
    <dt>Роль</dt>
    <dd>{roleLabel}</dd>
    {masterName && (
      <>
        <dt>Мастер</dt>
        <dd>{masterName}</dd>
      </>
    )}
    <dt>Действует до</dt>
    <dd>{formatDateLong(issued.expires_at)}</dd>
  </dl>

  <p className="staff-access__how">
    Человек отправляет этот код салонному боту в диалоге — доступ
    откроется сразу.
  </p>
    </>
  );
}
