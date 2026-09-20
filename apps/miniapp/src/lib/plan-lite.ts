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
 *
 * План-A (DRF-2123): `customer/plan-lite/proposal` — предложение из шаблона
 * активной цели (`goal_key`, `why`, `template_version`, действия); ничего не
 * создаёт. Подтверждение — тот же POST с `template_version`: каталог пишет
 * провенанс плана `source=template:<goal_key>:v<N>`. Каденс `per_2_weeks` —
 * ведро 14 дней от создания плана.
 */
import { request } from "./api";

export type PlanLiteActionType = "book_service" | "log_food" | "log_water";
export type PlanLiteCadence = "per_day" | "per_week" | "per_2_weeks";

export interface PlanLiteAction {
  action_type: PlanLiteActionType;
  cadence: PlanLiteCadence;
  target_count: number;
  /** Факты за текущее ведро, не больше target_count. */
  done_count: number;
  /**
   * DRF-2124, только у `log_food`: дни ведра с суммой ≤ подтверждённого
   * ориентира; `null` — ориентира нет (не 0, §103). Ключа может не быть
   * (вода, бронь, старый каталог) — экран показывает строку только при числе.
   */
  within_target_count?: number | null;
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

/** Предложение из шаблона цели (DRF-2123) — форма действий, ничего о результате. */
export interface PlanLiteProposal {
  /** Курируемый ключ активной цели — не текст человека. */
  goal_key: string;
  /** Текст шаблона «почему» — курируемый, один для всех с этой целью. */
  why: string;
  template_version: number;
  actions: PlanLiteActionSpec[];
}

export async function getPlanLite(): Promise<PlanLite | null> {
  const res = await request<{ plan_lite: PlanLite | null }>("/plan-lite");
  return res.plan_lite ?? null;
}

/**
 * Предложение из шаблона активной цели. Отказы слагами: `no_active_goal`
 * (цели нет), `no_template` (у цели нет шаблона — не ошибка для экрана),
 * `plan_lite_disabled`.
 */
export async function getPlanLiteProposal(): Promise<PlanLiteProposal> {
  const res = await request<{ proposal: PlanLiteProposal }>("/plan-lite/proposal");
  return res.proposal;
}

/**
 * Составить план. `templateVersion` — когда человек подтвердил предложение
 * (провенанс плана); без него ключа в теле нет — не `null`.
 */
export async function createPlanLite(
  actions: PlanLiteActionSpec[],
  templateVersion?: number,
): Promise<PlanLite> {
  const body = templateVersion === undefined ? { actions } : { actions, template_version: templateVersion };
  const res = await request<{ plan_lite: PlanLite }>("/plan-lite", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return res.plan_lite;
}

export async function closePlanLite(): Promise<void> {
  await request<{ closed: boolean }>("/plan-lite", { method: "DELETE" });
}
