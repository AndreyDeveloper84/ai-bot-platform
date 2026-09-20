/**
 * «Новая запись» — manual booking for the salon front desk.
 *
 * Built to `ayla-knowledge/07 UX/Ayla Master Schedule UX Contract.md`
 * §12–18. The rules live in `lib/booking-draft.ts`; the form itself is
 * `components/booking/NewBookingForm.tsx` — shared with the master's own
 * «Новая запись» since DRF-2155 (М-3). This file is the salon shell: the
 * `admin-api` adapter and the return target. What it renders is pinned
 * byte for byte by `AdminNewBookingScreen.snapshot.test.tsx`, taken
 * before the form was extracted.
 *
 * ### Deviation from the mock, stated rather than hidden
 *
 * The contract's mock has three rows — Клиент / Услуга / Дата и время —
 * because it was drawn for a master's own app, where the assignment is
 * implicit: the master is the master. The salon front desk books *for*
 * someone, so this surface adds a Мастер row (`subject: salon`). That is
 * a deviation from the drawing and a faithful reading of the logic: §12
 * names «duration/assignment context» as what makes an availability
 * query meaningful, and on this surface the assignment has to be chosen.
 */

import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import {
  createSalonBooking,
  getBookingSlots,
  getCatalogServicesForAdmin,
  listMasters,
  searchSalonCustomers,
} from "../../lib/admin-api";
import {
  NewBookingForm,
  type BookingFormApi,
  type ReturnTarget,
} from "../../components/booking/NewBookingForm";

/**
 * Куда возвращает этот экран — и как называется место возврата.
 *
 * Экран создания записи открывают ДВЕ поверхности. Мост зовёт его без
 * параметров, и возврат ведёт на «День салона» — как вёл всегда.
 * Пилотная админка (DRF-1236) зовёт с `?return=today`, потому что иначе
 * её главное действие оказалось бы дверью в один конец: с пятивкладочного
 * моста назад в пилот не ведёт ни одна кнопка.
 *
 * Список закрытый, а не «взять адрес из параметра»: подставляемый адрес
 * возврата — это чужая ссылка, решающая, куда уйдёт человек.
 */
const RETURN_TARGETS: Readonly<Record<string, ReturnTarget>> = {
  today: {
    path: "/admin/today",
    back: "← Сегодня",
    open: "Открыть «Сегодня»",
  },
};

/** Возврат по умолчанию — тот, что был до появления пилота. */
const RETURN_DEFAULT: ReturnTarget = {
  path: "/admin/day",
  back: "← День салона",
  open: "Открыть день салона",
};

/** Salon backend: the master is chosen in the form and travels in the body. */
const SALON_API: BookingFormApi = {
  listServices: () => getCatalogServicesForAdmin(),
  listMasters: async () =>
    (await listMasters({ is_active: true, limit: 50 })).items,
  searchCustomers: (q, opts) => searchSalonCustomers(q, opts),
  getSlots: async (params, opts) => {
    const res = await getBookingSlots(
      {
        masterId: params.masterId ?? "",
        serviceId: params.serviceId,
        date: params.date,
      },
      opts,
    );
    return { slots: res.slots, timezone: res.timezone };
  },
  createBooking: (body) =>
    createSalonBooking({
      master_id: body.master_id ?? "",
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

export function AdminNewBookingScreen() {
  const [searchParams] = useSearchParams();
  const returnTo = useMemo(
    () => RETURN_TARGETS[searchParams.get("return") ?? ""] ?? RETURN_DEFAULT,
    [searchParams],
  );
  return (
    <NewBookingForm
      subject={{ kind: "salon" }}
      api={SALON_API}
      returnTo={returnTo}
    />
  );
}
