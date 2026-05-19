/** Root — auth round-trip + CTA into catalog. */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, authVerify, type AuthVerifyResponse } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { signalReady } from "../lib/max-sdk";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: AuthVerifyResponse }
  | { kind: "error"; status: number; slug: string; detail: string };

export function HelloScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    signalReady();
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

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Помощник студии">
        <p>Соединяемся…</p>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout title="Не удалось войти">
        <div className="callout callout--danger" role="alert">
          <strong>{state.slug}</strong>
          <p style={{ margin: "var(--s-2) 0 0" }}>{state.detail}</p>
        </div>
      </ScreenLayout>
    );
  }

  const { user, tenant } = state.data;
  const name = user.client_name || user.display_name || "гость";
  return (
    <ScreenLayout
      title={`Здравствуйте, ${name}!`}
      cta={<StickyCta onClick={() => navigate("/catalog")}>Записаться</StickyCta>}
    >
      <p>Помогу записаться в студию {tenant.name}.</p>
    </ScreenLayout>
  );
}
