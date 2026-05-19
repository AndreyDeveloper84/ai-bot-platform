/** F3 — Date strip + slot grid. Spec §8 F3. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchSlots, type FreeSlot } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { DelayedSkeleton, Skeleton, SlotGridSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import { formatDateLabel, formatDayStrip, formatSlotTime } from "../lib/format";
import { setVisitAt, useBookingDraft } from "../state/booking";

function isoDateNDaysAhead(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; slots: FreeSlot[] }
  | { kind: "error"; err: unknown };

export function BookingWhenScreen() {
  const navigate = useNavigate();
  const draft = useBookingDraft();
  const haptics = useHaptics();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  useBackButton({ onBack: () => navigate(-1) });

  const load = useCallback(() => {
    if (!draft.serviceId || !draft.masterId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    fetchSlots({
      serviceId: draft.serviceId,
      masterId: draft.masterId,
      dateFrom: isoDateNDaysAhead(0),
      dateTo: isoDateNDaysAhead(14),
    })
      .then(({ slots }) => {
        if (cancelled) return;
        setState({ kind: "ok", slots });
        if (slots.length > 0 && slots[0]) setSelectedDate(slots[0].date);
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [draft.serviceId, draft.masterId]);

  useEffect(() => {
    if (!draft.serviceId || !draft.masterId) {
      navigate("/catalog", { replace: true });
      return;
    }
    return load();
  }, [draft.serviceId, draft.masterId, navigate, load]);

  const slotsByDate = useMemo(() => {
    if (state.kind !== "ok") return new Map<string, FreeSlot[]>();
    const m = new Map<string, FreeSlot[]>();
    for (const s of state.slots) {
      const arr = m.get(s.date) ?? [];
      arr.push(s);
      m.set(s.date, arr);
    }
    return m;
  }, [state]);

  const availableDates = useMemo(() => Array.from(slotsByDate.keys()), [slotsByDate]);
  const slotsForDay = selectedDate ? slotsByDate.get(selectedDate) ?? [] : [];

  function onPickSlot(s: FreeSlot) {
    haptics.selection();
    setVisitAt(s.start);
  }

  function onContinue() {
    if (!draft.visitAt) return;
    navigate("/book/confirm");
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Выберите время">
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
      <ScreenLayout title="Выберите время">
        <StateError err={state.err} onRetry={load} screenId="slots" />
      </ScreenLayout>
    );
  }

  if (state.slots.length === 0) {
    return (
      <ScreenLayout title="Выберите время">
        <div className="callout">
          <p style={{ margin: 0 }}>На ближайшие две недели свободного времени нет.</p>
        </div>
      </ScreenLayout>
    );
  }

  const emptyDayCopy = selectedDate
    ? `На ${formatDateLabel(selectedDate)} свободного времени нет.`
    : "В этот день нет свободного времени.";

  return (
    <ScreenLayout
      title="Выберите время"
      cta={
        <StickyCta onClick={onContinue} disabled={!draft.visitAt}>
          {draft.visitAt ? "Дальше" : "Выберите слот"}
        </StickyCta>
      }
    >
      <div className="date-strip" role="tablist" aria-label="Дата визита">
        {availableDates.map((d) => {
          const ds = formatDayStrip(d);
          const active = selectedDate === d;
          return (
            <button
              type="button"
              key={d}
              role="tab"
              aria-selected={active}
              className={`date-strip__day ${active ? "date-strip__day--active" : ""}`}
              onClick={() => {
                haptics.selection();
                setSelectedDate(d);
              }}
            >
              <div className="date-strip__day-name">{ds.name}</div>
              <div className="date-strip__day-num">{ds.num}</div>
            </button>
          );
        })}
      </div>

      <div className="slot-grid" role="radiogroup" aria-label="Свободные слоты">
        {slotsForDay.length === 0 ? (
          <div className="slot-grid__empty">{emptyDayCopy}</div>
        ) : (
          slotsForDay.map((s) => {
            const active = draft.visitAt === s.start;
            return (
              <button
                type="button"
                key={s.start}
                role="radio"
                aria-checked={active}
                className={`slot-grid__cell ${active ? "slot-grid__cell--active" : ""}`}
                onClick={() => onPickSlot(s)}
              >
                {formatSlotTime(s.start)}
              </button>
            );
          })
        )}
      </div>
    </ScreenLayout>
  );
}
