/** Root — auth round-trip + CTA into catalog.
 *
 * The Mini App's first screen. Calls /auth/verify once on launch; on
 * success the rest of the app is unblocked. Failure cases get
 * slug-specific recovery copy instead of dumping raw English messages
 * (see `lib/auth-error-copy.ts` — shared with StateError, DRF-1319 D-1).
 *
 * DRF-1481 — все кнопки ведут в каноническое `/customer/*`: до уборки
 * поколений «Записаться» вела в старый `/catalog`, а «Мои записи» — в
 * старое `/my-visits`, то есть единственный fallback-экран раздавал
 * прошлое поколение целиком.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, authVerify, type AuthVerifyResponse } from "../lib/api";
import { authErrorCopy } from "../lib/auth-error-copy";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { signalReady } from "../lib/max-sdk";
import { screenRoot } from "../lib/screen-back";

/** Вид экрана (DRF-1493). */
const BACK = screenRoot(
  "Приветствие смонтировано на `*`: это то, чем заканчивается дерево " +
    "адресов, а не экран внутри сценария. Родителя у него нет.",
);

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: AuthVerifyResponse }
  | { kind: "error"; status: number; slug: string; detail: string };

export function HelloScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });

  const verify = useCallback((isFirstCall: boolean) => {
    if (isFirstCall) signalReady();
    setState({ kind: "loading" });
    let cancelled = false;
    authVerify()
      .then((data) => {
        if (!cancelled) setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setState({ kind: "error", status: err.status, slug: err.slug, detail: err.detail });
        } else {
          const message = err instanceof Error ? err.message : String(err);
          setState({ kind: "error", status: 0, slug: "network", detail: message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const cancel = verify(true);
    return cancel;
  }, [verify]);

  // Deeplink redirect (F3) — MOVED to `useStartParamRedirect` in App.tsx
  // (DRF-1349).
  //
  // When MAX opens the Mini App via an `open_app` button, the button's
  // `payload` arrives as initData's `start_param` and names the screen
  // to jump to. Doing that here worked only for people with no role:
  // HelloScreen is mounted solely in `CustomerRoutes`, so on the admin,
  // master, solo and unified surfaces the payload was read by nobody and
  // that surface's own catch-all won instead. The master invitation is
  // the case that made it visible — its recipient can boot into either
  // the customer or the admin surface, and only one of them was looking.
  //
  // It now runs once per boot above the role cascade, which is also why
  // it is no longer conditional on `authVerify` here: `App` gates it on
  // the `/me` boot, and `/me` lazy-creates the same BotUser.

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Помощник студии">
        <p>Соединяемся…</p>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    const copy = authErrorCopy(state.slug);
    return (
      <ScreenLayout back={BACK} title={copy.title}>
        <div className="hello-error" role="alert">
          <p>{copy.body}</p>
          {copy.retryLabel && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "var(--s-3)" }}
              onClick={() => verify(false)}
            >
              {copy.retryLabel}
            </button>
          )}
        </div>
      </ScreenLayout>
    );
  }

  const { user, tenant } = state.data;
  const name = user.client_name || user.display_name || "гость";
  return (
    <ScreenLayout
      back={BACK}
      title={`Здравствуйте, ${name}!`}
      cta={<StickyCta onClick={() => navigate("/customer/catalog")}>Записаться</StickyCta>}
    >
      <p>Помогу записаться в студию {tenant.name}.</p>
      <nav className="hello-nav" aria-label="Разделы">
        <button type="button" className="btn-secondary" onClick={() => navigate("/customer/records")}>
          Мои записи
        </button>
        <button type="button" className="btn-secondary" onClick={() => navigate("/customer/profile")}>
          Профиль
        </button>
      </nav>
    </ScreenLayout>
  );
}
