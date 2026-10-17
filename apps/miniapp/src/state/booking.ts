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
/**
 * DRF-2752 — услуга ЭТОГО пути записи: её называет экран или адрес, и она
 * главнее того, что осталось в черновике.
 *
 * Адрес (`?service=`) и черновик — не два независимых источника истины.
 * До этого листа они могли молча расходиться: услуга ехала по адресу, а
 * экран времени читал черновик — пустой (человека выбрасывало в каталог)
 * или оставшийся от прошлого выбора (окна и подтверждение шли по чужой
 * услуге).
 *
 * - Та же услуга — черновик не трогается; имя дописывается, если стало
 *   известно, и **никогда не затирается пустым**: подтверждение показывает
 *   его человеку.
 * - Другая услуга — это другой путь. Всё, что относилось к прежнему, к нему
 *   не относится: мастер, время, источник входа и перенос чужой записи.
 */
export const alignService = (id: string, name = "") => {
  if (state.serviceId === id) {
    if (name && state.serviceName !== name) {
      state = { ...state, serviceName: name };
      emit();
    }
    return;
  }
  state = { ...EMPTY, serviceId: id, serviceName: name };
  emit();
};
/**
 * DRF-2752 — мастер ЭТОГО пути: тот, чьё время человек выбирает. Тот же
 * мастер — имя дописывается и не затирается пустым. Другой — время,
 * выбранное у прежнего, больше не выбрано.
 */
export const alignMaster = (id: string, name = "") => {
  if (state.masterId === id) {
    if (name && state.masterName !== name) {
      state = { ...state, masterName: name };
      emit();
    }
    return;
  }
  state = { ...state, masterId: id, masterName: name, visitAt: null };
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
