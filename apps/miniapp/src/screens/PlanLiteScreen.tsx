/**
 * «Мой план» — Plan Lite без веса (DRF-2101, решение владельца §49).
 *
 * Route: `/customer/plan`. Входы — «Мой план» с экрана цели (C01) и с
 * дашборда, когда цель есть. Под флагом сборки `VITE_PLAN_LITE=1`.
 *
 * Что здесь:
 *   - плана нет → конструктор: три обязательства-ДЕЙСТВИЯ («Записаться на
 *     услугу под цель», «Вести дневник N дней в неделю», «Пить воду N раз в
 *     день»), выбрать 1–3 → «Составить план» → POST только `actions`
 *     (активную цель знает каталог);
 *   - план есть → карточка: «Твоя цель: {метка}» (метка — из
 *     decision-context, как на экране цели; иначе ключ) и по обязательству
 *     «N из M» за текущее ведро — и ничего о результате: ни процента цели,
 *     ни шкалы, ни «ты пропустил» (В-5, DRF-1332); каждое обязательство
 *     ведёт туда, где оно делается (каталог / дневник / вода);
 *   - «Изменить план» = закрыть (append-only) и составить заново.
 *
 * Чего здесь нет по построению: наблюдений тела, веса, прогресса по
 * результату (гейт O, DRF-1331), проактивности — человек видит план, когда
 * сам открыл.
 *
 * Отказы по слагам: `not_found` на POST — активной цели нет → экран цели;
 * `already_active` — перечитать; `plan_lite_disabled` — «недоступно»;
 * прочее — фраза + «Повторить».
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import { fetchDecisionContext } from "../lib/customer-goals";
import { planLiteEnabled } from "../lib/feature-flags";
import {
  closePlanLite,
  createPlanLite,
  getPlanLite,
  type PlanLite,
  type PlanLiteAction,
  type PlanLiteActionSpec,
  type PlanLiteActionType,
} from "../lib/plan-lite";
import { backTo } from "../lib/screen-back";

export const PLAN_LITE_ROUTE = "/customer/plan";
const GOAL_ROUTE = "/customer/goal-select";

export const PLAN_LITE_COPY = {
  title: "Мой план",
  entryFromGoal: "Мой план",
  entryFromDashboard: "Мой план",
  loading: "Загружаю…",
  builderTitle: "Выбери 1–3 шага под свою цель",
  builderHint: "План — это действия, а не обещание результата: я буду показывать, сколько из них сделано.",
  chipBook: "Записаться на услугу под цель",
  chipFood: (n: number) => `Вести дневник ${n} дн. в неделю`,
  chipWater: (n: number) => `Пить воду ${n} раз в день`,
  less: "меньше",
  more: "больше",
  compose: "Составить план",
  composing: "Составляю…",
  goalTitle: (goal: string) => `Твоя цель: ${goal}`,
  thisWeek: "На этой неделе",
  today: "Сегодня",
  ofTotal: (done: number, target: number) => `${done} из ${target}`,
  go: "Перейти",
  change: "Изменить план",
  changing: "Закрываю…",
  needGoal: "Сначала выбери цель — план строится от неё.",
  unavailable: "«Мой план» пока недоступен.",
  transient: "Не получилось прочитать план — сервис не отвечает. Попробуй чуть позже.",
  retry: "Повторить",
  labelBook: "Записаться на услугу",
  labelFood: "Дневник",
  labelWater: "Вода",
} as const;

const ACTION_LABELS: Record<PlanLiteActionType, string> = {
  book_service: PLAN_LITE_COPY.labelBook,
  log_food: PLAN_LITE_COPY.labelFood,
  log_water: PLAN_LITE_COPY.labelWater,
};

/** Куда ведёт обязательство — туда, где оно делается (то, что уже работает). */
const ACTION_ROUTES: Record<PlanLiteActionType, string> = {
  book_service: "/customer/catalog",
  log_food: "/customer/food-scanner/diary",
  log_water: "/customer/main",
};

const FOOD_DAYS = { min: 1, max: 7, initial: 3 };
const WATER_TIMES = { min: 1, max: 14, initial: 7 };

type Status =
  | { kind: "loading" }
  | { kind: "builder" }
  | { kind: "card"; plan: PlanLite }
  | { kind: "unavailable" }
  | { kind: "error" };

function refusalSlug(e: unknown): string | null {
  return e instanceof ApiError ? e.slug : null;
}

export function PlanLiteScreen() {
  const navigate = useNavigate();
  const onBack = useScreenBack(backTo(GOAL_ROUTE));

  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [goalLabel, setGoalLabel] = useState<string | null>(null);
  const [book, setBook] = useState(false);
  const [foodDays, setFoodDays] = useState<number | null>(null);
  const [waterTimes, setWaterTimes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (!planLiteEnabled()) {
      setStatus({ kind: "unavailable" });
      return;
    }
    setStatus({ kind: "loading" });
    setNotice(null);
    try {
      const plan = await getPlanLite();
      if (!alive.current) return;
      setStatus(plan ? { kind: "card", plan } : { kind: "builder" });
    } catch (e) {
      if (!alive.current) return;
      setStatus(refusalSlug(e) === "plan_lite_disabled" ? { kind: "unavailable" } : { kind: "error" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Метка цели — как на экране цели: текст человека, иначе подпись
  // курируемой цели из документа; сервер отдаёт лишь ключ. Не смогли
  // спросить — показываем ключ, план от этого не зависит.
  useEffect(() => {
    if (!planLiteEnabled()) return;
    let cancelled = false;
    fetchDecisionContext()
      .then((doc) => {
        if (cancelled) return;
        const goal = doc.known.goal;
        if (!goal) return;
        setGoalLabel(
          goal.goal_text ?? doc.suggestions.find((s) => s.key === goal.goal_key)?.label ?? null,
        );
      })
      .catch(() => {
        /* метка — украшение; ключ покажется и без неё */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedActions = (): PlanLiteActionSpec[] => {
    const out: PlanLiteActionSpec[] = [];
    if (book) out.push({ action_type: "book_service", cadence: "per_week", target_count: 1 });
    if (foodDays !== null) out.push({ action_type: "log_food", cadence: "per_week", target_count: foodDays });
    if (waterTimes !== null) out.push({ action_type: "log_water", cadence: "per_day", target_count: waterTimes });
    return out;
  };

  const compose = async () => {
    const actions = selectedActions();
    if (!actions.length || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const plan = await createPlanLite(actions);
      if (!alive.current) return;
      setStatus({ kind: "card", plan });
    } catch (e) {
      if (!alive.current) return;
      const slug = refusalSlug(e);
      if (slug === "not_found") {
        navigate(GOAL_ROUTE, { state: { returnTo: PLAN_LITE_ROUTE } });
        return;
      }
      if (slug === "already_active") {
        await load();
        return;
      }
      if (slug === "plan_lite_disabled") {
        setStatus({ kind: "unavailable" });
        return;
      }
      setNotice(PLAN_LITE_COPY.transient);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const change = async () => {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await closePlanLite();
      if (!alive.current) return;
      setBook(false);
      setFoodDays(null);
      setWaterTimes(null);
      setStatus({ kind: "builder" });
    } catch (e) {
      if (!alive.current) return;
      // Активного плана уже нет — значит, конструктор и есть правда.
      if (refusalSlug(e) === "not_found") setStatus({ kind: "builder" });
      else setNotice(PLAN_LITE_COPY.transient);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button type="button" className="records-screen__back" aria-label="Назад" onClick={onBack}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M12 4l-6 6 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <h1 className="records-screen__title">{PLAN_LITE_COPY.title}</h1>
      </header>

      <main className="food-scanner-screen__main">
        <div className="food-scanner-diary__notice-slot" aria-live="polite">
          {notice && (
            <div className="food-scanner-diary__notice">
              <span>{notice}</span>
            </div>
          )}
        </div>

        {status.kind === "loading" && (
          <p className="food-scanner-diary__caption" role="status">
            {PLAN_LITE_COPY.loading}
          </p>
        )}

        {status.kind === "unavailable" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{PLAN_LITE_COPY.unavailable}</p>
          </div>
        )}

        {status.kind === "error" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{PLAN_LITE_COPY.transient}</p>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              {PLAN_LITE_COPY.retry}
            </button>
          </div>
        )}

        {status.kind === "builder" && (
          <section aria-label={PLAN_LITE_COPY.builderTitle}>
            {goalLabel && <p className="food-scanner-diary__caption">{PLAN_LITE_COPY.goalTitle(goalLabel)}</p>}
            <h2 className="food-scanner-diary__caption">{PLAN_LITE_COPY.builderTitle}</h2>
            <p className="food-scanner-diary__unreadable-hint">{PLAN_LITE_COPY.builderHint}</p>
            <div className="chip-row" role="group" aria-label={PLAN_LITE_COPY.builderTitle}>
              <label className={`chip${book ? " chip--active" : ""}`}>
                <input type="checkbox" checked={book} onChange={() => setBook((b) => !b)} />
                {PLAN_LITE_COPY.chipBook}
              </label>
              <label className={`chip${foodDays !== null ? " chip--active" : ""}`}>
                <input
                  type="checkbox"
                  checked={foodDays !== null}
                  onChange={() => setFoodDays((d) => (d === null ? FOOD_DAYS.initial : null))}
                />
                {PLAN_LITE_COPY.chipFood(foodDays ?? FOOD_DAYS.initial)}
              </label>
              <label className={`chip${waterTimes !== null ? " chip--active" : ""}`}>
                <input
                  type="checkbox"
                  checked={waterTimes !== null}
                  onChange={() => setWaterTimes((t) => (t === null ? WATER_TIMES.initial : null))}
                />
                {PLAN_LITE_COPY.chipWater(waterTimes ?? WATER_TIMES.initial)}
              </label>
            </div>
            {foodDays !== null && (
              <Stepper
                label={PLAN_LITE_COPY.labelFood}
                value={foodDays}
                min={FOOD_DAYS.min}
                max={FOOD_DAYS.max}
                onChange={setFoodDays}
              />
            )}
            {waterTimes !== null && (
              <Stepper
                label={PLAN_LITE_COPY.labelWater}
                value={waterTimes}
                min={WATER_TIMES.min}
                max={WATER_TIMES.max}
                onChange={setWaterTimes}
              />
            )}
            <div className="food-scanner-screen__cta-stack">
              <button
                type="button"
                className="btn-primary"
                disabled={busy || selectedActions().length === 0}
                onClick={() => void compose()}
              >
                {busy ? PLAN_LITE_COPY.composing : PLAN_LITE_COPY.compose}
              </button>
            </div>
          </section>
        )}

        {status.kind === "card" && (
          <section data-testid="plan-lite-card" aria-label={PLAN_LITE_COPY.title}>
            <h2 className="food-scanner-diary__caption">
              {PLAN_LITE_COPY.goalTitle(goalLabel ?? status.plan.goal_key)}
            </h2>
            <ul className="food-scanner-diary__list">
              {status.plan.actions.map((action) => (
                <li key={action.action_type} className="food-scanner-diary__entry">
                  <div className="food-scanner-diary__entry-main">
                    <span className="food-scanner-diary__entry-dish">{ACTION_LABELS[action.action_type]}</span>
                    <span className="food-scanner-diary__entry-time">{bucketLabel(action)}</span>
                  </div>
                  <span className="food-scanner-diary__entry-cal">
                    {PLAN_LITE_COPY.ofTotal(action.done_count, action.target_count)}
                  </span>
                  <div className="food-scanner-diary__entry-actions">
                    <button
                      type="button"
                      className="food-scanner-diary__entry-action"
                      aria-label={`${PLAN_LITE_COPY.go}: ${ACTION_LABELS[action.action_type]}`}
                      onClick={() => navigate(ACTION_ROUTES[action.action_type])}
                    >
                      {PLAN_LITE_COPY.go}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <div className="food-scanner-screen__cta-stack">
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void change()}>
                {busy ? PLAN_LITE_COPY.changing : PLAN_LITE_COPY.change}
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function bucketLabel(action: PlanLiteAction): string {
  return action.cadence === "per_day" ? PLAN_LITE_COPY.today : PLAN_LITE_COPY.thisWeek;
}

function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="food-scanner-diary__entry-actions" role="group" aria-label={label}>
      <button
        type="button"
        className="food-scanner-diary__entry-action"
        aria-label={`${label}: ${PLAN_LITE_COPY.less}`}
        disabled={value <= min}
        onClick={() => onChange(Math.max(min, value - 1))}
      >
        −
      </button>
      <span className="food-scanner-diary__entry-time">{value}</span>
      <button
        type="button"
        className="food-scanner-diary__entry-action"
        aria-label={`${label}: ${PLAN_LITE_COPY.more}`}
        disabled={value >= max}
        onClick={() => onChange(Math.min(max, value + 1))}
      >
        +
      </button>
    </div>
  );
}
