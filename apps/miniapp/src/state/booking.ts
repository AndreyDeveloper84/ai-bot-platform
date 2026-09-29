/** Light booking-flow draft store. */

import { useSyncExternalStore } from "react";

export interface BookingDraft {
  serviceId: string | null;
  serviceName: string | null;
  masterId: string | null;
  masterName: string | null;
  visitAt: string | null;
  /** Set when flow is a reschedule of an existing booking. */
  rescheduleOf: string | null;
  /**
   * Provenance of the draft — where the booking flow started
   * (DRF-1484 / §24.5). Stamped by the screen that originates the
   * draft (`"catalog"` / `"master"`); consumed by
   * `resolveEntryPoint` when the pending intent is snapshotted.
   */
  entryPoint: string | null;
}

const EMPTY: BookingDraft = {
  serviceId: null,
  serviceName: null,
  masterId: null,
  masterName: null,
  visitAt: null,
  rescheduleOf: null,
  entryPoint: null,
};

let state: BookingDraft = { ...EMPTY };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const getBookingDraft = (): BookingDraft => state;
export const setService = (id: string, name: string) => {
  state = { ...state, serviceId: id, serviceName: name };
  emit();
};
export const setMaster = (id: string, name: string) => {
  state = { ...state, masterId: id, masterName: name };
  emit();
};
export const setVisitAt = (visitAt: string | null) => {
  state = { ...state, visitAt };
  emit();
};
export const setEntryPoint = (entryPoint: string | null) => {
  state = { ...state, entryPoint };
  emit();
};
export const setRescheduleContext = (
  rescheduleOf: string,
  serviceId: string,
  serviceName: string,
  masterId: string,
  masterName: string,
) => {
  state = { ...EMPTY, rescheduleOf, serviceId, serviceName, masterId, masterName };
  emit();
};
export const resetBooking = () => {
  state = { ...EMPTY };
  emit();
};

export function useBookingDraft(): BookingDraft {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getBookingDraft,
    getBookingDraft,
  );
}
