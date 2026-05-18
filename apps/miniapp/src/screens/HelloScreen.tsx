/**
 * Phase 0c — auth round-trip smoke test screen.
 *
 * On mount calls POST /api/v1/customer/auth/verify and renders the
 * resolved identity. Real screens land in Phase 1+; this exists so we
 * can prove the full stack (MAX SDK → fetch → initData header → HMAC
 * verify → BotUser resolve → tenant_scope → response) works end-to-end
 * before building UI on top.
 */

import { useEffect, useState } from "react";
import { ApiError, authVerify, type AuthVerifyResponse } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { signalReady } from "../lib/max-sdk";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: AuthVerifyResponse }
  | { kind: "error"; status: number; slug: string; detail: string };

export function HelloScreen() {
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
    <ScreenLayout title={`Здравствуйте, ${name}!`}>
      <div className="callout">
        Это шелл мини-приложения. Дальше — каталог, мастера, запись.
      </div>
      <dl style={{ margin: 0 }}>
        <dt style={{ color: "var(--c-text-secondary)" }}>Студия</dt>
        <dd style={{ margin: "0 0 var(--s-3)" }}>
          {tenant.name} ({tenant.slug})
        </dd>
        <dt style={{ color: "var(--c-text-secondary)" }}>Часовой пояс</dt>
        <dd style={{ margin: 0 }}>{tenant.timezone}</dd>
      </dl>
    </ScreenLayout>
  );
}
