/**
 * Сквозные отказы подписки салона → экран, где их снимают (DRF-2988, B3).
 *
 * * `403 subscription_frozen` — при заморозке бот закрывает всю админскую
 *   поверхность, кроме биллинга. Владелец открывает «Сегодня» и получил бы
 *   ошибку без выхода; вместо этого — экран биллинга, где можно заплатить.
 * * `409 plan_change_required` — соло пытается получить второго мастера (В-2):
 *   не запрет, а предложение перейти на «Салон» с подтверждением цены.
 *
 * Отказ приходит в любую дверь (`admin-api.ts`), а уводит с экрана тот, у
 * кого есть роутер: клиент шлёт событие окна, `SalonBillingRedirect` внутри
 * админских маршрутов его слушает.
 */

export const SALON_BILLING_PATH = "/admin/billing";
export const SALON_PLAN_CHANGE_PATH = "/admin/billing/salon";

/** Цена «Салона», если отказ гейта её не назвал. Канон — каталог (PRICE_MISMATCH). */
export const SALON_PRICE_FALLBACK = "990.00";

const EVENT = "ayla:salon-billing";

export type SalonBillingSignal =
  | { kind: "frozen" }
  | { kind: "plan_change"; salonPrice: string };

/** Перевести отказ двери в сигнал, или `null`, если отказ не про подписку. */
export function billingSignalFor(
  status: number,
  slug: string,
  details?: Record<string, unknown>,
): SalonBillingSignal | null {
  if (status === 403 && slug === "subscription_frozen") return { kind: "frozen" };
  if (status === 409 && slug === "plan_change_required") {
    const price = details?.salon_price;
    return {
      kind: "plan_change",
      salonPrice: typeof price === "string" && price ? price : SALON_PRICE_FALLBACK,
    };
  }
  return null;
}

export function emitSalonBillingSignal(signal: SalonBillingSignal): void {
  window.dispatchEvent(new CustomEvent<SalonBillingSignal>(EVENT, { detail: signal }));
}

export function onSalonBillingSignal(handler: (signal: SalonBillingSignal) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<SalonBillingSignal>).detail);
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
