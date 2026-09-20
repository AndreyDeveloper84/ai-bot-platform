/**
 * «Мой план» — Plan Lite без веса (DRF-2101, решение владельца §49).
 *
 * Route: `/customer/plan`. Входы — вкладка «План» нижней панели, карточка
 * цели на Главной («Продолжить сегодняшний план» / «Составить план») и «Мой
 * план» с экрана цели (C01). Флага сборки нет (DRF-2144, §55 б): включён ли
 * план, решает сервер — 404 `plan_lite_disabled` → «пока недоступен».
 *
 * Что здесь:
 *   - плана нет → сперва ПРЕДЛОЖЕНИЕ из шаблона цели (DRF-2123, План-A):
 *     «Ayla предлагает для цели «{метка}»», текст «почему» и строки
 *     действий со степперами (числа — из предложения; пункт можно снять) →
 *     «Подтвердить план» → POST `actions` + `template_version`; ссылка
 *     «Собрать самому» → прежний конструктор. У цели нет шаблона
 *     (`no_template`) → сразу конструктор, без блока и без ошибки. Нет
 *     активной цели (`no_active_goal`) → «сначала выбери цель»;
 *   - конструктор: три обязательства-ДЕЙСТВИЯ («Записаться на услугу под
 *     цель», «Вести дневник N дней в неделю», «Пить воду N раз в день»),
 *     выбрать 1–3 → «Составить план» → POST только `actions` (активную
 *     цель знает каталог);
 *   - план есть → карточка: «Твоя цель: {метка}» (метка — из
 *     decision-context, как на экране цели; иначе ключ) и по обязательству
 *     «N из M» за текущее ведро — и ничего о результате: ни процента цели,
 *     ни шкалы, ни «ты пропустил» (В-5, DRF-1332); у дневника при
 *     подтверждённом ориентире — ещё «в ориентире N» (DRF-2124: второй
 *     факт, не оценка; `null` — строки нет); каждое обязательство
 *     ведёт туда, где оно делается (каталог / дневник / вода);
 *   - «Изменить план» = закрыть (append-only) и составить заново.
 *
 * Строка «дневник» в предложении без согласия дневника (тот же гейт, что у
 * сканера — `fetchDiaryConsentGate`) помечается «Нужно согласие», снимается
 * и в POST не уходит; пометка ведёт на экран согласия сканера с возвратом
 * сюда. Гейт не ответил — читается как «согласия нет» (fail-closed).
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
import { fetchDiaryConsentGate } from "../lib/food-scanner";
import {
  closePlanLite,
  createPlanLite,
  getPlanLite,
  getPlanLiteProposal,
  type PlanLite,
  type PlanLiteAction,
  type PlanLiteActionSpec,
  type PlanLiteActionType,
  type PlanLiteCadence,
  type PlanLiteProposal,
} from "../lib/plan-lite";
import { screenRoot } from "../lib/screen-back";
import { CustomerTabBar } from "../components/CustomerTabBar";

export const PLAN_LITE_ROUTE = "/customer/plan";
const GOAL_ROUTE = "/customer/goal-select";
/** Гейт согласия дневника живёт в экране съёмки — тот же адрес, что у недели/дня. */
const CONSENT_GATE_ROUTE = "/customer/food-scanner/capture";

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
  twoWeeks: "Эти 2 недели",
  ofTotal: (done: number, target: number) => `${done} из ${target}`,
  /** DRF-2124 — второй факт дневника при подтверждённом ориентире; не оценка. */
  withinTarget: (n: number) => `в ориентире ${n}`,
  go: "Перейти",
  change: "Изменить план",
  changing: "Закрываю…",
  needGoal: "Сначала выбери цель — план строится от неё.",
  chooseGoal: "Выбрать цель",
  unavailable: "«Мой план» пока недоступен.",
  transient: "Не получилось прочитать план — сервис не отвечает. Попробуй чуть позже.",
  retry: "Повторить",
  labelBook: "Записаться на услугу",
  labelFood: "Дневник",
  labelWater: "Вода",
  // План-A (DRF-2123)
  proposalTitle: (goal: string) => `Ayla предлагает для цели «${goal}»`,
  proposalHint: "Можно снять пункт или поменять число — план останется про действия.",
  confirm: "Подтвердить план",
  confirming: "Подтверждаю…",
  byHand: "Собрать самому",
  needConsent: "Нужно согласие",
  perDay: "в день",
  perWeek: "в неделю",
  per2Weeks: "в 2 недели",
  unitDays: "дн.",
  unitTimes: "раз",
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

const CADENCE_PERIOD: Record<PlanLiteCadence, string> = {
  per_day: PLAN_LITE_COPY.perDay,
  per_week: PLAN_LITE_COPY.perWeek,
  per_2_weeks: PLAN_LITE_COPY.per2Weeks,
};

const FOOD_DAYS = { min: 1, max: 7, initial: 3 };
const WATER_TIMES = { min: 1, max: 14, initial: 7 };
/** Степпер предложения: дневник — дни ведра, остальное — до 14 раз. */
const PROPOSAL_MAX = 14;
const BUCKET_DAYS: Record<PlanLiteCadence, number> = { per_day: 1, per_week: 7, per_2_weeks: 14 };

type Status =
  | { kind: "loading" }
  | { kind: "builder" }
  | { kind: "proposal"; proposal: PlanLiteProposal }
  | { kind: "need_goal" }
  | { kind: "card"; plan: PlanLite }
  | { kind: "unavailable" }
  | { kind: "error" };

/** Строка предложения — пункт из шаблона с правкой человека. */
interface ProposalRow {
  action_type: PlanLiteActionType;
  cadence: PlanLiteCadence;
  count: number;
  included: boolean;
}

function refusalSlug(e: unknown): string | null {
  return e instanceof ApiError ? e.slug : null;
}

function rowMax(row: ProposalRow): number {
  return row.action_type === "log_food" ? BUCKET_DAYS[row.cadence] : PROPOSAL_MAX;
}

/** «1 раз в 2 недели», «3 дн. в неделю», «6 раз в день». */
function cadenceLabel(row: ProposalRow): string {
  const unit = row.action_type === "log_food" ? PLAN_LITE_COPY.unitDays : PLAN_LITE_COPY.unitTimes;
  return `${row.count} ${unit} ${CADENCE_PERIOD[row.cadence]}`;
}

export function PlanLiteScreen() {
  const navigate = useNavigate();
  // DRF-2201 — «План» вкладка панели, значит корень: стрелки «назад» у него
  // нет (ни нарисованной, ни системной в MAX), уход — другими вкладками.
  // Прежде стрелка вела на экран цели; такой дороги у корня быть не может —
  // вход на цель, если он нужен, живёт ссылкой в карточке цели, не стрелкой.
  useScreenBack(
    screenRoot(
      "«План» — вкладка нижней панели (макет DRF-1321, §55 б): выше неё " +
        "ничего нет, а уход с экрана — соседние вкладки.",
    ),
  );

  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [goalLabel, setGoalLabel] = useState<string | null>(null);
  const [book, setBook] = useState(false);
  const [foodDays, setFoodDays] = useState<number | null>(null);
  const [waterTimes, setWaterTimes] = useState<number | null>(null);
  const [rows, setRows] = useState<ProposalRow[]>([]);
  // Согласие дневника: null — ещё не спрошено / неизвестно. Строка
  // «дневник» уходит в POST только при true (fail-closed).
  const [diaryConsent, setDiaryConsent] = useState<boolean | null>(null);
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
    setStatus({ kind: "loading" });
    setNotice(null);
    let plan: PlanLite | null;
    try {
      plan = await getPlanLite();
    } catch (e) {
      if (!alive.current) return;
      setStatus(refusalSlug(e) === "plan_lite_disabled" ? { kind: "unavailable" } : { kind: "error" });
      return;
    }
    if (!alive.current) return;
    if (plan) {
      setStatus({ kind: "card", plan });
      return;
    }
    // Плана нет — спросить предложение из шаблона (План-A).
    try {
      const proposal = await getPlanLiteProposal();
      if (!alive.current) return;
      setRows(
        proposal.actions.map((a) => ({
          action_type: a.action_type,
          cadence: a.cadence,
          count: Math.max(1, a.target_count),
          included: true,
        })),
      );
      setStatus({ kind: "proposal", proposal });
    } catch (e) {
      if (!alive.current) return;
      const slug = refusalSlug(e);
      if (slug === "no_template") setStatus({ kind: "builder" });
      else if (slug === "no_active_goal") setStatus({ kind: "need_goal" });
      else if (slug === "plan_lite_disabled") setStatus({ kind: "unavailable" });
      else setStatus({ kind: "error" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Метка цели — как на экране цели: текст человека, иначе подпись
  // курируемой цели из документа; сервер отдаёт лишь ключ. Не смогли
  // спросить — показываем ключ, план от этого не зависит.
  useEffect(() => {
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

  // Согласие дневника — только когда в предложении есть строка «дневник»;
  // тот же гейт, что у сканера. Не ответил — «согласия нет».
  const proposalHasFood = status.kind === "proposal" && rows.some((r) => r.action_type === "log_food");
  useEffect(() => {
    if (!proposalHasFood) return;
    let cancelled = false;
    fetchDiaryConsentGate()
      .then((gate) => {
        if (!cancelled) setDiaryConsent(gate.grantedAt !== null);
      })
      .catch(() => {
        if (!cancelled) setDiaryConsent(false);
      });
    return () => {
      cancelled = true;
    };
  }, [proposalHasFood]);

  const rowEffective = (row: ProposalRow): boolean =>
    row.included && (row.action_type !== "log_food" || diaryConsent === true);

  const proposalActions = (): PlanLiteActionSpec[] =>
    rows
      .filter(rowEffective)
      .map((r) => ({ action_type: r.action_type, cadence: r.cadence, target_count: r.count }));

  const selectedActions = (): PlanLiteActionSpec[] => {
    const out: PlanLiteActionSpec[] = [];
    if (book) out.push({ action_type: "book_service", cadence: "per_week", target_count: 1 });
    if (foodDays !== null) out.push({ action_type: "log_food", cadence: "per_week", target_count: foodDays });
    if (waterTimes !== null) out.push({ action_type: "log_water", cadence: "per_day", target_count: waterTimes });
    return out;
  };

  const submit = async (actions: PlanLiteActionSpec[], templateVersion?: number) => {
    if (!actions.length || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const plan = await createPlanLite(actions, templateVersion);
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

  const compose = () => submit(selectedActions());

  const confirmProposal = () => {
    if (status.kind !== "proposal") return;
    return submit(proposalActions(), status.proposal.template_version);
  };

  const toBuilder = () => {
    setBook(false);
    setFoodDays(null);
    setWaterTimes(null);
    setStatus({ kind: "builder" });
  };

  const toggleRow = (type: PlanLiteActionType) =>
    setRows((rs) => rs.map((r) => (r.action_type === type ? { ...r, included: !r.included } : r)));

  const setRowCount = (type: PlanLiteActionType, count: number) =>
    setRows((rs) => rs.map((r) => (r.action_type === type ? { ...r, count } : r)));

  const change = async () => {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await closePlanLite();
      if (!alive.current) return;
      toBuilder();
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
    <div className="food-scanner-screen food-scanner-screen--tab-root">
      <header className="records-screen__header">
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

        {status.kind === "need_goal" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{PLAN_LITE_COPY.needGoal}</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate(GOAL_ROUTE, { state: { returnTo: PLAN_LITE_ROUTE } })}
            >
              {PLAN_LITE_COPY.chooseGoal}
            </button>
          </div>
        )}

        {status.kind === "proposal" && (
          <section data-testid="plan-lite-proposal" aria-label={PLAN_LITE_COPY.title}>
            <h2 className="food-scanner-diary__caption">
              {PLAN_LITE_COPY.proposalTitle(goalLabel ?? status.proposal.goal_key)}
            </h2>
            <p className="food-scanner-diary__unreadable-hint">{status.proposal.why}</p>
            <p className="food-scanner-diary__caption">{PLAN_LITE_COPY.proposalHint}</p>
            <ul className="food-scanner-diary__list">
              {rows.map((row) => {
                const needsConsent = row.action_type === "log_food" && diaryConsent !== true;
                const on = rowEffective(row);
                return (
                  <li key={row.action_type} className="food-scanner-diary__entry">
                    <div className="food-scanner-diary__entry-main">
                      <label className={`chip${on ? " chip--active" : ""}`}>
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={needsConsent}
                          onChange={() => toggleRow(row.action_type)}
                        />
                        {ACTION_LABELS[row.action_type]}
                      </label>
                      <span className="food-scanner-diary__entry-time">{cadenceLabel(row)}</span>
                    </div>
                    {needsConsent && diaryConsent === false && (
                      <div className="food-scanner-diary__entry-actions">
                        <button
                          type="button"
                          className="food-scanner-diary__entry-action"
                          onClick={() =>
                            navigate(CONSENT_GATE_ROUTE, { state: { returnTo: PLAN_LITE_ROUTE } })
                          }
                        >
                          {PLAN_LITE_COPY.needConsent}
                        </button>
                      </div>
                    )}
                    {on && (
                      <Stepper
                        label={ACTION_LABELS[row.action_type]}
                        value={row.count}
                        min={1}
                        max={rowMax(row)}
                        onChange={(v) => setRowCount(row.action_type, v)}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="food-scanner-screen__cta-stack">
              <button
                type="button"
                className="btn-primary"
                disabled={busy || proposalActions().length === 0}
                onClick={() => void confirmProposal()}
              >
                {busy ? PLAN_LITE_COPY.confirming : PLAN_LITE_COPY.confirm}
              </button>
              <button type="button" className="btn-secondary" disabled={busy} onClick={toBuilder}>
                {PLAN_LITE_COPY.byHand}
              </button>
            </div>
          </section>
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
                    {action.action_type === "log_food" && typeof action.within_target_count === "number" && (
                      <>
                        {", "}
                        {PLAN_LITE_COPY.withinTarget(action.within_target_count)}
                      </>
                    )}
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

      {/* Панель — вкладка этого экрана (DRF-2201); стоит вне веток состояния:
          состояние ошибки не убирает навигацию (#1918). */}
      <CustomerTabBar active="plan" />
    </div>
  );
}

function bucketLabel(action: PlanLiteAction): string {
  if (action.cadence === "per_day") return PLAN_LITE_COPY.today;
  if (action.cadence === "per_2_weeks") return PLAN_LITE_COPY.twoWeeks;
  return PLAN_LITE_COPY.thisWeek;
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
