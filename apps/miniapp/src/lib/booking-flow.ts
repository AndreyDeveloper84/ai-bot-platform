/**
 * Один поток записи C05 по макету DRF-1320 — словарь и сборка шагов.
 *
 * Этап 1 из 4 (DRF-2178): кадры 1–2 — способ исполнения и выбор
 * специалиста. Время (кадр 3), «Проверь запись» (4) и снятие чатового
 * пошагового выбора — следующие этапы; радиус ограничен намеренно.
 *
 * Откуда данные
 * -------------
 * Всё — из резолвера и зеркала, ничего не сочиняется экраном:
 *
 * * **услуга шага** — первая SERVICE-рекомендация (`picks[0]`), тот же
 *   источник, что у полки каталога. Таблицы «направление → услуга» в
 *   каталоге нет и мы её не заводим: по ADR-0009 это транзакционный
 *   словарь Ayla, и завести его молча значило бы перейти границу
 *   репозиториев. Вопрос «нужен ли курируемый маппинг» у владельца;
 * * **специалист** — `providerPicks`, кандидаты вида PROVIDER. Лист
 *   DRF-1775 объявлял их причины заблокированными D11; это верно для
 *   12.09, но транзит `reasonCodes`/`reasons` сделан в DRF-2174, и
 *   гейт «без displayable-причины кандидат сюда не доходит» встроен
 *   в него же;
 * * **длительность и цена** — из услуги зеркала. Нет значения — нет
 *   строки: «0 ₽» и «— мин» это не «неизвестно», а неправда.
 *
 * §60 остаётся в силе: OD-PILOT-9 снят только в части C04, и «Ayla
 * рекомендует услугу X» на этих кадрах не звучит — сторожа в тестах
 * обоих экранов.
 */
import type { Master, Service } from "./api";
import { formatDuration, priceFromLabel } from "./format";
import type { CatalogBrowseData } from "./customer-booking";

/** Кадр 1 — дословно с макета. */
export const OPTION_HEAD = "Подходящий вариант для этого шага";
export const OPTION_WHY_HEAD = "Почему этот вариант";
export const OPTION_CTA = "Выбрать специалиста";
export const OPTION_OTHER = "Другой вариант";

/** Кадр 2 — дословно с макета. */
export const PROVIDER_HEAD = "Лучше всего подходит";
export const PROVIDER_WHY_HEAD = "Почему она:";
export const PROVIDER_OTHERS_HEAD = "Другие варианты";
export const PROVIDER_OTHER_LINK = "Другой специалист";
/** «Выбрать Екатерину» — макет называет выбранного по имени. */
export const PROVIDER_CTA = (name: string): string => `Выбрать ${firstName(name)}`;

/** Адреса шагов потока. */
export const OPTION_ROUTE = "/customer/booking/option";
export const PROVIDER_ROUTE = "/customer/booking/provider";
/** Полный список мастеров — прежний экран, он же запасной путь. */
export const ALL_PROVIDERS_ROUTE = "/customer/book/master";
export const CATALOG_ROUTE = "/customer/catalog";

/**
 * Сколько ДРУГИХ специалистов показываем. Столько же, сколько других
 * подходов на C04.2: список выбора, а не каталог (C05.2 — «не каталог»).
 */
export const MAX_OTHER_PROVIDERS = 2;

/** Сколько причин под именем. Тот же предел, что у карточки и полки. */
export const MAX_REASONS = 3;

/**
 * Имя в винительном падеже макет пишет как «Выбрать Екатерину». Склонять
 * русские имена кодом мы не умеем и не будем: ошибка склонения читается
 * хуже, чем именительный падеж. Берём первое слово имени как есть —
 * названное отступление от макета.
 */
function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? name;
}

export interface ExecutionOption {
  readonly service: Service;
  /** Проверяемые причины (ПРАВКА 1: абстрактных пунктов нет). */
  readonly reasons: readonly string[];
}

export interface ProviderOption {
  readonly master: Master;
  readonly reasons: readonly string[];
}

export interface ProviderChoice {
  readonly best: ProviderOption;
  readonly others: readonly ProviderOption[];
}

/**
 * Способ исполнения для этого шага — или ``null``.
 *
 * ``null`` значит «показывать нечего»: подбор не дал услуг, которые
 * умеет объяснить. Тогда кадра нет вовсе, и человек идёт в каталог —
 * прежний честный путь, а не пустой экран с заголовком.
 */
export function executionOption(browse: CatalogBrowseData): ExecutionOption | null {
  for (const pick of browse.picks) {
    const service = browse.services.find((s) => s.id === pick.serviceId);
    if (service && pick.reasons.length > 0) {
      return { service, reasons: pick.reasons.slice(0, MAX_REASONS) };
    }
  }
  return null;
}

/**
 * Лучший специалист и другие варианты — или ``null``.
 *
 * Порядок — порядок решения (`rank`), а не наша сортировка. ``null`` —
 * кандидатов нет, и кадра нет: остаётся прежний выбор мастера.
 */
export function providerChoice(browse: CatalogBrowseData): ProviderChoice | null {
  const options: ProviderOption[] = [];
  for (const pick of [...(browse.providerPicks ?? [])].sort((a, b) => a.rank - b.rank)) {
    const master = browse.masters.find((m) => m.id === pick.masterId);
    if (master && pick.reasons.length > 0) {
      options.push({ master, reasons: pick.reasons.slice(0, MAX_REASONS) });
    }
  }
  const [best, ...rest] = options;
  if (!best) return null;
  return { best, others: rest.slice(0, MAX_OTHER_PROVIDERS) };
}

/**
 * «60 мин · 3 200 ₽» — только то, что есть в данных.
 *
 * Считает НЕ этот модуль: цена и длительность форматируются теми же
 * `priceFromLabel` и `formatDuration`, что и карточка каталога. Свой
 * формат здесь был бы вторым на ту же работу — и однажды разошёлся бы
 * с витриной, показав «90 минут» там, где весь остальной продукт
 * говорит «1 ч 30 мин».
 *
 * Названное отступление от макета: он пишет «60 минут» словом, витрина
 * везде сокращает. Второй формат ради одного слова не завожу.
 *
 * Пустая строка значит «сказать нечего»: ни «0 ₽», ни прочерка. Цена —
 * прайс зеркала («от»), а не снимок: снимок цены фиксируется на шаге
 * «Проверь запись» (DRF-1708/2172), и до создания записи его нет.
 */
export function serviceMeta(service: Service): string {
  const parts: string[] = [];
  const duration = formatDuration(service.duration_min);
  if (duration) parts.push(duration);
  const price = priceFromLabel(service.price_from);
  if (price) parts.push(price);
  return parts.join(" · ");
}

/** «4.8 · 74 отзыва» — или пусто. Нет данных — нет скобок (DRF-1778). */
export function providerMeta(master: Master): string {
  const parts: string[] = [];
  if (master.rating) parts.push(master.rating);
  const count = master.review_count ?? 0;
  if (count > 0) parts.push(`${count} ${reviewWord(count)}`);
  return parts.join(" · ");
}

function reviewWord(count: number): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "отзывов";
  switch (count % 10) {
    case 1:
      return "отзыв";
    case 2:
    case 3:
    case 4:
      return "отзыва";
    default:
      return "отзывов";
  }
}
