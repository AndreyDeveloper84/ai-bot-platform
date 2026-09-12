/**
 * Customer goal-select client lib (DRF-1190).
 *
 * The screen is a DUMB RENDERER: the backend owns every decision and
 * returns a "decision context" document; the frontend only displays it
 * and POSTs user actions back, replacing the document with whatever
 * the server returns. No hardcoded chip lists, no local
 * what-to-show-next logic, no next-screen branching.
 *
 * Proxy envelope: both endpoints wrap the document as `{"data": …}`
 * (same unwrap pattern as `master-api.ts::getBillingStatus`).
 */

import { request } from "./api";

// ---------------------------------------------------------------------------
// Decision-context document (server contract, version 1).
// ---------------------------------------------------------------------------

export type GoalSourceChannel = "bot" | "miniapp";

export interface KnownGoal {
  goal_key: string | null;
  goal_text: string | null;
  selected_at: string; // ISO 8601
  source_channel: GoalSourceChannel;
}

export type MissingKind =
  | "goal"
  | "goal_clarification"
  | "goal_guidance"
  | "goal_anketa";

/** One anketa answer option — render the label, post back the key. */
export interface AnketaOption {
  key: string;
  label: string;
  /**
   * DRF-1747 — роль опции. `escape` = «Не знаю»: полноценный ответ,
   * рисуется тихо и отдельно от вариантов; тап шлёт `{option_key}` и на
   * multi-шаге тоже (заменяет отмеченное). Отсутствие роли — обычный
   * вариант.
   */
  role?: "escape" | string;
}

/** Server-computed position of the current question. Never derived here. */
/**
 * Где человек в проходе. `index`/`total` экран больше НЕ рисует (DRF-1743,
 * доктрина 12.09): «Вопрос 2 из 3» — счётчик, честный лишь пока порядок
 * вопросов фиксирован; с движком вопросов общее число неизвестно
 * заранее, и число стало бы выдумкой. Рисуется только `is_last` — факт,
 * который сервер гарантирует («Ещё один короткий вопрос»). Числа
 * остаются в типе на один релиз, пока сервер их шлёт.
 */
export interface AnketaProgress {
  index?: number;
  total?: number;
  is_last?: boolean;
}

export interface MissingItem {
  kind: MissingKind;
  /** Question/prompt text to render verbatim. */
  prompt: string;
  /**
   * DRF-1451 — anketa step fields. Present on `kind: "goal_anketa"`.
   *
   * `step` is an opaque token: the screen echoes it back with the
   * answer and never interprets it. It exists so a stale answer is
   * refused by the server (409) instead of being filed under the
   * wrong question — NOT so the client can choose a step.
   *
   * There is deliberately no list of remaining steps: the sequence is
   * the server's, and the screen must not be able to compute what comes
   * next. `progress.is_last` is a server-guaranteed fact rendered as
   * one phrase, not material for arithmetic (DRF-1743).
   */
  step?: string;
  options?: AnketaOption[];
  allow_free_text?: boolean;
  progress?: AnketaProgress;
  /**
   * DRF-1746 — тип ответа по смыслу. Отсутствие = `single`; незнакомое
   * значение экран рисует как `single` (к простому, не к пустому).
   * `multi` отвечает `{option_keys: [...]}` одним запросом, `scale` —
   * деления в порядке `options` с подписями концов, `text` — короткое
   * поле с `text_limit`. `confirm` — компонент подтверждения (DRF-1745).
   */
  mode?: "single" | "multi" | "confirm" | "scale" | "text" | string;
  scale?: { low_label: string; high_label: string };
  text_limit?: number;
  /**
   * DRF-1745 — подтверждение известного (`mode: "confirm"`). `prompt` —
   * вопрос подтверждения («Раньше ты выбирала «X». Всё ещё так?»),
   * `question` — обычный вопрос шага (для «Изменилось»), `answer_mode` —
   * каким компонентом на него отвечать, `known_value` — что подтверждаем.
   * «Да» шлёт `{confirm: true}`; значение экран не пересылает.
   */
  known_value?: {
    option_key: string | null;
    option_keys?: string[];
    text?: string | null;
    label: string;
  };
  question?: string;
  answer_mode?: string;
}

export interface GoalSuggestion {
  key: string;
  label: string;
}

export type GoalIntentId =
  | "choose_suggested"
  | "formulate_own"
  | "need_guidance"
  /** DRF-1225 / DRF-1451 — pass the anketa again, any number of times. */
  | "start_anketa";

export interface GoalIntent {
  id: GoalIntentId;
  label: string;
}

/**
 * Where to send the person when there is nothing left to ask (DRF-1451).
 *
 * The id is a route contract, exactly like the bot's start-param slugs
 * in `max-sdk.ts::_ROUTE_MAP`. The server names the destination; the
 * client maps the id to a path. `null` — the server is still asking.
 *
 * This is the one place where "what comes next" moved ONTO the server:
 * before it, the screen simply re-rendered after a goal was chosen and
 * nobody decided anything.
 */
export interface NextStep {
  /**
   * DRF-1481 (решение владельца §24.1) — произвольная строка, а не
   * перечень известных назначений. Единственное решающее место — таблица
   * маршрутов экрана (`NEXT_ROUTES` в `GoalSelectScreen`): тип, копирующий
   * её содержимое, расходился бы с сервером молча — Python вправе
   * завести новое назначение, не спросив клиентские типы. Незнакомый id
   * безопасен по построению: назначение вне таблицы не даёт кнопки, а
   * guard DRF-1483 в том документе ставит запасной выход.
   */
  id: string;
  label: string;
}

/**
 * Строка блока «Уже учла» (DRF-1744): ответ на шаг открытого прохода,
 * как его прислал сервер. Несёт `options` шага, чтобы «Изменить» было
 * чем ответить без списка вопросов на клиенте; `revisable` — решение
 * сервера, экран его не выводит.
 */
export interface KnownAnketaAnswer {
  /** DRF-1746 — ключи multi-ответа; [] у остальных режимов. */
  option_keys?: string[];
  mode?: string;
  /** DRF-1747 — ответ «Не знаю»: сказанное, но не известный факт. */
  unknown?: boolean;
  /** Происхождение факта: conversation / anketa / operator. */
  origin?: string;
  step: string;
  prompt: string;
  option_key: string | null;
  label: string;
  options: AnketaOption[];
  revisable: boolean;
}

export interface DecisionContext {
  version: number;
  known: {
    goal: KnownGoal | null;
    /** Absent on documents before DRF-1744 — then there is no block. */
    anketa?: KnownAnketaAnswer[];
  };
  missing: MissingItem[];
  suggestions: GoalSuggestion[];
  intents: GoalIntent[];
  /** Absent on version 1 documents. */
  next?: NextStep | null;
}

interface DecisionContextEnvelope {
  data: DecisionContext;
}

/**
 * POST /goals/select body — exactly one of the variants.
 *
 * DRF-1451 added `answer` (one anketa step) and `intent: "start_anketa"`.
 * The three original variants are untouched: the anketa does not replace
 * them, it stands beside them — that is what keeps it from being a gate
 * (BOT-001 amendment A-1, §24, condition C-2).
 */
export type GoalSelectBody =
  | { goal_key: string; source_channel: "miniapp" }
  | { goal_text: string; source_channel: "miniapp" }
  | { intent: "need_guidance"; source_channel: "miniapp" }
  | { intent: "start_anketa"; source_channel: "miniapp" }
  | {
      answer: { step: string; option_key: string };
      source_channel: "miniapp";
    }
  /** DRF-1746 — режим multi: массив ключей одним ответом. */
  | {
      answer: { step: string; option_keys: string[] };
      source_channel: "miniapp";
    }
  /** DRF-1745 — «Да, всё так» на шаге подтверждения. */
  | {
      answer: { step: string; confirm: true };
      source_channel: "miniapp";
    }
  | { answer: { step: string; text: string }; source_channel: "miniapp" }
  /** DRF-1744 — пересмотр уже данного ответа («Изменить» в «Уже учла»). */
  | {
      answer: { step: string; option_key: string; revise: true };
      source_channel: "miniapp";
    };

/** GET /decision-context — current decision-context document. */
export const fetchDecisionContext = async (): Promise<DecisionContext> => {
  const env = await request<DecisionContextEnvelope>("/decision-context", {
    method: "GET",
  });
  return env.data;
};

/**
 * POST /goals/select — apply a user action (pick a suggestion, submit
 * free-form text, or ask for guidance). Returns the UPDATED document;
 * the caller replaces its state with it verbatim.
 */
export const postGoalSelect = async (
  body: GoalSelectBody,
): Promise<DecisionContext> => {
  const env = await request<DecisionContextEnvelope>("/goals/select", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return env.data;
};
