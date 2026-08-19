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

export type MissingKind = "goal" | "goal_clarification" | "goal_guidance";

export interface MissingItem {
  kind: MissingKind;
  /** Question/prompt text to render verbatim. */
  prompt: string;
}

export interface GoalSuggestion {
  key: string;
  label: string;
}

export type GoalIntentId = "choose_suggested" | "formulate_own" | "need_guidance";

export interface GoalIntent {
  id: GoalIntentId;
  label: string;
}

export interface DecisionContext {
  version: number;
  known: { goal: KnownGoal | null };
  missing: MissingItem[];
  suggestions: GoalSuggestion[];
  intents: GoalIntent[];
}

interface DecisionContextEnvelope {
  data: DecisionContext;
}

/** POST /goals/select body — exactly one of the three variants. */
export type GoalSelectBody =
  | { goal_key: string; source_channel: "miniapp" }
  | { goal_text: string; source_channel: "miniapp" }
  | { intent: "need_guidance"; source_channel: "miniapp" };

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
