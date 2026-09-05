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
}

/** Server-computed position of the current question. Never derived here. */
export interface AnketaProgress {
  index: number;
  total: number;
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
   * There is deliberately no "is this the last one" flag and no list
   * of remaining steps: the sequence is the server's, and the screen
   * must not be able to compute what comes next. `progress` arrives
   * ready-made for the same reason.
   */
  step?: string;
  options?: AnketaOption[];
  allow_free_text?: boolean;
  progress?: AnketaProgress;
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

export interface DecisionContext {
  version: number;
  known: { goal: KnownGoal | null };
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
  | { answer: { step: string; text: string }; source_channel: "miniapp" };

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
