/**
 * Manual booking draft — the rules, with no HTTP anywhere.
 *
 * Source: `ayla-knowledge/07 UX/Ayla Master Schedule UX Contract.md`
 * §12–18. This module is the part of manual booking that is ours to get
 * right regardless of what the canonical API turns out to look like: the
 * order of the flow, what invalidates what, and what may never happen
 * silently.
 *
 * ### The four rules that actually break in implementations
 *
 * 1. **Service precedes date/time.** §12: «Customer identifies the
 *    subject, Service determines duration/assignment context, and only
 *    then can a meaningful availability query be presented.» Asking
 *    availability without a service produces a slot list that means
 *    nothing, and the user will believe it.
 *
 * 2. **Changing date, service or assignment invalidates the interval.**
 *    §16, verbatim. This is the one that gets lost: a draft that keeps
 *    «15:00» after the service changed from 30 to 90 minutes is offering
 *    a slot that was never validated for the new duration.
 *
 * 3. **The system never silently selects or shifts the start time.**
 *    §12. So invalidation is not enough — it has to be *reported*, or
 *    the user sees their time vanish with no explanation and re-picks
 *    the same one. :func:`applyDraftAction` returns what it cleared.
 *
 * 4. **«Выбранное окно» is the range the draft started from, not the
 *    appointment duration.** §12, including the wording: the field is
 *    «Выбранное окно», never «Исходный интервал».
 *
 * The client also may not compute an authoritative slot from local data
 * (§17) — which is why nothing here derives availability. Slots arrive
 * from the canonical owner and this module only tracks which one is
 * selected and when that selection stops being trustworthy.
 */

/** An existing customer, as returned by search. */
export interface DraftExistingCustomer {
  kind: "existing";
  id: string;
  name: string;
  /** Masked for display — the draft never holds a raw phone. */
  phone_masked: string;
}

/**
 * A customer being created inline (§14: «Новый клиент → имя + телефон»).
 *
 * The raw phone lives here because the create command needs it, and only
 * until that command runs. Nothing renders it back and no draft is ever
 * persisted.
 */
export interface DraftNewCustomer {
  kind: "new";
  name: string;
  phone: string;
}

export type DraftCustomer = DraftExistingCustomer | DraftNewCustomer;

export interface DraftService {
  id: string;
  name: string;
  duration_min: number;
  /** Snapshot for the review screen; the commit boundary is authoritative. */
  price_from?: string | null;
}

export interface DraftMaster {
  id: string;
  name: string;
}

/**
 * The free interval the draft started from — display context only.
 *
 * §12: «This is the available range from which the draft started, not
 * Appointment duration.» Rendered as «Выбранное окно».
 */
export interface DraftWindow {
  start_at: string;
  end_at: string;
}

/**
 * A concrete start the user picked from canonically-supplied options.
 *
 * Shaped after what the schedule actually returns, now that
 * `/booking-slots/` is wired: `time` is always there, the full timestamp
 * only sometimes. Neither is reconstructed from the other — filling in a
 * missing `start_at` would mean the client choosing a timezone, and §17
 * rules that out.
 *
 * There is no `end_at`: the appointment's length comes from the selected
 * service, and storing a second copy here would be one more thing that
 * can disagree with it.
 */
export interface DraftSlot {
  /** `HH:MM` in the salon's timezone, as the schedule labelled it. */
  time: string;
  /** Full ISO start when the schedule sent one, otherwise null. */
  start_at: string | null;
}

export interface BookingDraft {
  customer: DraftCustomer | null;
  service: DraftService | null;
  master: DraftMaster | null;
  window: DraftWindow | null;
  slot: DraftSlot | null;
}

export const EMPTY_DRAFT: BookingDraft = {
  customer: null,
  service: null,
  master: null,
  window: null,
  slot: null,
};

export type DraftAction =
  | { type: "customer/set"; customer: DraftCustomer }
  | { type: "customer/clear" }
  | { type: "service/set"; service: DraftService }
  | { type: "master/set"; master: DraftMaster }
  | { type: "window/set"; window: DraftWindow }
  | { type: "slot/set"; slot: DraftSlot }
  | { type: "slot/clear" }
  | { type: "reset" };

/**
 * Why a previously chosen start was dropped.
 *
 * Exists so the UI can say it out loud. Rule 3 forbids the time changing
 * silently, and a cleared selection with no explanation is the same
 * failure wearing a different hat.
 */
export type SlotInvalidationReason = "service_changed" | "master_changed" | "window_changed";

export interface DraftTransition {
  draft: BookingDraft;
  /** Set when this action dropped a previously selected start. */
  slotInvalidatedBy?: SlotInvalidationReason;
}

function sameService(a: DraftService | null, b: DraftService): boolean {
  return a !== null && a.id === b.id && a.duration_min === b.duration_min;
}

function sameMaster(a: DraftMaster | null, b: DraftMaster): boolean {
  return a !== null && a.id === b.id;
}

function sameWindow(a: DraftWindow | null, b: DraftWindow): boolean {
  return a !== null && a.start_at === b.start_at && a.end_at === b.end_at;
}

/**
 * Apply one action to the draft.
 *
 * Pure. Returns the next draft plus, when a start was dropped, the
 * reason — see :type:`SlotInvalidationReason`.
 *
 * Note that re-selecting the *same* service or master is not a change
 * and does not invalidate anything. Treating an idempotent tap as a
 * change would clear a valid start for no reason, which is the same
 * broken promise as rule 3 from the other direction.
 */
export function applyDraftAction(
  draft: BookingDraft,
  action: DraftAction,
): DraftTransition {
  switch (action.type) {
    case "customer/set":
      // Customer does not participate in availability, so changing it
      // leaves the interval alone. §16 lists date, service and
      // assignment — not customer — and going back must not cost the
      // user their slot.
      return { draft: { ...draft, customer: action.customer } };

    case "customer/clear":
      return { draft: { ...draft, customer: null } };

    case "service/set": {
      if (sameService(draft.service, action.service)) {
        return { draft: { ...draft, service: action.service } };
      }
      const hadSlot = draft.slot !== null;
      return {
        draft: { ...draft, service: action.service, slot: null },
        ...(hadSlot ? { slotInvalidatedBy: "service_changed" as const } : {}),
      };
    }

    case "master/set": {
      if (sameMaster(draft.master, action.master)) {
        return { draft: { ...draft, master: action.master } };
      }
      const hadSlot = draft.slot !== null;
      return {
        draft: { ...draft, master: action.master, slot: null },
        ...(hadSlot ? { slotInvalidatedBy: "master_changed" as const } : {}),
      };
    }

    case "window/set": {
      if (sameWindow(draft.window, action.window)) {
        return { draft: { ...draft, window: action.window } };
      }
      const hadSlot = draft.slot !== null;
      return {
        draft: { ...draft, window: action.window, slot: null },
        ...(hadSlot ? { slotInvalidatedBy: "window_changed" as const } : {}),
      };
    }

    case "slot/set":
      return { draft: { ...draft, slot: action.slot } };

    case "slot/clear":
      return { draft: { ...draft, slot: null } };

    case "reset":
      return { draft: EMPTY_DRAFT };

    default:
      return { draft };
  }
}

/**
 * May we ask the canonical owner for intervals yet?
 *
 * §12: a meaningful availability query needs the service (for duration)
 * and the assignment context (which master). Without both, any list we
 * render is a guess, and §17 forbids the client from guessing.
 */
export function canQueryAvailability(draft: BookingDraft): boolean {
  return draft.service !== null && draft.master !== null;
}

/** Everything the review screen needs is present (§18). */
export function canReview(draft: BookingDraft): boolean {
  return (
    draft.customer !== null &&
    draft.service !== null &&
    draft.master !== null &&
    draft.slot !== null
  );
}

/**
 * Ordered list of what is still missing, for the primary screen's hint.
 *
 * Ordered by the contract's business order rather than by field
 * position, so the prompt always names the step the user should take
 * next rather than the first empty box on screen.
 */
export function missingSteps(draft: BookingDraft): string[] {
  const out: string[] = [];
  if (draft.customer === null) out.push("клиента");
  if (draft.service === null) out.push("услугу");
  if (draft.master === null) out.push("мастера");
  if (draft.slot === null) out.push("время");
  return out;
}

/**
 * What happened to a submitted draft (§18).
 *
 * `pending` is deliberately distinct from `committed`: «Do not claim
 * creation; show reconciliation/refresh path and idempotent retry
 * affordance.» A surface that renders an unknown outcome as success is
 * the same defect class this window has been fixing all day — an answer
 * that looks like an answer.
 */
export type SubmitOutcome =
  | "committed"
  | "conflict"
  | "blocked"
  | "pending"
  | "failed";

/** Copy for each outcome. Kept beside the enum so none can go unhandled. */
export const SUBMIT_OUTCOME_COPY: Record<SubmitOutcome, string> = {
  committed: "Запись создана.",
  conflict:
    "Это время уже занято — выберите другое. Клиент и услуга сохранены.",
  blocked: "Недостаточно прав для записи. Обратитесь к владельцу салона.",
  pending:
    "Ответ от расписания не пришёл. Запись могла быть создана — обновите день, прежде чем пробовать снова.",
  failed: "Не удалось создать запись. Попробуйте ещё раз.",
};

/** True when the outcome must preserve what the user entered (§18). */
export function outcomeKeepsDraft(outcome: SubmitOutcome): boolean {
  return outcome !== "committed";
}
