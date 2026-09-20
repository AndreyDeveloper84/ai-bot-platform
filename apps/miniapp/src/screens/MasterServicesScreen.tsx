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

import {
  ADD_OWN_LABEL,
  FIELD_PRICE,
  NOT_LINKED_MESSAGE,
  OWN_EMPTY,
  OWN_NOTE,
  OWN_TITLE,
  OwnServiceForm,
  SENT_MESSAGE,
  pickedMessage,
  type PickAvailability,
} from "../components/OwnServiceForm";
import { SystemState } from "../components/master/SystemState";
import { ApiError } from "../lib/api";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import {
  getOnboardingReadiness,
  getServiceSelection,
  listCanonGapRequests,
  putServiceOffer,
  removeService,
  type CanonGapRequest,
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
  // DRF-1809 (M17): единственный выход из нулевого выбора — экран 03.
  chooseFromCatalog: "Выбрать из каталога",
  // DRF-1808 (M16): направления можно изменить в любое время (P13) — экран 02.
  directions: "Направления",
  salonManaged: "Услуги салона ведёт владелец салона.",
  notLinkedTitle: "Доступ не настроен",
  notLinkedText: "Профиль ещё не привязан — привязку выполнит оператор.",
  support: "Написать в поддержку",
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
/** Экран 03 — выбор услуг из каталога (DRF-1809, M17). */
export const SELECT_PATH = "/solo/services/select";
/** Экран 02 — направления (DRF-1808, M16): «изменить в любое время». */
export const DIRECTIONS_PATH = "/solo/directions";

// --- «Свои услуги» copy (DRF-1896) ----------------------------------------
// Форма «Добавить мою» и её слова живут в components/OwnServiceForm (DRF-1809:
// её переиспользует экран 03); здесь — переэкспорт для тех, кто импортирует
// их из экрана.

export {
  ADD_ANYWAY_LABEL,
  ADD_OWN_LABEL,
  ERR_DURATION,
  ERR_NAME,
  ERR_PRICE,
  FIELD_DESCRIPTION,
  FIELD_DURATION,
  FIELD_NAME,
  FIELD_PRICE,
  NOT_LINKED_MESSAGE,
  OWN_EMPTY,
  OWN_NOTE,
  OWN_TITLE,
  PICK_CANON_LABEL,
  PICK_CANON_UNAVAILABLE,
  SALON_MANAGED_MESSAGE,
  SENT_MESSAGE,
  SIMILAR_TITLE,
  pickedMessage,
  validateOwnService,
  type OwnServiceDraft,
  type OwnServiceErrors,
} from "../components/OwnServiceForm";

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

// --- «Свои услуги» ------------------------------------------------------------

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

function OwnServicesSection({
  availability,
  onSelected,
}: {
  availability: PickAvailability;
  onSelected: (state: ServiceSelectionState) => void;
}) {
  const [items, setItems] = useState<CanonGapRequest[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [notLinked, setNotLinked] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

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
        setLoadError(e);
      });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
      {loadError !== null && loadError !== undefined && (
        <SystemState
          kind="load_error"
          what="ownServices"
          err={loadError}
          onRetry={() => void load()}
        />
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
        <OwnServiceForm
          availability={availability}
          onCreated={async () => {
            setFormOpen(false);
            setMessage(SENT_MESSAGE);
            // Список и счётчик — заново с сервера, а не дописанной локально строкой.
            await load();
          }}
          onSelected={(res) => {
            setFormOpen(false);
            setMessage(pickedMessage(res.selected));
            // Выбранная услуга появляется в «Цены и длительность» — из ответа сервера.
            onSelected(res);
          }}
          onNotLinked={() => setNotLinked(true)}
          onSubmitStart={() => setMessage(null)}
        />
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
  | { kind: "error"; err: unknown };

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
              : { kind: "error", err: e },
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

      {load.kind === "loading" && <SystemState kind="loading" />}
      {load.kind === "salon_managed" && (
        <p className="callout" role="status">
          {COPY.salonManaged}
        </p>
      )}
      {load.kind === "error" && (
        <SystemState
          kind="load_error"
          what="services"
          err={load.err}
          onRetry={() => setReloadKey((k) => k + 1)}
        />
      )}

      {state && (
        <p className="master-services__directions-entry">
          {/* DRF-1808 (P13): вход на экран 02 из настроек услуг — направления
              не хранятся, экран 02 выведет их из выбранных услуг. */}
          <button type="button" className="btn-secondary" onClick={() => navigate(DIRECTIONS_PATH)}>
            {COPY.directions}
          </button>
        </p>
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
            <>
              <p className="master-services__actions-hint">{COPY.selectAtLeastOne}</p>
              {/* DRF-1809: выход из нулевого выбора — только в ready: салон и
                  непривязанный профиль сюда не доходят, каталог им откажет. */}
              <button type="button" className="btn-primary" onClick={() => navigate(SELECT_PATH)}>
                {COPY.chooseFromCatalog}
              </button>
            </>
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
