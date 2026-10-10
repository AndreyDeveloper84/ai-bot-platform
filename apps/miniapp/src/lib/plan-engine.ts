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
  /**
   * Время действующей записи, сделанной от этого шага, — в поясе салона
   * (часы берутся из строки). `null` или ключа нет — записи нет. Названий
   * услуги и мастера у записи здесь нет: каталог хранит у шага только время.
   */
  booked_at?: string | null;
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

/**
 * Несохранённое предложение, собранное в чате: шаги словами каталога и
 * опознаватель показанной карточки. Экран сохраняет именно показанное.
 */
export interface PlanDraft {
  token: string;
  steps: { label: string; why?: string | null }[];
}

export interface SavedPlanState {
  plan: SavedPlan | null;
  proposal: PlanProposal | null;
  /** `null` — предложения нет (или сервер его не отдаёт). */
  draft: PlanDraft | null;
}

/** Действующий план и предложение рядом с ним; то же правило `null`, что у `getSavedPlan`. */
export async function getSavedPlanState(): Promise<SavedPlanState> {
  try {
    const res = await request<{
      plan: SavedPlan | null;
      proposal?: PlanProposal | null;
      draft?: PlanDraft | null;
    }>("/plan/current");
    return { plan: res.plan ?? null, proposal: res.proposal ?? null, draft: res.draft ?? null };
  } catch (e) {
    if (e instanceof ApiError && e.slug === "plan_engine_disabled") {
      return { plan: null, proposal: null, draft: null };
    }
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

/**
 * «Сохранить» несохранённое предложение — без сообщения в чат (задание
 * владельца §9). Сервер сохраняет с оценкой безопасности последнего хода чата;
 * отказы слагами: `plan_safety_unavailable`, `plan_safety_blocked`,
 * `plan_proposal_expired` (карточка устарела), отказы гейта согласия.
 */
export async function saveDraft(draft: PlanDraft): Promise<void> {
  await request<{ saved: boolean }>("/plan/save", {
    method: "POST",
    body: JSON.stringify({ token: draft.token }),
  });
}

/** «Оставить текущий» — предложение уходит в архив, действующий план не меняется. */
export async function keepCurrentPlan(proposal: PlanProposal): Promise<void> {
  await request<{ kept: boolean }>("/plan/keep", {
    method: "POST",
    body: JSON.stringify({ plan_id: proposal.plan_id }),
  });
}

/**
 * Вариант «услуга × мастер», которым можно выполнить шаг. Все поля — слова
 * каталога; идентификаторов услуги и мастера экран не получает — выбор и
 * запись идут по номеру варианта и опознавателю подбора.
 */
export interface StepOption {
  service_name: string;
  salon_name: string | null;
  salon_city: string | null;
  master_name: string;
  price: string | null;
  duration_minutes: number | null;
  /** Адрес только подтверждённого места; `null` — адреса нет, ничего не подставляется. */
  place_address: string | null;
  /** Каталог пометил услугу синтетической (тестовой). */
  synthetic: boolean;
}

export interface StepOffers {
  /** Опознаватель подбора — с ним идут выбор услуги и запись. */
  token: string;
  options: StepOption[];
}

export interface StepSlots {
  token: string;
  option: StepOption;
  /** Дни со свободным временем (`ГГГГ-ММ-ДД`), ближайшие первыми. */
  days: string[];
  /** Показанный день; `null` — сервер его не назвал. */
  day: string | null;
  /** Времена показанного дня — в поясе мастера; пусто — в этом дне времени уже нет. */
  slots: string[];
}

async function stepAction<T>(
  action: "offers" | "choose" | "day" | "book",
  token: string,
  index: number,
): Promise<T> {
  return request<T>("/plan/step", { method: "POST", body: JSON.stringify({ action, token, index }) });
}

/**
 * Услуги для шага сохранённого плана — тот же подбор, что по кнопке шага в
 * чате. Сервер идёт с оценкой безопасности последнего хода чата; отказы
 * слагами: `plan_safety_unavailable` (нужен ход в чате), отказы гейта
 * согласия, причина каталога своим именем (`no_offer` и другие).
 */
export async function stepOffers(plan: SavedPlan, stepIndex: number): Promise<StepOffers> {
  const token = plan.plan_id.replace(/-/g, "").slice(0, 8).toLowerCase();
  return stepAction<StepOffers>("offers", token, stepIndex);
}

/** Выбор услуги: сервер фиксирует его в каталоге и отдаёт свободное время. */
export async function chooseStepOption(token: string, optionIndex: number): Promise<StepSlots> {
  return stepAction<StepSlots>("choose", token, optionIndex);
}

/** Другой день из показанных: сервер читает его время у каталога заново. */
export async function chooseStepDay(token: string, dayIndex: number): Promise<StepSlots> {
  return stepAction<StepSlots>("day", token, dayIndex);
}

/** Запись на время; возвращает время созданной записи. */
export async function bookStepSlot(token: string, slotIndex: number): Promise<string> {
  return (await stepAction<{ booked_at: string }>("book", token, slotIndex)).booked_at;
}
