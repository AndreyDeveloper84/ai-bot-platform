/**
 * «Готовность» — салонная готовность поимённо (DRF-2117, §50 п.4).
 *
 * Адрес: `/admin/readiness`, с карточки «Готовность — N проблем» на
 * «Сегодня». Источник — `GET /api/v1/admin/readiness/` (#1878): каталог +
 * зеркало бота, формулировки — серверные (`salon_readiness.TEXTS`), экран
 * показывает `problems[].text` дословно и ничего не переписывает — иначе у
 * одной причины было бы два текста: в чате и здесь.
 *
 * Три исхода, и ни один не молчит:
 *   * `ready` — «Салон готов принимать записи.»;
 *   * проблемы — «Салон пока не готов:» + список;
 *   * отказ источника — одна строка о салоне (`origin: "source"`), не «готов»
 *     и не пустой список; `unknown` без строк — тоже «не удалось проверить».
 *
 * «Проверить снова» — без спама (решение главного окна 20.09): кнопка
 * заблокирована на время запроса и не чаще раза в 10 с; бот кэширует ответ
 * каталога на 120 с, поэтому рядом — «обновлено HH:MM» из `checked_at`, и
 * человек видит, что повтор мог вернуть тот же снимок.
 *
 * Ресепшну маршрут закрыт вместе с тройкой (`canOpenSalonPilot`) — как
 * `/admin/handoff`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { StateError } from "../../components/StateError";
import { useBackButton } from "../../hooks/useBackButton";
import { getSalonReadiness, type SalonReadinessResponse } from "../../lib/admin-api";
import { SALON_PILOT_LANDING } from "../../lib/salon-pilot";
import { checkedAtLabel, readinessState } from "../../lib/salon-readiness";

export const READINESS_COPY = {
  title: "Готовность",
  loading: "Проверяем готовность салона…",
  ready: "Салон готов принимать записи.",
  notReadyHead: "Салон пока не готов:",
  unknown: "Не удалось проверить готовность.",
  recheck: "Проверить снова",
  rechecking: "Проверяем…",
  limitsHead: "Пределы проверки",
  updated: (hhmm: string) => `обновлено ${hhmm}`,
} as const;

/** Минимальный интервал между повторами (мс). */
export const RECHECK_MIN_INTERVAL_MS = 10_000;

type State =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "ready"; data: SalonReadinessResponse };

export function AdminReadinessScreen() {
  const navigate = useNavigate();
  const back = useCallback(() => navigate(SALON_PILOT_LANDING), [navigate]);
  useBackButton({ onBack: back });

  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const lastRunAt = useRef(0);
  const cooldownTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    lastRunAt.current = Date.now();
    try {
      const data = await getSalonReadiness();
      setState({ kind: "ready", data });
    } catch (err) {
      setState({ kind: "error", err });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      if (cooldownTimer.current) clearTimeout(cooldownTimer.current);
    };
  }, [load]);

  const recheck = useCallback(() => {
    if (busy || cooldown) return;
    const since = Date.now() - lastRunAt.current;
    if (since < RECHECK_MIN_INTERVAL_MS) return;
    setCooldown(true);
    cooldownTimer.current = setTimeout(() => setCooldown(false), RECHECK_MIN_INTERVAL_MS);
    void load();
  }, [busy, cooldown, load]);

  const data = state.kind === "ready" ? state.data : null;
  const view = data ? readinessState(data) : null;
  const updated = data ? checkedAtLabel(data.checked_at) : "";

  return (
    <div className="screen">
      <header className="records-screen__header">
        <button type="button" className="records-screen__back" aria-label="Назад" onClick={back}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">{READINESS_COPY.title}</h1>
      </header>

      <div className="screen__body">
        {state.kind === "loading" ? (
          <p className="salon-pilot__note" role="status">
            {READINESS_COPY.loading}
          </p>
        ) : null}

        {state.kind === "error" ? <StateError err={state.err} onRetry={load} /> : null}

        {data && view ? (
          <>
            {view.kind === "ready" ? (
              <div className="callout" role="status">
                <p style={{ margin: 0 }}>{READINESS_COPY.ready}</p>
              </div>
            ) : null}

            {view.kind === "unknown" ? (
              <div className="callout callout--danger" role="status">
                <p style={{ margin: 0 }}>
                  {/* Отказ источника — его строка о салоне дословно; unknown без строк — своя. */}
                  {data.problems[0]?.text ?? READINESS_COPY.unknown}
                </p>
              </div>
            ) : null}

            {view.kind === "problems" ? (
              <>
                <p className="salon-pilot__note">{READINESS_COPY.notReadyHead}</p>
                <ul className="readiness__list" aria-label="Проблемы готовности">
                  {data.problems.map((p, i) => (
                    <li key={`${p.code}:${p.master.id ?? i}`} className="readiness__row">
                      {p.text}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            {data.limits.length > 0 ? (
              <section className="readiness__limits" aria-labelledby="readiness-limits-h">
                <h2 id="readiness-limits-h" className="readiness__limits-head">
                  {READINESS_COPY.limitsHead}
                </h2>
                <ul className="readiness__limits-list">
                  {data.limits.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </section>
            ) : null}

            <div className="readiness__actions">
              <button
                type="button"
                className="btn-secondary readiness__recheck"
                disabled={busy || cooldown}
                onClick={recheck}
              >
                {busy ? READINESS_COPY.rechecking : READINESS_COPY.recheck}
              </button>
              {updated ? (
                <span className="readiness__updated">{READINESS_COPY.updated(updated)}</span>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
