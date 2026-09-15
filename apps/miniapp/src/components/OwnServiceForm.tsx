/**
 * «Добавить мою» — форма своей услуги (DRF-1896, M18a), вынесена из экрана 04
 * для экрана 03 (DRF-1809, M17) без изменения поведения.
 *
 * Своя услуга — не строка каталога, а ЗАЯВКА о разрыве канона к владельцу
 * (`/api/v1/master/canon-gap-requests`). Сначала форма спрашивает у каталога
 * «похожую услугу»: это подсказка, связи она не создаёт. «Выбрать эту услугу»
 * (DRF-1895) активна ⇔ выбор канона доступен этому мастеру — это знает экран,
 * который загрузил выбор, и передаёт как `availability`.
 *
 * Список заявок, счётчик «Свои услуги · N» и сообщения после отправки живут у
 * экрана: форма сообщает о результате через колбэки и сама ничего не
 * дописывает к списку.
 */

import { useState } from "react";

import { ApiError } from "../lib/api";
import {
  createCanonGapRequest,
  getSimilarCanonTemplates,
  selectServices,
  type CanonGapSimilar,
  type ServiceSelectionState,
} from "../lib/master-api";

export const OWN_TITLE = "Свои услуги";
export const OWN_NOTE =
  "Услуги, которых нет в каталоге. Их проверяет владелец; клиенты увидят услугу только после подтверждения.";
export const OWN_EMPTY = "Своих услуг пока нет.";
export const ADD_OWN_LABEL = "Добавить мою";
export const ADD_ANYWAY_LABEL = "Всё равно добавить мою";
export const PICK_CANON_LABEL = "Выбрать эту услугу";
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
export const FIELD_NAME = "Название";
export const FIELD_DESCRIPTION = "Описание";
export const FIELD_DURATION = "Длительность, мин";
export const FIELD_PRICE = "Цена, ₽";
export const ERR_NAME = "Укажите название.";
export const ERR_DURATION = "Длительность — целое число минут, не меньше 1.";
export const ERR_PRICE = "Укажите цену — число, не меньше 0.";

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

/** Доступность выбора канона — из загрузки выбора, которую делает экран (одна на экран). */
export type PickAvailability = "loading" | "available" | "salon_managed" | "unavailable";

const EMPTY_DRAFT: OwnServiceDraft = { name: "", description: "", duration: "", price: "" };

export function OwnServiceForm({
  availability,
  onCreated,
  onSelected,
  onNotLinked,
  onSubmitStart,
}: {
  availability: PickAvailability;
  /** Заявка принята сервером — экран перечитывает список и показывает сообщение. */
  onCreated: () => void | Promise<void>;
  /** Похожая услуга выбрана — состояние выбора из ответа сервера. */
  onSelected: (state: ServiceSelectionState) => void;
  /** Профиль не связан — экран прячет форму и объясняет. */
  onNotLinked: () => void;
  /** Новая отправка началась — экран снимает прежнее сообщение (как до выноса формы). */
  onSubmitStart?: () => void;
}) {
  const [draft, setDraft] = useState<OwnServiceDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<OwnServiceErrors>({});
  const [similar, setSimilar] = useState<CanonGapSimilar[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Отказ «каталог ведёт владелец» на самом выборе сильнее загруженного состояния.
  const [salonRefused, setSalonRefused] = useState(false);
  const selection: PickAvailability = salonRefused ? "salon_managed" : availability;

  const refusal = (e: unknown) => {
    if (e instanceof ApiError && e.slug === "not_linked") {
      onNotLinked();
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
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
      await onCreated();
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
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
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
    onSubmitStart?.();
    const found = validateOwnService(draft);
    setErrors(found);
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
        onNotLinked();
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
  );
}
