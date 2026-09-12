/**
 * «Рядом со мной» — одноразовая геолокация и формат расстояния (DRF-1707).
 *
 * Решение владельца (пакет 2, D3): «Показать рядом со мной» — явное
 * contextual consent на одноразовую геолокацию, если координаты не
 * сохраняются; перед вызовом ОС должно быть понятно, зачем location
 * нужна. Поэтому:
 *
 *   - координаты живут только в памяти экрана на время запроса; ни
 *     sessionStorage, ни localStorage, ни DeviceStorage их не видят;
 *   - пояснение стоит НА кнопке/рядом с ней, до вызова ОС;
 *   - расстояние считает каталог (до подтверждённого места оказания
 *     услуги, §9) — экран его только показывает; `null` = неизвестно,
 *     не «0 м».
 *
 * Формат — OD-PILOT-9 distance contract: `< 1000` → метры, `>= 1000` →
 * километры с одним знаком («1.8 км»).
 */
export const NEARBY_BUTTON = "Показать рядом со мной";
export const NEARBY_EXPLANATION =
  "Чтобы показать мастеров рядом, приложение один раз узнает твоё местоположение. " +
  "Координаты никуда не сохраняются.";
export const NEARBY_DENIED =
  "Не удалось определить местоположение — показываю список без расстояний.";
export const NEARBY_LOCATING = "Определяю местоположение…";

export interface Coords {
  lat: number;
  lon: number;
}

/** Метры → подпись по контракту; для `null`/некорректного — пусто (не рисуется). */
export function formatDistance(meters: number | null | undefined): string {
  if (meters == null || !Number.isFinite(meters) || meters < 0) return "";
  if (meters < 1000) return `${Math.round(meters)} м`;
  return `${(meters / 1000).toFixed(1)} км`;
}

/** Есть ли в списке хоть одно известное расстояние — только тогда список
 *  зовётся «Рядом с вами» (#1653: имя обещает сортировку по близости). */
export function hasKnownDistance(masters: ReadonlyArray<{ distance_meters?: number | null }>): boolean {
  return masters.some((m) => typeof m.distance_meters === "number");
}

/**
 * Одноразовый запрос координат у ОС. Ничего не кэширует (`maximumAge: 0`),
 * ничего не пишет. Отказ/отсутствие API/таймаут — `null`, без деталей:
 * человеку достаточно «не удалось».
 */
export function locateOnce(timeoutMs = 10_000): Promise<Coords | null> {
  const geo = typeof navigator !== "undefined" ? navigator.geolocation : undefined;
  if (!geo) return Promise.resolve(null);
  return new Promise((resolve) => {
    geo.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => resolve(null),
      { enableHighAccuracy: false, timeout: timeoutMs, maximumAge: 0 },
    );
  });
}
