/** Light booking-flow draft store. */

import { useSyncExternalStore } from "react";

export interface BookingDraft {
  serviceId: string | null;
  serviceName: string | null;
  masterId: string | null;
  masterName: string | null;
  visitAt: string | null;
}

const EMPTY: BookingDraft = {
  serviceId: null,
  serviceName: null,
  masterId: null,
  masterName: null,
  visitAt: null,
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
