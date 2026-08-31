/**
 * Goal select screen (DRF-1190) — dumb renderer over the server-side
 * decision-context document.
 *
 * The screen takes NO decisions: no hardcoded chip lists, no local
 * "what to show next" computation, no next-screen branching. Every
 * user action is a POST `/goals/select`; the returned document
 * replaces state verbatim and the UI re-renders from it:
 *
 *   1. Suggestions (`suggestions`) — chips; click POSTs `{goal_key}`.
 *   2. Free-form input (intent `formulate_own`) — same surface, not a
 *      separate branch: textarea + submit POSTs `{goal_text}`.
 *   3. Guidance (intent `need_guidance`) — button POSTs
 *      `{intent: "need_guidance"}`. The user STAYS on the surface;
 *      the server answers with `missing` kind=goal_guidance and the
 *      first guiding question in `prompt`, which we render like any
 *      other missing item.
 *
 * Sections:
 *   - `known.goal` != null → "current goal" block (goal_text, or the
 *     suggestion label resolved by goal_key, or the raw key).
 *   - `missing` non-empty → each item's `prompt` rendered as-is
 *     (clarifying and guiding questions are not distinguished here).
 *   - While a POST is in flight every control is disabled; a failed
 *     POST shows an inline error and keeps the old document.
 *
 * Экран не корневой, поэтому кнопка «назад» платформы должна быть
 * показана и заведена на роутер (канон `useBackButton`: показывать
 * везде, кроме корня). Пока входом была только стартовая сетка бота,
 * это не мешало; теперь на анкету ведёт приглашение с домашнего экрана,
 * и без выхода назад она стала бы тупиком — то есть загородила бы
 * дорогу к записи.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type GoalSelectBody,
} from "../lib/customer-goals";
import { useBackButton } from "../hooks/useBackButton";

type State =
  | { kind: "loading" }
  | { kind: "ok"; doc: DecisionContext }
  | { kind: "error"; err: unknown };

const GOAL_TEXT_MAX = 500;

export function GoalSelectScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [goalText, setGoalText] = useState("");

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    fetchDecisionContext()
      .then((doc) => {
        if (!cancelled) setState({ kind: "ok", doc });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  // Не корень — «назад» ведёт туда, откуда пришли (домашний экран или
  // стартовая сетка бота), а не закрывает мини-апп.
  const goBack = useCallback(() => navigate(-1), [navigate]);
  useBackButton({ onBack: goBack });

  const submit = useCallback((body: GoalSelectBody) => {
    setSubmitting(true);
    setSubmitError(null);
    postGoalSelect(body)
      .then((doc) => {
        setState({ kind: "ok", doc });
        setGoalText("");
      })
      .catch(() => {
        // Keep the old document; just surface the failure.
        setSubmitError("Не получилось отправить. Попробуй снова.");
      })
      .finally(() => setSubmitting(false));
  }, []);

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Какая у тебя цель?">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout title="Какая у тебя цель?">
        <StateError err={state.err} onRetry={load} screenId="goal-select" />
      </ScreenLayout>
    );
  }

  const { doc } = state;
  const knownGoal = doc.known.goal;
  const knownLabel = knownGoal
    ? knownGoal.goal_text ??
      doc.suggestions.find((s) => s.key === knownGoal.goal_key)?.label ??
      knownGoal.goal_key
    : null;
  const intentLabel = (id: string) =>
    doc.intents.find((i) => i.id === id)?.label ?? null;
  const formulateOwnLabel = intentLabel("formulate_own");
  const guidanceLabel = intentLabel("need_guidance");

  return (
    <ScreenLayout title="Какая у тебя цель?">
      {knownGoal && knownLabel && (
        <section aria-labelledby="goal-select-current">
          <h2 id="goal-select-current" className="goal-select__section-title">
            Текущая цель
          </h2>
          <p className="goal-select__current">{knownLabel}</p>
        </section>
      )}

      {doc.missing.length > 0 && (
        <section aria-label="Вопросы">
          {doc.missing.map((item, index) => (
            <p key={`${item.kind}-${index}`} className="goal-select__prompt">
              {item.prompt}
            </p>
          ))}
        </section>
      )}

      {doc.suggestions.length > 0 && (
        <section aria-labelledby="goal-select-suggestions">
          <h2
            id="goal-select-suggestions"
            className="goal-select__section-title"
          >
            {intentLabel("choose_suggested") ?? "Выбери из вариантов"}
          </h2>
          <div
            className="chip-row"
            role="radiogroup"
            aria-labelledby="goal-select-suggestions"
          >
            {doc.suggestions.map((s) => {
              const active = knownGoal?.goal_key === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={`chip${active ? " chip--active" : ""}`}
                  disabled={submitting}
                  onClick={() =>
                    submit({ goal_key: s.key, source_channel: "miniapp" })
                  }
                >
                  {s.label}
                </button>
              );
            })}
          </div>
        </section>
      )}

      {formulateOwnLabel && (
        <section aria-labelledby="goal-select-own">
          <h2 id="goal-select-own" className="goal-select__section-title">
            {formulateOwnLabel}
          </h2>
          <textarea
            className="goal-select__textarea"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            maxLength={GOAL_TEXT_MAX}
            rows={3}
            placeholder="Опиши своими словами"
            aria-label={formulateOwnLabel}
            disabled={submitting}
          />
          <div className="goal-select__actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={submitting || goalText.trim().length === 0}
              onClick={() =>
                submit({
                  goal_text: goalText.trim(),
                  source_channel: "miniapp",
                })
              }
            >
              Отправить
            </button>
          </div>
        </section>
      )}

      {guidanceLabel && (
        <div className="goal-select__actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={submitting}
            onClick={() =>
              submit({ intent: "need_guidance", source_channel: "miniapp" })
            }
          >
            {guidanceLabel}
          </button>
        </div>
      )}

      {submitError && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{submitError}</p>
        </div>
      )}
    </ScreenLayout>
  );
}
