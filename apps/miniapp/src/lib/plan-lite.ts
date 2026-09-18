/**
 * Plan Lite без веса — клиент Mini App (DRF-2101, решение владельца §49).
 *
 * План из 1–3 обязательств-ДЕЙСТВИЙ из уже выбранной цели; человек видит
 * только adherence «N из M» за текущее ведро каденса — никаких процентов
 * результата, шкал цели, «ты пропустил» (В-5, DRF-1332). Источник —
 * каталог через прокси бота (`customer/plan-lite`, #1842); наблюдений тела
 * на этой тропе нет по построению.
 *
 * POST шлёт ТОЛЬКО `actions`: активную цель знает каталог (PR-1b), а
 * decision-context отдаёт экрану ключ и текст цели, не её id.
 */
import { request } from "./api";

export type PlanLiteActionType = "book_service" | "log_food" | "log_water";
export type PlanLiteCadence = "per_day" | "per_week";

export interface PlanLiteAction {
  action_type: PlanLiteActionType;
  cadence: PlanLiteCadence;
  target_count: number;
  /** Факты за текущее ведро, не больше target_count. */
  done_count: number;
  bucket: { start: string; end: string };
}

export interface PlanLite {
  plan_id: string;
  /** Курируемый ключ цели — не текст человека. */
  goal_key: string;
  actions: PlanLiteAction[];
}

export interface PlanLiteActionSpec {
  action_type: PlanLiteActionType;
  cadence: PlanLiteCadence;
  target_count: number;
}

export async function getPlanLite(): Promise<PlanLite | null> {
  const res = await request<{ plan_lite: PlanLite | null }>("/plan-lite");
  return res.plan_lite ?? null;
}

export async function createPlanLite(actions: PlanLiteActionSpec[]): Promise<PlanLite> {
  const res = await request<{ plan_lite: PlanLite }>("/plan-lite", {
    method: "POST",
    body: JSON.stringify({ actions }),
  });
  return res.plan_lite;
}

export async function closePlanLite(): Promise<void> {
  await request<{ closed: boolean }>("/plan-lite", { method: "DELETE" });
}
