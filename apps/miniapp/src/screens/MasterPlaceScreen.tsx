/**
 * Экран 05 онбординга мастера — «Место работы» (DRF-1811, M19; макет 5).
 *
 * Route: /solo/place. Вход — пункт готовности «location» (экран 01) и
 * «Место работы» в настройках. Всё живёт в каталоге (M11): бот-прокси
 * `GET/POST/PATCH /service-locations` отдаёт readback, экран ничего не
 * хранит и ничего не дорисовывает.
 *
 * Кадры макета:
 *  - 5.1 — формат: чекбоксы «Свой кабинет» / «Салон или студия» / «Выезд».
 *    Кабинет и салон — одно место (в каталоге у мастера одно место, второе —
 *    409 `place_already_set`); «Выезд» — отдельная строка-зона. Два формата =
 *    две записи. «Изменить тип» возвращает сюда;
 *  - 5.2 / 5.5 — адрес руками; подсказки — только пока `POST /geocoding/suggest`
 *    отвечает 200 (на стенде геокодер не настроен → 503 → поле как есть, без
 *    ошибки на экране); название студии — свободный текст (P56: не поиск по
 *    салонам и не присоединение); «Как клиенту вас найти?» ≤ 200 со счётчиком;
 *  - карта — ТОЛЬКО при координатах из ответа каталога: ссылка «Открыть на
 *    карте» + координаты текстом, без iframe (CSP Mini App внутри MAX) и без
 *    фото фасада (источника нет — исправление макета §3): на экране нет ни
 *    одного `<img>`;
 *  - 5.3 — выезд: город из ответа (`city`), радио «По всему городу» /
 *    «Настрою позже». «Только в некоторых районах» отсутствует по построению —
 *    у каталога нет источника районов;
 *  - 5.4 — сводка с бейджами из `status`: CONFIRMED → «Будет виден клиентам»,
 *    REVIEW_REQUIRED → «На проверке», зона whole_city → «По всему городу»,
 *    later → «Настроить позже». Бейдж «Будет виден клиентам» — только со слов
 *    каталога (`shown_to_clients_after_publication`), не по догадке экрана.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../lib/api";
import {
  createServiceLocation,
  getServiceLocations,
  patchServiceLocation,
  suggestAddress,
  type AddressSuggestion,
  type AreaCoverage,
  type PlaceKind,
  type ServiceArea,
  type ServiceLocationsState,
  type ServicePlace,
} from "../lib/master-api";

export const PLACE_ROUTE = "/solo/place";
export const NOTE_MAX = 200;

export const PLACE_COPY = {
  title: "Место работы",
  formatsTitle: "Где вы принимаете?",
  formatsHint: "Можно отметить несколько форматов. Изменить их можно в любое время.",
  kindPrivate: "Свой кабинет",
  kindSalon: "Салон или студия",
  kindMobile: "Выезд к клиенту",
  next: "Продолжить",
  nextDisabled: "Отметьте хотя бы один формат",
  changeKind: "Изменить тип",
  addressTitle: "Где вас найти",
  labelField: "Название (необязательно)",
  labelHint: "Как вы называете своё место. Это не присоединение к салону.",
  addressField: "Адрес",
  addressHint: "Город, улица, дом. Подсказки появятся, если сервис адресов доступен.",
  noteField: "Как клиенту вас найти?",
  noteHint: "Вход, этаж, ориентир — до 200 символов.",
  noteCounter: (n: number) => `${n} / ${NOTE_MAX}`,
  saveAddress: "Сохранить адрес",
  addressRequired: "Укажите адрес.",
  noteTooLong: "Не больше 200 символов.",
  areaTitle: "Куда выезжаете?",
  areaCity: (city: string) => `Город: ${city}`,
  areaWholeCity: "По всему городу",
  areaLater: "Настрою позже",
  areaLaterHint: "Выезд не мешает завершить настройку — клиенты пока увидят только место.",
  saveArea: "Сохранить зону",
  summaryTitle: "Ваши места",
  badgeVisible: "Будет виден клиентам",
  badgeReview: "На проверке",
  badgeInactive: "Не активно",
  badgeWholeCity: "По всему городу",
  badgeLater: "Настроить позже",
  openMap: "Открыть на карте",
  coords: (lat: string, lon: string) => `${lat}, ${lon}`,
  noCoords: "Координаты появятся после проверки адреса.",
  edit: "Изменить",
  done: "Готово",
  loadError: "Не удалось загрузить место работы.",
  saveError: "Не получилось сохранить.",
  retry: "Повторить",
  notLinked: "Профиль ещё не связан с каталогом — сохранить место пока некуда.",
  salonManaged: "Место работы мастера салона ведёт владелец салона.",
  refusal: {
    place_already_set: "Место уже указано — измените его, а не добавляйте второе.",
    area_already_set: "Зона выезда уже указана — измените её.",
    no_workspace_tenant: "У профиля ещё нет рабочего пространства — привязку выполнит оператор.",
    place_outside_workspace: "Это место не из вашего рабочего пространства.",
    validation_error: "Проверьте введённое.",
  } as Record<string, string>,
} as const;

/** Ссылка на карту по координатам — обычная ссылка, не встраивание (без ключей и без iframe). */
export function mapLink(lat: string, lon: string): string {
  return `https://www.openstreetmap.org/?mlat=${encodeURIComponent(lat)}&mlon=${encodeURIComponent(lon)}#map=17/${encodeURIComponent(lat)}/${encodeURIComponent(lon)}`;
}

type Format = PlaceKind | "mobile";
type Frame = "formats" | "address" | "area" | "summary";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; state: ServiceLocationsState }
  | { kind: "salon_managed" }
  | { kind: "not_linked" }
  | { kind: "error" };

function refusalSlug(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  return e.slug ?? null;
}

/** Форматы, выведенные из readback: у места — его kind, у зоны — mobile. */
export function formatsFromState(state: ServiceLocationsState): Set<Format> {
  const out = new Set<Format>();
  for (const p of state.places) if (p.kind === "private_studio" || p.kind === "salon_or_studio") out.add(p.kind);
  for (const a of state.areas) if (a.kind === "mobile") out.add("mobile");
  return out;
}

/** Бейдж места — только со слов каталога. */
export function placeBadge(place: ServicePlace): string {
  if (place.shown_to_clients_after_publication) return PLACE_COPY.badgeVisible;
  if (place.status === "inactive") return PLACE_COPY.badgeInactive;
  return PLACE_COPY.badgeReview;
}

export function areaBadge(area: ServiceArea): string {
  return area.coverage === "whole_city" ? PLACE_COPY.badgeWholeCity : PLACE_COPY.badgeLater;
}

/** Подсказки: 503/409 прокси — это «подсказок нет», не ошибка экрана. */
async function fetchSuggestions(q: string): Promise<AddressSuggestion[]> {
  try {
    const res = await suggestAddress(q);
    return res.available ? res.suggestions : [];
  } catch (e) {
    if (e instanceof ApiError) return [];
    throw e;
  }
}

export function MasterPlaceScreen() {
  const navigate = useNavigate();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [frame, setFrame] = useState<Frame>("formats");
  const [formats, setFormats] = useState<Set<Format>>(new Set());
  const [placeKind, setPlaceKind] = useState<PlaceKind>("private_studio");
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [note, setNote] = useState("");
  const [coverage, setCoverage] = useState<AreaCoverage | null>(null);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const suggestSeq = useRef(0);

  useEffect(() => {
    let alive = true;
    setLoad({ kind: "loading" });
    getServiceLocations()
      .then((state) => {
        if (!alive) return;
        setLoad({ kind: "ready", state });
        const derived = formatsFromState(state);
        setFormats(derived);
        const place = state.places[0];
        if (place) {
          setPlaceKind(place.kind === "salon_or_studio" ? "salon_or_studio" : "private_studio");
          setLabel(place.label ?? "");
          setAddress(place.address ?? "");
          setNote(place.note_for_client ?? "");
        }
        const area = state.areas[0];
        if (area) setCoverage(area.coverage);
        // Есть что показать — сразу сводка (P50 «Изменить тип» ведёт к форматам).
        setFrame(state.places.length > 0 || state.areas.length > 0 ? "summary" : "formats");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const slug = refusalSlug(e);
        setLoad(
          slug === "salon_place_owner_managed"
            ? { kind: "salon_managed" }
            : slug === "not_linked"
              ? { kind: "not_linked" }
              : { kind: "error" },
        );
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const applyState = useCallback((state: ServiceLocationsState) => {
    setLoad({ kind: "ready", state });
  }, []);

  const toggleFormat = (f: Format) =>
    setFormats((prev) => {
      const next = new Set(prev);
      if (next.has(f)) next.delete(f);
      else next.add(f);
      if (f !== "mobile" && next.has(f)) setPlaceKind(f);
      return next;
    });

  const wantsPlace = formats.has("private_studio") || formats.has("salon_or_studio");
  const wantsArea = formats.has("mobile");

  // Подсказки — по мере ввода; ответ старше последнего ввода отбрасывается.
  const onAddressChange = (value: string) => {
    setAddress(value);
    const seq = ++suggestSeq.current;
    const q = value.trim();
    if (q.length < 3) {
      setSuggestions([]);
      return;
    }
    void fetchSuggestions(q).then((items) => {
      if (suggestSeq.current === seq) setSuggestions(items);
    });
  };

  const existingPlace = load.kind === "ready" ? load.state.places[0] ?? null : null;
  const existingArea = load.kind === "ready" ? load.state.areas[0] ?? null : null;

  const savePlace = async () => {
    setFormError(null);
    if (!address.trim()) {
      setFormError(PLACE_COPY.addressRequired);
      return;
    }
    if (note.length > NOTE_MAX) {
      setFormError(PLACE_COPY.noteTooLong);
      return;
    }
    setBusy(true);
    try {
      const fields = {
        kind: placeKind,
        address: address.trim(),
        label: label.trim(),
        note_for_client: note.trim(),
      };
      const state = existingPlace
        ? await patchServiceLocation(existingPlace.id, fields)
        : await createServiceLocation(fields);
      applyState(state);
      setSuggestions([]);
      setFrame(wantsArea ? "area" : "summary");
    } catch (e) {
      const slug = refusalSlug(e);
      setFormError((slug && PLACE_COPY.refusal[slug]) || PLACE_COPY.saveError);
    } finally {
      setBusy(false);
    }
  };

  const saveArea = async () => {
    setFormError(null);
    if (!coverage) return;
    setBusy(true);
    try {
      const state = existingArea
        ? await patchServiceLocation(existingArea.id, { coverage })
        : await createServiceLocation({ kind: "mobile", coverage });
      applyState(state);
      setFrame("summary");
    } catch (e) {
      const slug = refusalSlug(e);
      setFormError((slug && PLACE_COPY.refusal[slug]) || PLACE_COPY.saveError);
    } finally {
      setBusy(false);
    }
  };

  const goFromFormats = () => {
    setFormError(null);
    if (wantsPlace) setFrame("address");
    else if (wantsArea) setFrame("area");
  };

  const header = (
    <header className="master-services__header">
      <h1>{PLACE_COPY.title}</h1>
    </header>
  );

  if (load.kind !== "ready") {
    return (
      <div className="screen master-services">
        {header}
        {load.kind === "loading" && (
          <div className="master-services__section" aria-busy="true">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton service-card service-card--skel" />
            ))}
          </div>
        )}
        {load.kind === "salon_managed" && (
          <p className="callout" role="status">
            {PLACE_COPY.salonManaged}
          </p>
        )}
        {load.kind === "not_linked" && (
          <p className="callout" role="status">
            {PLACE_COPY.notLinked}
          </p>
        )}
        {load.kind === "error" && (
          <div className="callout callout--danger" role="alert">
            <p>{PLACE_COPY.loadError}</p>
            <button type="button" className="btn-secondary" onClick={() => setReloadKey((k) => k + 1)}>
              {PLACE_COPY.retry}
            </button>
          </div>
        )}
      </div>
    );
  }

  const { state } = load;

  return (
    <div className="screen master-services">
      {header}

      {frame === "formats" && (
        <section className="master-services__section" aria-label={PLACE_COPY.formatsTitle}>
          <h2 className="master-services__section-title">{PLACE_COPY.formatsTitle}</h2>
          <p className="master-services__note">{PLACE_COPY.formatsHint}</p>
          <fieldset className="service-select__group">
            {(
              [
                ["private_studio", PLACE_COPY.kindPrivate],
                ["salon_or_studio", PLACE_COPY.kindSalon],
                ["mobile", PLACE_COPY.kindMobile],
              ] as Array<[Format, string]>
            ).map(([f, text]) => (
              <label key={f} className="service-card service-select__item" data-testid={`format-${f}`}>
                <input type="checkbox" checked={formats.has(f)} onChange={() => toggleFormat(f)} />
                <span className="service-card__name">{text}</span>
              </label>
            ))}
          </fieldset>
          <button
            type="button"
            className="btn-primary"
            disabled={formats.size === 0}
            title={formats.size === 0 ? PLACE_COPY.nextDisabled : undefined}
            onClick={goFromFormats}
          >
            {PLACE_COPY.next}
          </button>
        </section>
      )}

      {frame === "address" && (
        <section className="master-services__section" aria-label={PLACE_COPY.addressTitle}>
          <h2 className="master-services__section-title">{PLACE_COPY.addressTitle}</h2>
          <label className="service-select__search">
            {PLACE_COPY.labelField}
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} />
          </label>
          <p className="master-services__note">{PLACE_COPY.labelHint}</p>
          <label className="service-select__search">
            {PLACE_COPY.addressField}
            <input
              type="text"
              value={address}
              onChange={(e) => onAddressChange(e.target.value)}
              autoComplete="off"
            />
          </label>
          <p className="master-services__note">{PLACE_COPY.addressHint}</p>
          {suggestions.length > 0 && (
            <ul className="place__suggestions" role="listbox" aria-label="Подсказки адреса">
              {suggestions.map((s) => (
                <li key={s.unrestricted_value}>
                  <button
                    type="button"
                    className="btn-secondary place__suggestion"
                    onClick={() => {
                      setAddress(s.value);
                      setSuggestions([]);
                    }}
                  >
                    {s.value}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="service-select__search">
            {PLACE_COPY.noteField}
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={NOTE_MAX} rows={3} />
          </label>
          <p className="master-services__note" data-testid="note-counter">
            {PLACE_COPY.noteHint} {PLACE_COPY.noteCounter(note.length)}
          </p>
          {formError && (
            <p className="master-services__field-error" role="alert">
              {formError}
            </p>
          )}
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void savePlace()}>
            {PLACE_COPY.saveAddress}
          </button>
          <button type="button" className="btn-secondary" onClick={() => setFrame("formats")}>
            {PLACE_COPY.changeKind}
          </button>
        </section>
      )}

      {frame === "area" && (
        <section className="master-services__section" aria-label={PLACE_COPY.areaTitle}>
          <h2 className="master-services__section-title">{PLACE_COPY.areaTitle}</h2>
          <p className="master-services__note">{PLACE_COPY.areaCity(state.city)}</p>
          {/* Только два значения: «Только в некоторых районах» отсутствует по
              построению — у каталога нет источника районов (фриз §12.4). */}
          <fieldset className="service-select__group">
            {(
              [
                ["whole_city", PLACE_COPY.areaWholeCity],
                ["later", PLACE_COPY.areaLater],
              ] as Array<[AreaCoverage, string]>
            ).map(([c, text]) => (
              <label key={c} className="service-card service-select__item" data-testid={`coverage-${c}`}>
                <input type="radio" name="coverage" checked={coverage === c} onChange={() => setCoverage(c)} />
                <span className="service-card__name">{text}</span>
              </label>
            ))}
          </fieldset>
          <p className="master-services__note">{PLACE_COPY.areaLaterHint}</p>
          {formError && (
            <p className="master-services__field-error" role="alert">
              {formError}
            </p>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={busy || coverage === null}
            onClick={() => void saveArea()}
          >
            {PLACE_COPY.saveArea}
          </button>
          <button type="button" className="btn-secondary" onClick={() => setFrame("formats")}>
            {PLACE_COPY.changeKind}
          </button>
        </section>
      )}

      {frame === "summary" && (
        <section className="master-services__section" aria-label={PLACE_COPY.summaryTitle}>
          <h2 className="master-services__section-title">{PLACE_COPY.summaryTitle}</h2>
          {state.places.map((p) => (
            <div key={p.id} className="service-card place__card" data-testid={`place-${p.id}`}>
              <span className="service-card__name">
                {p.kind === "salon_or_studio" ? PLACE_COPY.kindSalon : PLACE_COPY.kindPrivate}
                {p.label ? ` · ${p.label}` : ""}
              </span>
              <span className="service-card__meta">{p.address}</span>
              {p.note_for_client && <span className="service-card__meta">{p.note_for_client}</span>}
              <span className="place__badge" data-testid={`badge-${p.id}`}>
                {placeBadge(p)}
              </span>
              {p.latitude && p.longitude ? (
                <a
                  className="place__map-link"
                  href={mapLink(p.latitude, p.longitude)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {PLACE_COPY.openMap} · {PLACE_COPY.coords(p.latitude, p.longitude)}
                </a>
              ) : (
                <span className="service-card__meta">{PLACE_COPY.noCoords}</span>
              )}
            </div>
          ))}
          {state.areas.map((a) => (
            <div key={a.id} className="service-card place__card" data-testid={`area-${a.id}`}>
              <span className="service-card__name">{PLACE_COPY.kindMobile}</span>
              <span className="service-card__meta">{a.city}</span>
              <span className="place__badge" data-testid={`badge-${a.id}`}>
                {areaBadge(a)}
              </span>
            </div>
          ))}
          <button type="button" className="btn-secondary" onClick={() => setFrame("formats")}>
            {PLACE_COPY.changeKind}
          </button>
          <button type="button" className="btn-primary" onClick={() => navigate("/solo/setup")}>
            {PLACE_COPY.done}
          </button>
        </section>
      )}
    </div>
  );
}
