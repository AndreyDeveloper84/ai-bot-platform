/**
 * «Новая запись» мастера — макет DRF-1184 (DRF-2155, М-3).
 *
 * `/master/booking/new` и `/solo/booking/new`. Форма — общая с салонной
 * стойкой (`components/booking/NewBookingForm`) под `subject: master`:
 * мастер — субъект initData, строки «Мастер» нет; услуги — свои
 * (`getMasterCatalog`, только активные); клиент, слоты и создание — ручки
 * М-2 (`master-api`). Из «Расписания» тап по свободному окну приводит с
 * `?date&from&to` — форма покажет «Выбранное окно: 14:00–17:00».
 *
 * Тексты состояний — словарь `SystemState` (М-6); своих здесь нет.
 */

import { useMemo } from "react";
import { useLocation } from "react-router-dom";

import {
  NewBookingForm,
  type BookingFormApi,
  type ReturnTarget,
} from "../components/booking/NewBookingForm";
import {
  createMasterBooking,
  getMasterBookingSlots,
  getMasterCatalog,
  searchMasterCustomers,
} from "../lib/master-api";

/** Master backend: no master_id anywhere — the subject is initData. */
const MASTER_API: BookingFormApi = {
  listServices: async () =>
    (await getMasterCatalog())
      .filter((s) => s.is_active)
      .map((s) => ({
        id: s.service_id,
        name: s.name,
        duration_min: s.duration_min || null,
      })),
  listMasters: async () => [],
  searchCustomers: (q, opts) => searchMasterCustomers(q, opts),
  getSlots: async (params, opts) => {
    const res = await getMasterBookingSlots(
      { serviceId: params.serviceId, date: params.date },
      opts,
    );
    return { slots: res.slots, timezone: res.timezone };
  },
  createBooking: (body) =>
    createMasterBooking({
      service_id: body.service_id,
      start_at: body.start_at,
      idempotency_key: body.idempotency_key,
      ...(body.client_id !== undefined ? { client_id: body.client_id } : {}),
      ...(body.client_name !== undefined
        ? { client_name: body.client_name }
        : {}),
      ...(body.client_phone !== undefined
        ? { client_phone: body.client_phone }
        : {}),
    }),
};

export function MasterNewBookingScreen() {
  const location = useLocation();
  const isSolo = location.pathname.startsWith("/solo/");
  const base = isSolo ? "/solo" : "/master";
  const returnTo = useMemo<ReturnTarget>(
    () => ({
      path: `${base}/schedule`,
      back: "← Расписание",
      open: "Открыть расписание",
    }),
    [base],
  );
  return (
    <NewBookingForm
      subject={{ kind: "master", isSolo }}
      api={MASTER_API}
      returnTo={returnTo}
      bookingHref={(id) => `${base}/bookings/${encodeURIComponent(id)}`}
    />
  );
}
