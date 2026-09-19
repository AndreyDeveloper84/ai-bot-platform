/**
 * Очередь диалогов, ждущих человека (DRF-2115) — `/admin/handoff`.
 *
 * Открывается с карточки «Диалоги — N ждут ответа» на «Сегодня». Показывает
 * то, что отдаёт `GET admin/handoff-queue/`: сколько ждут, как давно, взята
 * ли задача и кем, эскалирована ли. Клиента и текста здесь нет — очередь
 * отвечает «сколько и как давно», а не «кто и что писал».
 *
 * Предел экрана: только чтение. «Взять» и «закрыть» — в Django-админке
 * (DRF-1499 экран очереди, DRF-1488 механика); вторую механику
 * назначения здесь не заводят.
 *
 * Вложенный экран пилота: без нижней панели (DRF-1235), возврат — системной
 * кнопкой MAX и стрелкой на «Сегодня».
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { StateError } from "../../components/StateError";
import { useBackButton } from "../../hooks/useBackButton";
import { getHandoffQueue, type HandoffQueueResponse } from "../../lib/admin-api";
import { SALON_PILOT_LANDING } from "../../lib/salon-pilot";
import { waitingLabel } from "./SalonTodayCards";

export const HANDOFF_COPY = {
  title: "Диалоги",
  empty: "Никто не ждёт ответа.",
  loading: "Загружаем очередь…",
  claimed: (who: string) => `взял: ${who}`,
  unclaimed: "никто не взял",
  escalated: "эскалирована",
  readOnly: "Взять и закрыть диалог — в панели администратора.",
} as const;

/** 84 → «1 ч 24 мин», 3 → «3 мин», 0 → «только что». */
export function formatAge(minutes: number): string {
  if (minutes <= 0) return "только что";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} мин`;
  return m === 0 ? `${h} ч` : `${h} ч ${m} мин`;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: HandoffQueueResponse }
  | { kind: "error"; err: unknown };

export function AdminHandoffQueueScreen() {
  const navigate = useNavigate();
  const back = useCallback(() => navigate(SALON_PILOT_LANDING), [navigate]);
  useBackButton({ onBack: back });

  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    getHandoffQueue()
      .then((data) => setState({ kind: "ready", data }))
      .catch((err: unknown) => setState({ kind: "error", err }));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
        <h1 className="records-screen__title">{HANDOFF_COPY.title}</h1>
      </header>

      <div className="screen__body">
        {state.kind === "loading" ? (
          <p className="salon-pilot__note" role="status">
            {HANDOFF_COPY.loading}
          </p>
        ) : null}

        {state.kind === "error" ? <StateError err={state.err} onRetry={load} /> : null}

        {state.kind === "ready" && state.data.rows.length === 0 ? (
          <div className="callout" role="status">
            <p style={{ margin: 0 }}>{HANDOFF_COPY.empty}</p>
          </div>
        ) : null}

        {state.kind === "ready" && state.data.rows.length > 0 ? (
          <>
            <p className="salon-pilot__note">{waitingLabel(state.data.waiting)}.</p>
            <ul className="handoff-queue__list">
              {state.data.rows.map((row) => (
                <li key={row.task_id} className="handoff-queue__row">
                  <span className="handoff-queue__age">{formatAge(row.age_minutes)}</span>
                  <span className="handoff-queue__who">
                    {row.claimed && row.addressee
                      ? HANDOFF_COPY.claimed(row.addressee)
                      : HANDOFF_COPY.unclaimed}
                  </span>
                  {row.escalated ? (
                    <span className="handoff-queue__flag">{HANDOFF_COPY.escalated}</span>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="salon-pilot__note">{HANDOFF_COPY.readOnly}</p>
          </>
        ) : null}
      </div>
    </div>
  );
}
