/**
 * Master Mini App «Услуги и цены» — экран 04 (DRF-1810, M18) + «Свои услуги».
 *
 * Route: /solo/services (rendered inside `UnifiedSoloSurface`).
 *
 * Источник — выбор каталога M8a/M8b через прокси бота (DRF-1895):
 * GET /api/v1/master/services/selection. Зеркало каталога бота
 * (GET /api/v1/master/catalog) экран больше не читает: цену и длительность
 * мастер задаёт здесь, запись ведёт каталог (ADR-0009 §Hard rule #4).
 *
 *   - Аккордеоны по направлению (корень дерева категорий, DRF-1912) в порядке
 *     direction_sort_order; строка без предложения — «Не настроено».
 *   - «Настроено N из M · Осталось K» — числа сервера (`configured` /
 *     `selected`); экран строки не пересчитывает.
 *   - Шторка 4.2: ровно два поля — цена и длительность — и «Убрать из моих
 *     услуг». После записи состояние берётся из ответа сервера, не дописывается.
 *   - «Продолжить» активна ⇔ выбрана хотя бы одна услуга и configured === selected
 *     (при 0 выбранных — выключена с подсказкой). Ведёт на deep_link первого
 *     missing-пункта готовности онбординга (M2); нет такого или готовность не
 *     прочиталась — /solo/setup. «Сохранить и продолжить позже» → /solo/setup:
 *     одна точка «позже» для всех шагов онбординга, как M25.
 *   - Отказы загрузки — каждый своим текстом; ни один не молчит и ни один не
 *     оставляет редактирование доступным.
 *
 * «Свои услуги» (DRF-1896, M18a; G6 / D6 владельца): услуга, которой нет в
 * каноне, — не строка каталога, а ЗАЯВКА о разрыве канона к владельцу
 * (`/api/v1/master/canon-gap-requests`, DRF-1802 → каталог DRF-1801).
 *   - Секция «Свои услуги · N»: N и статусы — только из ответа сервера.
 *   - Заявки не входят ни в направления, ни в счёт «Настроено» (фриз §11.2).
 *   - Форма «Добавить мою» сначала спрашивает «похожую услугу» у каталога;
 *     подсказка ничего не связывает. «Выбрать эту услугу» (DRF-1895) активна ⇔
 *     выбор канона загрузился для этого мастера.
 *   - Решение по заявке принимает владелец — экран его только показывает.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../lib/api";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import {
  createCanonGapRequest,
  getOnboardingReadiness,
  getServiceSelection,
  getSimilarCanonTemplates,
  listCanonGapRequests,
  putServiceOffer,
  removeService,
  selectServices,
  type CanonGapRequest,
  type CanonGapSimilar,
  type SelectedService,
  type ServiceSelectionState,
} from "../lib/master-api";

// --- Russian copy ------------------------------------------------------------

const COPY = {
  title: "Услуги и цены",
  pricesTitle: "Цены и длительность",
  notConfigured: "Не настроено",
  noServices: "Вы ещё не выбрали услуги из каталога.",
  noDirection: "Другое",
  continue: "Продолжить",
  selectAtLeastOne: "Выбери хотя бы одну услугу",
  later: "Сохранить и продолжить позже",
  loadError: "Не удалось загрузить услуги.",
  retryLoad: "Повторить",
  salonManaged: "Услуги салона ведёт владелец салона.",
  notLinkedTitle: "Доступ не настроен",
  notLinkedText: "Профиль ещё не привязан — привязку выполнит оператор.",
  support: "Написать в поддержку",
  retry: "Попробовать снова",
  durationUnit: "мин",
  priceUnit: "₽",
};

const SHEET = {
  duration: "Длительность",
  other: "Другое время",
  minutes: "Минут",
  save: "Сохранить",
  remove: "Убрать из моих услуг",
  close: "Закрыть",
  errPrice: "Цена — от 1 ₽, не больше двух знаков после запятой.",
  errDuration: "Длительность — от 5 до 480 минут.",
  serviceRemoved: "Услуга убрана — выберите её в каталоге снова.",
  serviceNotSelected: "Эта услуга больше не выбрана.",
  saveFailed: "Не получилось сохранить.",
  removeFailed: "Не получилось убрать услугу.",
};

const futureAppointmentsMessage = (count: number | null) =>
  count === null
    ? "Нельзя убрать: есть будущие записи."
    : `Нельзя убрать: есть будущие записи (${count}).`;

export const DURATION_PRESETS: readonly number[] = [15, 30, 45, 60, 75, 90, 120];
const OTHER_MINUTES_MIN = 5;
const OTHER_MINUTES_MAX = 480;

/** Одна точка «позже» для шагов онбординга (как M25). */
const SETUP_PATH = "/solo/setup";
/** Пункт готовности этого же экрана: вести на него из «Продолжить» — петля. */
const SELF_READINESS_KEY = "services";

// --- «Свои услуги» copy (DRF-1896) ----------------------------------------

export const OWN_TITLE = "Свои услуги";
export const OWN_NOTE =
  "Услуги, которых нет в каталоге. Их проверяет владелец; клиенты увидят услугу только после подтверждения.";
export const OWN_EMPTY = "Своих услуг пока нет.";
export const ADD_OWN_LABEL = "Добавить мою";
export const ADD_ANYWAY_LABEL = "Всё равно добавить мою";
export const PICK_CANON_LABEL = "Выбрать эту услугу";
// DRF-1895: кнопка активна ⇔ выбор канона доступен этому мастеру (состояние
// выбора загрузилось с сервера). Салон, чей каталог ведёт владелец, выбрать не
// может — экран говорит это словами; любой другой отказ — кнопка выключена.
export const PICK_CANON_UNAVAILABLE = "Выбор услуги из каталога сейчас недоступен.";
export const SALON_MANAGED_MESSAGE =
  "Каталог салона ведёт владелец — выбрать услугу из каталога здесь нельзя.";
/** Счётчик — из ответа сервера, экран его не считает. */
export const pickedMessage = (selected: number) =>
  `Добавили в ваши услуги. Выбрано услуг: ${selected}.`;
export const SIMILAR_TITLE = "В каталоге есть похожая услуга:";
export const SENT_MESSAGE = "Отправили на проверку.";
export const NOT_LINKED_MESSAGE =
  "Профиль ещё не связан с каталогом — заявку пока некуда отправить.";
export const OWN_LOAD_ERROR = "Не получилось загрузить свои услуги";
export const FIELD_NAME = "Название";
export const FIELD_DESCRIPTION = "Описание";
export const FIELD_DURATION = "Длительность, мин";
export const FIELD_PRICE = "Цена, ₽";
export const ERR_NAME = "Укажите название.";
export const ERR_DURATION = "Длительность — целое число минут, не меньше 1.";
export const ERR_PRICE = "Укажите цену — число, не меньше 0.";

// --- helpers (pure) ------------------------------------------------------------

/** Имя отказа: `details.reason` прокси, иначе слаг ошибки. */
function refusalReason(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const reason = e.details?.reason;
  return typeof reason === "string" ? reason : e.slug;
}

/** «1500.00» → «1500», «1500.50» → «1500,50». */
export function formatPrice(price: string): string {
  const n = Number(price);
  if (!Number.isFinite(n)) return price;
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(".", ",");
}

/** Цена мастера: от 1 ₽, не больше двух знаков. Нормализованная строка или null. */
export function normalizeOfferPrice(raw: string): string | null {
  const value = raw.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return null;
  return Number(value) >= 1 ? value : null;
}

interface DirectionGroup {
  key: string;
  name: string;
  order: number;
  services: SelectedService[];
}

/** Группы по направлению в порядке direction_sort_order; строки — в порядке сервера. */
function groupByDirection(services: SelectedService[]): DirectionGroup[] {
  const groups = new Map<string, DirectionGroup>();
  for (const service of services) {
    const key = service.direction_id ?? "";
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        name: service.direction_id ? service.direction_name ?? COPY.noDirection : COPY.noDirection,
        order: service.direction_id
          ? service.direction_sort_order ?? Number.MAX_SAFE_INTEGER
          : Number.MAX_SAFE_INTEGER,
        services: [],
      };
      groups.set(key, group);
    }
    group.services.push(service);
  }
  return Array.from(groups.values()).sort(
    (a, b) => a.order - b.order || a.name.localeCompare(b.name, "ru"),
  );
}

// --- Экран 04: направление-аккордеон ------------------------------------------

function DirectionSection({
  group,
  onOpen,
}: {
  group: DirectionGroup;
  onOpen: (service: SelectedService) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <section className="master-services__direction" aria-label={group.name}>
      <h3 className="master-services__direction-title">
        <button
          type="button"
          className="master-services__direction-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {group.name}
        </button>
      </h3>
      {open &&
        group.services.map((service) => (
          <button
            key={service.salon_service_id}
            type="button"
            className="service-card master-services__row"
            onClick={() => onOpen(service)}
          >
            <span className="service-card__name">{service.name}</span>
            <span
              className={
                service.configured && service.offer
                  ? "service-card__meta master-services__row-done"
                  : "service-card__meta master-services__row-pending"
              }
            >
              {service.configured && service.offer
                ? `${formatPrice(service.offer.price)} ${COPY.priceUnit} · ${service.offer.duration_minutes} ${COPY.durationUnit} ✓`
                : COPY.notConfigured}
            </span>
          </button>
        ))}
    </section>
  );
}

// --- Экран 04: шторка 4.2 ------------------------------------------------------

type DurationChoice = number | "other" | null;

function OfferSheet({
  service,
  onClose,
  onState,
}: {
  service: SelectedService;
  onClose: () => void;
  onState: (state: ServiceSelectionState) => void;
}) {
  const initialMinutes = service.offer?.duration_minutes ?? null;
  const initialIsPreset = initialMinutes !== null && DURATION_PRESETS.includes(initialMinutes);
  const [price, setPrice] = useState(service.offer ? formatPrice(service.offer.price) : "");
  const [choice, setChoice] = useState<DurationChoice>(
    initialMinutes === null ? null : initialIsPreset ? initialMinutes : "other",
  );
  const [otherMinutes, setOtherMinutes] = useState(
    initialMinutes !== null && !initialIsPreset ? String(initialMinutes) : "",
  );
  const [errors, setErrors] = useState<{ price?: string; duration?: string }>({});
  const [refusal, setRefusal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const minutes = (): number | null => {
    if (choice === null) return null;
    if (choice !== "other") return choice;
    const value = otherMinutes.trim();
    if (!/^\d+$/.test(value)) return null;
    const n = Number(value);
    return n >= OTHER_MINUTES_MIN && n <= OTHER_MINUTES_MAX ? n : null;
  };

  const save = async () => {
    const normalizedPrice = normalizeOfferPrice(price);
    const duration = minutes();
    setErrors({
      price: normalizedPrice === null ? SHEET.errPrice : undefined,
      duration: duration === null ? SHEET.errDuration : undefined,
    });
    setRefusal(null);
    if (normalizedPrice === null || duration === null) return;
    setBusy(true);
    try {
      const state = await putServiceOffer(service.salon_service_id, {
        price: normalizedPrice,
        duration_minutes: duration,
      });
      onState(state);
      onClose();
    } catch (e: unknown) {
      setBusy(false);
      const reason = refusalReason(e);
      setRefusal(
        reason === "service_removed"
          ? SHEET.serviceRemoved
          : reason === "service_not_selected"
            ? SHEET.serviceNotSelected
            : reason === "salon_catalog_owner_managed"
              ? COPY.salonManaged
              : SHEET.saveFailed,
      );
    }
  };

  const remove = async () => {
    setRefusal(null);
    setBusy(true);
    try {
      const state = await removeService(service.salon_service_id);
      onState(state);
      onClose();
    } catch (e: unknown) {
      setBusy(false);
      const reason = refusalReason(e);
      if (reason === "has_future_appointments") {
        const count = e instanceof ApiError ? e.details?.count : undefined;
        setRefusal(futureAppointmentsMessage(typeof count === "number" ? count : null));
        return;
      }
      setRefusal(
        reason === "service_removed"
          ? SHEET.serviceRemoved
          : reason === "service_not_selected"
            ? SHEET.serviceNotSelected
            : SHEET.removeFailed,
      );
    }
  };

  return (
    <div className="master-services__sheet" role="dialog" aria-modal="true" aria-label={service.name}>
      <h2 className="master-services__sheet-title">{service.name}</h2>
      <form
        className="master-services__offer-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label>
          {FIELD_PRICE}
          <input
            type="text"
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        {errors.price && <p className="master-services__field-error">{errors.price}</p>}

        <div className="master-services__chips" role="radiogroup" aria-label={SHEET.duration}>
          {DURATION_PRESETS.map((preset) => (
            <label key={preset} className="master-services__chip">
              <input
                type="radio"
                name="offer-duration"
                checked={choice === preset}
                onChange={() => setChoice(preset)}
              />
              {`${preset} ${COPY.durationUnit}`}
            </label>
          ))}
          <label className="master-services__chip">
            <input
              type="radio"
              name="offer-duration"
              checked={choice === "other"}
              onChange={() => setChoice("other")}
            />
            {SHEET.other}
          </label>
        </div>
        {choice === "other" && (
          <label>
            {SHEET.minutes}
            <input
              type="text"
              inputMode="numeric"
              value={otherMinutes}
              onChange={(e) => setOtherMinutes(e.target.value)}
            />
          </label>
        )}
        {errors.duration && <p className="master-services__field-error">{errors.duration}</p>}
        {refusal && (
          <p className="master-services__field-error" role="alert">
            {refusal}
          </p>
        )}

        <button type="submit" className="btn-primary" disabled={busy}>
          {SHEET.save}
        </button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => void remove()}>
          {SHEET.remove}
        </button>
        <button type="button" className="btn-secondary" onClick={onClose}>
          {SHEET.close}
        </button>
      </form>
    </div>
  );
}

// --- «Свои услуги»: validation (pure, exported for tests) --------------------

export interface OwnServiceDraft {
  name: string;
  description: string;
  duration: string;
  price: string;
}

export type OwnServiceErrors = Partial<Record<"name" | "duration" | "price", string>>;

export function validateOwnService(draft: OwnServiceDraft): OwnServiceErrors {
  const errors: OwnServiceErrors = {};
  if (!draft.name.trim()) errors.name = ERR_NAME;
  const duration = Number(draft.duration);
  if (!/^\d+$/.test(draft.duration.trim()) || !Number.isInteger(duration) || duration < 1) {
    errors.duration = ERR_DURATION;
  }
  const price = Number(draft.price.replace(",", "."));
  if (!draft.price.trim() || !Number.isFinite(price) || price < 0) errors.price = ERR_PRICE;
  return errors;
}

function OwnRequestCard({ item }: { item: CanonGapRequest }) {
  return (
    <div className="service-card own-service-card" data-testid="own-service">
      <div className="service-card__name">{item.name}</div>
      <div className="service-card__meta">
        {`${item.duration_minutes} ${COPY.durationUnit} · ${item.price} ${COPY.priceUnit}`}
      </div>
      {/* Статус — словом сервера, не вычисляется на экране. */}
      <div className="own-service-card__status">{item.status_label}</div>
      {item.clarification_question && (
        <p className="own-service-card__note">{item.clarification_question}</p>
      )}
      {item.rejection_reason && (
        <p className="own-service-card__note">{item.rejection_reason}</p>
      )}
    </div>
  );
}

const EMPTY_DRAFT: OwnServiceDraft = { name: "", description: "", duration: "", price: "" };

/** Доступность выбора канона — из загрузки выбора, которую делает экран (одна на экран). */
type PickAvailability = "loading" | "available" | "salon_managed" | "unavailable";

function OwnServicesSection({
  availability,
  onSelected,
}: {
  availability: PickAvailability;
  onSelected: (state: ServiceSelectionState) => void;
}) {
  const [items, setItems] = useState<CanonGapRequest[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notLinked, setNotLinked] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<OwnServiceDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<OwnServiceErrors>({});
  const [similar, setSimilar] = useState<CanonGapSimilar[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Отказ «каталог ведёт владелец» на самом выборе сильнее загруженного состояния.
  const [salonRefused, setSalonRefused] = useState(false);
  const selection: PickAvailability = salonRefused ? "salon_managed" : availability;

  const load = useCallback(() => {
    setLoadError(null);
    return listCanonGapRequests()
      .then((res) => {
        setItems(res.requests);
        setNotLinked(false);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.slug === "not_linked") {
          setNotLinked(true);
          setItems([]);
          return;
        }
        setLoadError(e instanceof ApiError ? e.detail || e.slug : "Сеть недоступна");
      });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refusal = (e: unknown) => {
    if (e instanceof ApiError && e.slug === "not_linked") {
      setNotLinked(true);
      return;
    }
    setSubmitError(e instanceof ApiError ? e.detail || e.slug : "Сеть недоступна");
  };

  const create = async () => {
    setBusy(true);
    setSubmitError(null);
    try {
      await createCanonGapRequest({
        name: draft.name.trim(),
        description: draft.description.trim(),
        duration_minutes: Number(draft.duration),
        price: draft.price.trim().replace(",", "."),
      });
      setFormOpen(false);
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
      setMessage(SENT_MESSAGE);
      // Список и счётчик — заново с сервера, а не дописанной локально строкой.
      await load();
    } catch (e: unknown) {
      refusal(e);
    } finally {
      setBusy(false);
    }
  };

  const pick = async (templateId: string) => {
    setBusy(true);
    setSubmitError(null);
    try {
      const res = await selectServices([templateId]);
      setFormOpen(false);
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
      setMessage(pickedMessage(res.selected));
      // Выбранная услуга появляется в «Цены и длительность» — из ответа сервера.
      onSelected(res);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.slug === "salon_catalog_owner_managed") {
        setSalonRefused(true);
        return;
      }
      refusal(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    const found = validateOwnService(draft);
    setErrors(found);
    setMessage(null);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const res = await getSimilarCanonTemplates(draft.name.trim());
      if (res.similar.length > 0) {
        // Подсказка — и только: связь не создаётся, заявка не отправлена.
        setSimilar(res.similar);
        setBusy(false);
        return;
      }
    } catch (e: unknown) {
      if (e instanceof ApiError && e.slug === "not_linked") {
        setNotLinked(true);
        setBusy(false);
        return;
      }
      // Подсказка недоступна — это не повод не принять заявку.
    }
    setBusy(false);
    await create();
  };

  const field = (key: keyof OwnServiceDraft) => ({
    value: draft[key],
    onChange: (e: { target: { value: string } }) =>
      setDraft((d) => ({ ...d, [key]: e.target.value })),
  });

  return (
    <section className="master-services__section master-services__own" aria-label={OWN_TITLE}>
      <h2 className="master-services__section-title">
        {items === null ? OWN_TITLE : `${OWN_TITLE} · ${items.length}`}
      </h2>
      <p className="master-services__own-note">{OWN_NOTE}</p>
      {notLinked && (
        <p className="callout" role="status">
          {NOT_LINKED_MESSAGE}
        </p>
      )}
      {loadError && (
        <div className="callout callout--danger" role="alert">
          <p>{OWN_LOAD_ERROR}</p>
          <p style={{ fontSize: "var(--font-size-100)", opacity: 0.7 }}>{loadError}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            {COPY.retry}
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {items !== null && items.length === 0 && !notLinked && <p>{OWN_EMPTY}</p>}
      {items?.map((item) => <OwnRequestCard key={item.id} item={item} />)}

      {!notLinked && !formOpen && (
        <button type="button" className="btn-secondary" onClick={() => setFormOpen(true)}>
          {ADD_OWN_LABEL}
        </button>
      )}

      {!notLinked && formOpen && (
        <form
          className="master-services__own-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label>
            {FIELD_NAME}
            <input type="text" {...field("name")} />
          </label>
          {errors.name && <p className="master-services__field-error">{errors.name}</p>}
          <label>
            {FIELD_DESCRIPTION}
            <textarea {...field("description")} />
          </label>
          <label>
            {FIELD_DURATION}
            <input type="text" inputMode="numeric" {...field("duration")} />
          </label>
          {errors.duration && <p className="master-services__field-error">{errors.duration}</p>}
          <label>
            {FIELD_PRICE}
            <input type="text" inputMode="decimal" {...field("price")} />
          </label>
          {errors.price && <p className="master-services__field-error">{errors.price}</p>}
          {submitError && (
            <p className="master-services__field-error" role="alert">
              {submitError}
            </p>
          )}

          {similar && similar.length > 0 ? (
            <div className="master-services__similar">
              <p>{SIMILAR_TITLE}</p>
              {similar.map((s) => (
                <div key={s.template_id} className="master-services__similar-item">
                  <span>{s.name}</span>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy || selection !== "available"}
                    title={selection === "available" ? undefined : PICK_CANON_UNAVAILABLE}
                    onClick={() => void pick(s.template_id)}
                  >
                    {PICK_CANON_LABEL}
                  </button>
                </div>
              ))}
              {selection === "salon_managed" ? (
                <p className="master-services__similar-later">{SALON_MANAGED_MESSAGE}</p>
              ) : selection === "unavailable" ? (
                <p className="master-services__similar-later">{PICK_CANON_UNAVAILABLE}</p>
              ) : null}
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void create()}>
                {ADD_ANYWAY_LABEL}
              </button>
            </div>
          ) : (
            <button type="submit" className="btn-primary" disabled={busy}>
              {ADD_OWN_LABEL}
            </button>
          )}
        </form>
      )}
    </section>
  );
}

// --- Main screen ---------------------------------------------------------

type SelectionLoad =
  | { kind: "loading" }
  | { kind: "ready"; state: ServiceSelectionState }
  | { kind: "salon_managed" }
  | { kind: "not_linked" }
  | { kind: "error" };

export function MasterServicesScreen() {
  const navigate = useNavigate();
  const [load, setLoad] = useState<SelectionLoad>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoad({ kind: "loading" });
    getServiceSelection()
      .then((state) => {
        if (alive) setLoad({ kind: "ready", state });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const reason = refusalReason(e);
        setLoad(
          reason === "salon_catalog_owner_managed"
            ? { kind: "salon_managed" }
            : reason === "not_linked"
              ? { kind: "not_linked" }
              : { kind: "error" },
        );
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const applyState = useCallback((state: ServiceSelectionState) => {
    setLoad({ kind: "ready", state });
  }, []);

  const state = load.kind === "ready" ? load.state : null;
  const groups = useMemo(() => (state ? groupByDirection(state.services) : []), [state]);
  const openService = state?.services.find((s) => s.salon_service_id === openId) ?? null;

  const proceed = async () => {
    setLeaving(true);
    let target = SETUP_PATH;
    try {
      const readiness = await getOnboardingReadiness();
      const next = readiness.items.find(
        (item) =>
          item.state === "missing" &&
          item.key !== SELF_READINESS_KEY &&
          item.deep_link.startsWith("/"),
      );
      if (next) target = next.deep_link;
    } catch {
      // Готовность не прочиталась — не гадаем, ведём в одну точку «позже».
    }
    navigate(target);
  };

  if (load.kind === "not_linked") {
    // Привязку выполняет оператор: кнопки регистрации нет, есть связь с поддержкой.
    return (
      <div className="screen master-services">
        <header className="master-services__header">
          <h1>{COPY.title}</h1>
        </header>
        <div className="callout" role="status">
          <h2 className="master-services__section-title">{COPY.notLinkedTitle}</h2>
          <p>{COPY.notLinkedText}</p>
          <a
            href={SUPPORT_DEEPLINK}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-secondary"
          >
            {COPY.support}
          </a>
        </div>
      </div>
    );
  }

  const availability: PickAvailability =
    load.kind === "ready"
      ? "available"
      : load.kind === "loading"
        ? "loading"
        : load.kind === "salon_managed"
          ? "salon_managed"
          : "unavailable";

  return (
    <div className="screen master-services">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
      </header>

      {load.kind === "loading" && (
        <div className="master-services__body" aria-busy="true">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton service-card service-card--skel" />
          ))}
        </div>
      )}
      {load.kind === "salon_managed" && (
        <p className="callout" role="status">
          {COPY.salonManaged}
        </p>
      )}
      {load.kind === "error" && (
        <div className="callout callout--danger" role="alert">
          <p>{COPY.loadError}</p>
          <button type="button" className="btn-secondary" onClick={() => setReloadKey((k) => k + 1)}>
            {COPY.retryLoad}
          </button>
        </div>
      )}

      {state && (
        <section className="master-services__section" aria-label={COPY.pricesTitle}>
          <h2 className="master-services__section-title">{COPY.pricesTitle}</h2>
          <div className="master-services__progress">
            <p>{`Настроено ${state.configured} из ${state.selected}`}</p>
            <p>{`Осталось ${Math.max(state.selected - state.configured, 0)}`}</p>
          </div>
          {state.services.length === 0 ? (
            <p className="master-services__empty">{COPY.noServices}</p>
          ) : (
            groups.map((group) => (
              <DirectionSection
                key={group.key}
                group={group}
                onOpen={(service) => setOpenId(service.salon_service_id)}
              />
            ))
          )}
        </section>
      )}

      {/* Заявки о разрыве канона — отдельно от направлений и счёта «Настроено». */}
      <OwnServicesSection availability={availability} onSelected={applyState} />

      {state && (
        <div className="master-services__actions">
          <button
            type="button"
            className="btn-primary"
            disabled={leaving || state.selected === 0 || state.configured !== state.selected}
            onClick={() => void proceed()}
          >
            {COPY.continue}
          </button>
          {state.selected === 0 && (
            <p className="master-services__actions-hint">{COPY.selectAtLeastOne}</p>
          )}
          <button type="button" className="btn-secondary" onClick={() => navigate(SETUP_PATH)}>
            {COPY.later}
          </button>
        </div>
      )}

      {openService && (
        <OfferSheet
          key={openService.salon_service_id}
          service={openService}
          onClose={() => setOpenId(null)}
          onState={applyState}
        />
      )}
    </div>
  );
}
