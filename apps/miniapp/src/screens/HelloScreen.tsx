/** Root — auth round-trip + CTA into catalog.
 *
 * The Mini App's first screen. Calls /auth/verify once on launch; on
 * success the rest of the app is unblocked. Failure cases get
 * slug-specific recovery copy instead of dumping raw English messages
 * (see ERROR_COPY map below).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, authVerify, type AuthVerifyResponse } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { signalReady } from "../lib/max-sdk";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: AuthVerifyResponse }
  | { kind: "error"; status: number; slug: string; detail: string };

type ErrorCopy = {
  title: string;
  body: string;
  /** Action verb shown on the recovery button. Omit to hide the button. */
  retryLabel?: string;
};

/**
 * Map auth-failure slugs to human-readable Russian copy.
 *
 * Slugs come from apps/miniapp_api/views.py:require_init_data:
 *  - bad_signature (401)
 *  - stale (401)        — auth_date older than 60 min
 *  - malformed (400)    — missing / mangled Authorization header
 *  - user_deleted (403) — soft-deleted user re-entry
 *  - server_misconfigured (500) — MAX_BOT_TOKEN / MAX_BOT_TENANT_SLUG missing
 *  - http_error / network — client-side fallback when JSON parse failed
 */
const ERROR_COPY: Record<string, ErrorCopy> = {
  bad_signature: {
    title: "Не получилось подтвердить личность",
    body: "Это может быть проблема с авторизацией в MAX. Закройте Mini App и откройте заново. Если проблема повторится — напишите в студию.",
    retryLabel: "Попробовать снова",
  },
  stale: {
    title: "Сессия устарела",
    body: "Прошло больше часа с момента открытия. Просто откройте Mini App заново.",
    retryLabel: "Попробовать снова",
  },
  malformed: {
    title: "Не получилось войти",
    body: "MAX не передал данные для входа. Попробуйте закрыть Mini App и открыть заново — это часто помогает.",
    retryLabel: "Попробовать снова",
  },
  user_deleted: {
    title: "Аккаунт удалён",
    body: "Вы попросили удалить данные ранее. Чтобы восстановить профиль, напишите боту студии — мы поможем.",
  },
  user_not_registered: {
    title: "Сейчас откроем",
    body: "Создаём ваш профиль в студии — это может занять секунду. Если страница не обновится сама — нажмите кнопку.",
    retryLabel: "Попробовать снова",
  },
  server_misconfigured: {
    title: "Что-то у нас не так",
    body: "Не получилось войти из-за временной проблемы на нашей стороне. Уже разбираемся — попробуйте чуть позже.",
    retryLabel: "Попробовать снова",
  },
  http_error: {
    title: "Не удалось загрузить",
    body: "Проверьте интернет и попробуйте ещё раз.",
    retryLabel: "Попробовать снова",
  },
  network: {
    title: "Нет связи",
    body: "Mini App не получилось подключиться к интернету. Проверьте соединение и попробуйте снова.",
    retryLabel: "Попробовать снова",
  },
};

function pickCopy(slug: string): ErrorCopy {
  return (
    ERROR_COPY[slug] ?? {
      title: "Не удалось войти",
      body: "Попробуйте закрыть Mini App и открыть заново. Если повторится — напишите в студию.",
      retryLabel: "Попробовать снова",
    }
  );
}

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

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Помощник студии">
        <p>Соединяемся…</p>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    const copy = pickCopy(state.slug);
    return (
      <ScreenLayout title={copy.title}>
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
      title={`Здравствуйте, ${name}!`}
      cta={<StickyCta onClick={() => navigate("/catalog")}>Записаться</StickyCta>}
    >
      <p>Помогу записаться в студию {tenant.name}.</p>
      <nav className="hello-nav" aria-label="Разделы">
        <button type="button" className="btn-secondary" onClick={() => navigate("/my-visits")}>
          Мои записи
        </button>
        <button type="button" className="btn-secondary" onClick={() => navigate("/me")}>
          Профиль
        </button>
      </nav>
    </ScreenLayout>
  );
}
