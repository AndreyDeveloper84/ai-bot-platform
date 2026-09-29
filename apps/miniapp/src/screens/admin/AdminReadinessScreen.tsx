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
 * «Проверить снова» — без спама (решение главного окна 20.09): каждая
 * проверка (и первая, и повтор) блокирует кнопку на время запроса и ещё на
 * 10 с после него — кнопка либо работает, либо выглядит выключенной, «живая,
 * но молчит» здесь не бывает. Бот кэширует ответ каталога на 120 с, поэтому
 * рядом — «обновлено HH:MM» из `checked_at`, и человек видит, что повтор мог
 * вернуть тот же снимок. Повтор из `StateError` (после сбоя) — вне троттла:
 * там нечего кэшировать и нечего беречь.
 *
 * Ресепшну маршрут закрыт вместе с тройкой (`canOpenSalonPilot`) — как
 * `/admin/handoff`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { StateError } from "../../components/StateError";
import { useScreenBack } from "../../hooks/useScreenBack";
import { backTo } from "../../lib/screen-back";
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
  // DRF-2368 — см. очередь передач: одно объявление, две половины.
  const back = useScreenBack(backTo(SALON_PILOT_LANDING));

  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const alive = useRef(true);
  const inflight = useRef<AbortController | null>(null);
  const cooldownTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const armCooldown = useCallback(() => {
    if (cooldownTimer.current) clearTimeout(cooldownTimer.current);
    setCooldown(true);
    cooldownTimer.current = setTimeout(() => {
      if (alive.current) setCooldown(false);
    }, RECHECK_MIN_INTERVAL_MS);
  }, []);

  const load = useCallback(
    async (opts: { throttle: boolean } = { throttle: true }) => {
      inflight.current?.abort();
      const ctrl = new AbortController();
      inflight.current = ctrl;
      setBusy(true);
      if (opts.throttle) armCooldown();
      try {
        const data = await getSalonReadiness({ signal: ctrl.signal });
        if (!alive.current || ctrl.signal.aborted) return;
        setState({ kind: "ready", data });
      } catch (err) {
        if (!alive.current || ctrl.signal.aborted) return;
        setState({ kind: "error", err });
      } finally {
        if (alive.current && !ctrl.signal.aborted) setBusy(false);
      }
    },
    [armCooldown],
  );

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      inflight.current?.abort();
      if (cooldownTimer.current) clearTimeout(cooldownTimer.current);
    };
  }, [load]);

  const recheck = useCallback(() => {
    if (busy || cooldown) return;
    void load();
  }, [busy, cooldown, load]);

  // После сбоя повтор — без троттла: беречь нечего, кэш каталога не при чём.
  const retryAfterError = useCallback(() => void load({ throttle: false }), [load]);

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
        {/* Одна постоянная live-область, меняется только текст: смена
            «загружаем» → итог объявляется, а вставка новой role=status — нет
            (то же правило, что у statusLine на «Сегодня»). */}
        <p className="salon-pilot__note" role="status">
          {state.kind === "loading" ? READINESS_COPY.loading : ""}
          {view?.kind === "ready" ? READINESS_COPY.ready : ""}
          {view?.kind === "unknown"
            ? // Отказ источника — его строка о салоне дословно; unknown без строк — своя.
              (data?.problems[0]?.text ?? READINESS_COPY.unknown)
            : ""}
        </p>

        {state.kind === "error" ? <StateError err={state.err} onRetry={retryAfterError} /> : null}

        {data && view ? (
          <>

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
