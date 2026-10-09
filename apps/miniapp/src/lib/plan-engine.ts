/**
 * Сохранённый план нового механизма — клиент Mini App (DRF-2876).
 *
 * Решение владельца (лист 07.10, п.9–10): раздел «Мой план» один; сохранённый
 * новый план — основной, прежний показывается, пока нового нет. Подписи шагов
 * — утверждённый клиентский текст каталога; ключей способностей в ответе нет.
 *
 * Источник — каталог через прокси бота (`customer/plan/current`). Экран —
 * не второй источник истины: он показывает то, что сохранено на сервере.
 */
import { ApiError, request } from "./api";

export interface SavedPlanStep {
  step_id: string;
  /** Подпись каталога — показывается как есть. */
  label: string;
  /**
   * «Почему этот шаг?» — курируемый «ожидаемый эффект» способности из
   * каталога, показывается как есть. `null` или ключа нет — текста нет:
   * ссылка не рисуется, текст не сочиняется.
   */
  why?: string | null;
}

export interface SavedPlan {
  plan_id: string;
  steps: SavedPlanStep[];
}

/**
 * Сохранённый план или `null`.
 *
 * `null` в двух случаях, и оба значат «показывай прежний план»: нового плана
 * нет, либо новый механизм выключен на сервере (`plan_engine_disabled`).
 * Любой другой отказ пробрасывается: подставить прежний план вместо
 * сохранённого нового, который не удалось прочитать, было бы неправдой.
 */
export async function getSavedPlan(): Promise<SavedPlan | null> {
  return (await getSavedPlanState()).plan;
}

/**
 * Предложение плана — новый план, сохранённый рядом с действующим. Действующим
 * он становится только после отдельного «Заменить план» (решение владельца:
 * сохранённый новый план не вытесняет действующий без подтверждения замены).
 */
export interface PlanProposal extends SavedPlan {
  /** Действующий план, о замене которого экран спрашивает человека. */
  replaces_plan_id: string;
}

export interface SavedPlanState {
  plan: SavedPlan | null;
  proposal: PlanProposal | null;
}

/** Действующий план и предложение рядом с ним; то же правило `null`, что у `getSavedPlan`. */
export async function getSavedPlanState(): Promise<SavedPlanState> {
  try {
    const res = await request<{ plan: SavedPlan | null; proposal?: PlanProposal | null }>("/plan/current");
    return { plan: res.plan ?? null, proposal: res.proposal ?? null };
  } catch (e) {
    if (e instanceof ApiError && e.slug === "plan_engine_disabled") return { plan: null, proposal: null };
    throw e;
  }
}

/**
 * «Заменить план». Сервер шлёт замену с оценкой безопасности последнего хода
 * чата; отказы приходят слагами: `plan_safety_unavailable` (оценки нет —
 * нужен ход в чате), `plan_safety_blocked`, `plan_replacement_target_changed`,
 * `plan_proposal_expired`, отказы гейта согласия. `false` — замена уже была
 * выполнена (повторное нажатие).
 */
export async function replacePlan(proposal: PlanProposal): Promise<boolean> {
  const res = await request<{ replaced: boolean }>("/plan/replace", {
    method: "POST",
    body: JSON.stringify({ plan_id: proposal.plan_id, replaces_plan_id: proposal.replaces_plan_id }),
  });
  return res.replaced;
}

/** «Оставить текущий» — предложение уходит в архив, действующий план не меняется. */
export async function keepCurrentPlan(proposal: PlanProposal): Promise<void> {
  await request<{ kept: boolean }>("/plan/keep", {
    method: "POST",
    body: JSON.stringify({ plan_id: proposal.plan_id }),
  });
}
