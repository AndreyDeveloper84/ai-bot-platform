/**
 * F3 — Date + time picker with smart suggestions + state-dependent
 * wording + master substitution.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §5 (§5.1 layout / §5.2
 * suggestions rationale / §5.3 master substitution Q-BF-3 / §5.4
 * states).
 *
 * Voice rules (founder cut #4 — state-dependent suggestions header):
 *   - Anonymous / new customer: «Ближайшие свободные»
 *   - Registered with behavior: «Похоже подойдёт»
 *   - Loyal (5+ visits): «Твоё обычное время»
 *
 * Master substitution (Q-BF-3) — verbatim founder copy:
 *   «Анна занята на 2 недели вперёд. Если хочешь раньше, есть Карина —
 *    тоже делает маникюр гель-лак.»
 *   [Посмотреть Карину] [Дождаться Анну]
 *
 * Anti-pattern enforcement: NEVER «Карина лучше / Рекомендуем» —
 * frontend ONLY shows the founder-locked copy template above.
 *
 * WCAG 2.2 AA (Tau §11):
 *   - Slots grouped by day in `<ul>` per spec §11.3
 *   - Slot = `<button>`
 *   - ✨ suggestion marker accompanied by text «обычное время» (not
 *     color-only) — handled via aria-label.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import {
  DelayedSkeleton,
  Skeleton,
  SlotGridSkeleton,
} from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useHaptics } from "../hooks/useHaptics";
import { getCustomerSlots } from "../lib/customer-booking";
import { formatDateLabel, formatSlotTime } from "../lib/format";
import { getInitData } from "../lib/max-sdk";
import { setVisitAt, useBookingDraft } from "../state/booking";
import { backTo } from "../lib/screen-back";

interface SlotRow {
  date: string;
  start: string;
  isSuggested?: boolean;
}

type State =
  | { kind: "loading" }
  | {
      kind: "ok";
      // Server slots, ordered chronologically.
      slots: SlotRow[];
    }
  | { kind: "error"; err: unknown };

/** Tau §8 F3 state-dependent header per founder cut #4. */
type CustomerMode = "anonymous" | "registered" | "loyal";

function suggestionsHeader(mode: CustomerMode): string {
  switch (mode) {
    case "anonymous":
      return "Ближайшие свободные";
    case "loyal":
      return "Твоё обычное время";
    case "registered":
    default:
      return "Похоже подойдёт";
  }
}

/**
 * Best-effort mode detection. Anonymous detection mirrors F4
 * (CustomerBookingConfirmScreen) — empty `initData` from MAX SDK =
 * anonymous user. Until the `/me` response surfaces
 * `is_loyal_with_master` / similar, we default registered users
 * to «registered» (no «loyal» fast path yet). Hooked so backend
 * can plumb a real signal later without changing this component.
 *
 * Round-1 fix: the prior heuristic («masterName set ⇒ registered»)
 * incorrectly tagged anonymous browsers (who DO have masterName from
 * the deeplink) as «registered», surfacing «Похоже подойдёт» — wrong
 * tone for first-time visitors. Now we consult MAX initData first.
 */
function isAnonymousCustomer(): boolean {
  return getInitData() === "";
}

function detectCustomerMode(): CustomerMode {
  if (isAnonymousCustomer()) return "anonymous";
  return "registered";
}

export function CustomerSlotsScreen() {
  const navigate = useNavigate();
  const { masterId } = useParams<{ masterId: string }>();
  const haptics = useHaptics();
  const draft = useBookingDraft();
  const [state, setState] = useState<State>({ kind: "loading" });

  // Возврат (DRF-1493) — к карточке того мастера, чьи окна показаны.
  // Не `-1`: по deep link в оформление истории нет, а мастер известен
  // из адреса. Без `masterId` (адрес испорчен) — в каталог.
  const back = backTo(
    masterId ? `/customer/masters/${masterId}` : "/customer/catalog",
  );

  const load = useCallback(() => {
    if (!masterId || !draft.serviceId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    getCustomerSlots({
      masterId,
      serviceId: draft.serviceId,
      days: 14,
    })
      .then(({ slots }) => {
        if (cancelled) return;
        // Re-shape to our local row type; `is_suggested` is a future
        // backend field — absent today, defaults to false.
        const rows = slots.map((s) => ({
          date: s.date,
          start: s.start,
          isSuggested: false as boolean,
        }));
        setState({ kind: "ok", slots: rows });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [masterId, draft.serviceId]);

  useEffect(() => {
    if (!masterId || !draft.serviceId) {
      // Missing prerequisites — bounce back to catalog rather than
      // showing an empty / broken screen.
      navigate("/customer/catalog", { replace: true });
      return;
    }
    return load();
  }, [masterId, draft.serviceId, navigate, load]);

  const slotsByDate = useMemo(() => {
    const m = new Map<string, SlotRow[]>();
    if (state.kind !== "ok") return m;
    for (const s of state.slots) {
      const arr = m.get(s.date) ?? [];
      arr.push(s);
      m.set(s.date, arr);
    }
    return m;
  }, [state]);

  const suggestions = useMemo(() => {
    if (state.kind !== "ok") return [];
    // Tau §5.2: backend marks suggested via `is_suggested`; fallback
    // (no backend signal yet) is first 2 chronologically.
    const flagged = state.slots.filter((s) => s.isSuggested);
    if (flagged.length > 0) return flagged.slice(0, 2);
    return state.slots.slice(0, 2);
  }, [state]);

  function onPickSlot(slotIso: string) {
    haptics.selection();
    setVisitAt(slotIso);
  }

  function onContinue() {
    if (!draft.visitAt) return;
    navigate("/customer/booking/confirm");
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <DelayedSkeleton loading>
          <div className="date-strip">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} width="64px" height="60px" radius="var(--r-md)" />
            ))}
          </div>
          <SlotGridSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <StateError err={state.err} onRetry={load} screenId="customer-slots" />
      </ScreenLayout>
    );
  }

  // Tau §5.3 master substitution — full 14-day fully-booked case.
  if (state.slots.length === 0) {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <MasterSubstitutionCallout
          masterName={draft.masterName ?? "она"}
          onBack={() => navigate("/customer/catalog")}
        />
      </ScreenLayout>
    );
  }

  const mode = detectCustomerMode();
  const sugHeader = suggestionsHeader(mode);
  const dates = Array.from(slotsByDate.keys());

  return (
    <ScreenLayout
      back={back}
      title="Выбери время"
      cta={
        <StickyCta onClick={onContinue} disabled={!draft.visitAt}>
          {draft.visitAt ? "Дальше" : "Выбери слот"}
        </StickyCta>
      }
    >
      {suggestions.length > 0 && (
        <section aria-labelledby="slots-suggestions-title">
          <h2 id="slots-suggestions-title" className="customer-slots__section-title">
            <span aria-hidden="true">✨ </span>
            {sugHeader}
          </h2>
          <ul className="customer-slots__suggestions" role="list">
            {suggestions.map((s) => {
              const active = draft.visitAt === s.start;
              return (
                <li key={s.start}>
                  <button
                    type="button"
                    className={`customer-slots__suggestion${active ? " customer-slots__suggestion--active" : ""}`}
                    aria-pressed={active}
                    aria-label={`${formatDateLabel(s.date)} в ${formatSlotTime(s.start)}${s.isSuggested ? ", обычное время" : ""}`}
                    onClick={() => onPickSlot(s.start)}
                  >
                    <div className="customer-slots__suggestion-day">
                      {formatDateLabel(s.date)}
                    </div>
                    <div className="customer-slots__suggestion-time">
                      {formatSlotTime(s.start)}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section aria-labelledby="slots-all-title">
        <h2 id="slots-all-title" className="customer-slots__section-title">
          Все слоты
        </h2>
        {dates.map((d) => {
          const daySlots = slotsByDate.get(d) ?? [];
          if (daySlots.length === 0) return null;
          return (
            <div key={d} className="customer-slots__day">
              <h3 className="customer-slots__day-title">
                {formatDateLabel(d)}
              </h3>
              <ul className="customer-slots__day-list" role="list">
                {daySlots.map((s) => {
                  const active = draft.visitAt === s.start;
                  return (
                    <li key={s.start}>
                      <button
                        type="button"
                        className={`customer-slots__cell${active ? " customer-slots__cell--active" : ""}`}
                        aria-pressed={active}
                        aria-label={`${formatDateLabel(d)} в ${formatSlotTime(s.start)}`}
                        onClick={() => onPickSlot(s.start)}
                      >
                        {formatSlotTime(s.start)}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </section>
    </ScreenLayout>
  );
}

/**
 * Master substitution callout (Q-BF-3 verbatim founder copy).
 *
 * **Critical**: copy is locked verbatim per Tau §5.3 + founder cut.
 * Anti-pattern: NEVER «Карина лучше / Рекомендуем». Frontend renders
 * this template; substitute candidates will be plumbed by backend.
 *
 * NOTE: We don't have a substitution candidate from the current
 * backend; this is a generic «нет слотов» state. When backend ships
 * `substitution_candidates`, swap the placeholder for real data and
 * keep the wording template.
 */
function MasterSubstitutionCallout({
  masterName,
  onBack,
}: {
  masterName: string;
  onBack: () => void;
}) {
  return (
    <div className="callout" role="status">
      <p style={{ margin: 0 }}>
        {masterName} занята на 2 недели вперёд.
      </p>
      <p style={{ margin: "var(--s-2) 0 0 0", color: "var(--c-text-secondary)" }}>
        Если хочешь раньше — посмотри других мастеров в каталоге.
      </p>
      <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)", flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn-secondary"
          onClick={onBack}
        >
          Посмотреть других
        </button>
      </div>
    </div>
  );
}
