/**
 * A link the reader is meant to hand to somebody else (DRF-1505).
 *
 * Both credentials the admin surface issues end in the same problem: the
 * owner is holding a string that has to reach another person's phone.
 * Until this component existed the master invitation showed a URL as
 * plain `<code>` with no way to take it, and the staff access code had
 * no link at all — the pilot's answer to «how do I invite someone» was
 * «read four characters aloud».
 *
 * ### Why the link stays visible after «Скопировать»
 *
 * Clipboard access is a convenience and it is allowed to fail: an older
 * webview, a denied permission, a MAX build that does not expose it.
 * When it does fail the reader must still be able to select the text by
 * hand, so the URL is rendered in full and the button is layered on top
 * of it rather than replacing it. The button reports what happened
 * instead of pretending — a silent no-op on a credential that is shown
 * once is how somebody closes the screen with nothing.
 *
 * ### Why the copy is one tap and not a share sheet
 *
 * MAX's Mini App bridge has no share intent we can rely on, and the
 * owner's next move is a paste into whichever chat they already have
 * with that person — MAX, Telegram, SMS. The clipboard is the one place
 * all of those read from.
 */

import { useCallback, useState } from "react";

import { hapticSelection } from "../lib/max-sdk";

interface Props {
  /** The URL itself. Rendered verbatim and copied verbatim. */
  readonly url: string;
  /** What this link is, in the reader's terms. */
  readonly label: string;
  /** One line under the label: what happens when someone opens it. */
  readonly hint?: string;
  /** Button caption before the tap. */
  readonly copyLabel?: string;
  /** Test hook / styling hook for the surrounding block. */
  readonly className?: string;
}

export function ShareableLink({
  url,
  label,
  hint,
  copyLabel = "Скопировать ссылку",
  className,
}: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setState("copied");
      hapticSelection();
    } catch {
      // Says so out loud. A copy button that quietly does nothing is
      // worse than no button: the reader believes they have the link.
      setState("failed");
    }
  }, [url]);

  return (
    <div className={className ? `shareable ${className}` : "shareable"}>
      <p className="shareable__label">{label}</p>
      {hint && <p className="shareable__hint">{hint}</p>}
      <code className="shareable__url">{url}</code>
      <button
        type="button"
        className="btn-secondary shareable__copy"
        onClick={() => void onCopy()}
      >
        {state === "copied" ? "Скопировано" : copyLabel}
      </button>
      {state === "failed" && (
        <p className="shareable__failed" role="alert">
          Не удалось скопировать — выделите ссылку и скопируйте вручную.
        </p>
      )}
    </div>
  );
}
