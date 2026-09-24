/**
 * Экран 02 онбординга мастера — «Чем вы занимаетесь?» (DRF-1808, M16).
 *
 * Route: /solo/directions. Вход — пункт готовности «services» при нуле
 * выбранных (экран 01) и кнопка «Направления» на экране 04 (/solo/services,
 * «изменить в любое время», P13); выход — экран 03 (/solo/services/select),
 * которому выбранные направления передаются навигацией.
 *
 * Что здесь НЕ хранится — и почему:
 *   - направления мастера — производное от выбранных услуг: у каждой услуги
 *     сервера есть `direction_id`, и «мастер занимается маникюром» значит
 *     «у него есть активная услуга направления „маникюр“». Отдельной записи
 *     нет ни на сервере, ни в браузере; выбор с этого экрана живёт ровно до
 *     экрана 03 — в `location.state`;
 *   - карточки — корни каталога из `GET /services/directions`: ни их числа,
 *     ни названий экран не знает (макет показывает шесть, каталог отдаёт свои
 *     корни; G7 — открытый пункт владельца, экран его не решает);
 *   - «Выбрано: N» — длина множества отмеченного в состоянии, не константа и
 *     не поле ответа.
 *
 * «Другое направление» — не новая категория (фриз §8 «do not silently create
 * taxonomy nodes»): это контролируемое уточнение, которое ведёт в ту же
 * форму своей услуги, что и на экране 03, — заявку о разрыве канона (M9) с
 * названным направлением в описании. Направление появится у мастера, когда
 * владелец подтвердит услугу и она станет выбранной.
 */

import { useEffect, useMemo, useState } from "react";
import { StudioCallout, notConnectedText } from "../components/StudioCallout";
import { useNavigate } from "react-router-dom";

import {
  NOT_LINKED_MESSAGE,
  OwnServiceForm,
  SENT_MESSAGE,
  pickedMessage,
} from "../components/OwnServiceForm";
import { SystemState } from "../components/master/SystemState";
import { ApiError } from "../lib/api";
import {
  getServiceDirections,
  getServiceSelection,
  type ServiceDirection,
  type ServiceSelectionState,
} from "../lib/master-api";

export const DIRECTIONS_COPY = {
  title: "Чем вы занимаетесь?",
  hint: "Отметьте одно или несколько направлений — по ним Ayla подберёт готовый список услуг.",
  selected: (n: number) => `Выбрано: ${n}`,
  hasServices: (n: number) => `Услуг выбрано: ${n}`,
  next: "Продолжить",
  nextDisabled: "Отметьте хотя бы одно направление",
  other: "Другое направление",
  otherHint:
    "Такого направления в каталоге нет. Назовите его и услугу, которую оказываете, — владелец проверит, и направление появится вместе с подтверждённой услугой.",
  otherField: "Какое направление?",
  otherContinue: "Описать услугу",
  otherCancel: "Отмена",
  directionPrefix: "Направление: ",
  salonManaged: "Услуги салона ведёт владелец салона.",
  notLinked: notConnectedText("направления пока не выбрать"),
} as const;

export const DIRECTIONS_PATH = "/solo/directions";
export const SELECT_PATH_FROM_DIRECTIONS = "/solo/services/select";

/** Что экран 02 передаёт экрану 03 навигацией — и ничем больше. */
export interface DirectionsNavState {
  directionIds: string[];
}

type Load =
  | { kind: "loading" }
  | { kind: "ready"; directions: ServiceDirection[]; selection: ServiceSelectionState }
  | { kind: "salon_managed" }
  | { kind: "not_linked" }
  | { kind: "error"; err: unknown };

function refusalReason(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const reason = e.details?.reason;
  return typeof reason === "string" ? reason : e.slug;
}

/** Направления, у которых есть активная услуга, — производное, не запись. */
export function derivedDirectionIds(selection: ServiceSelectionState): Set<string> {
  const ids = new Set<string>();
  for (const s of selection.services) {
    if (s.is_active && s.direction_id) ids.add(s.direction_id);
  }
  return ids;
}

/** Сколько активных услуг сервера лежит в направлении. */
export function servicesInDirection(selection: ServiceSelectionState, directionId: string): number {
  return selection.services.filter((s) => s.is_active && s.direction_id === directionId).length;
}

export function MasterDirectionsScreen() {
  const navigate = useNavigate();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherName, setOtherName] = useState("");
  const [otherFormOpen, setOtherFormOpen] = useState(false);
  const [ownNotLinked, setOwnNotLinked] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoad({ kind: "loading" });
    Promise.all([getServiceDirections(), getServiceSelection()])
      .then(([directions, selection]) => {
        if (!alive) return;
        setLoad({ kind: "ready", directions: directions.directions, selection });
        // Предвыбор — из услуг, которые уже есть: это и есть «направления
        // мастера» сегодня. Дальше множество живёт в состоянии экрана.
        setChecked(derivedDirectionIds(selection));
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

  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const orderedChecked = useMemo(() => {
    if (load.kind !== "ready") return [];
    // Порядок — как в каталоге, а не как кликали: экран 03 идёт по списку.
    return load.directions.filter((d) => checked.has(d.id)).map((d) => d.id);
  }, [load, checked]);

  const header = (
    <header className="master-services__header">
      <h1>{DIRECTIONS_COPY.title}</h1>
    </header>
  );

  if (load.kind !== "ready") {
    return (
      <div className="screen master-services">
        {header}
        {load.kind === "loading" && <SystemState kind="loading" />}
        {load.kind === "salon_managed" && (
          <p className="callout" role="status">
            {DIRECTIONS_COPY.salonManaged}
          </p>
        )}
        {load.kind === "not_linked" && <StudioCallout text={DIRECTIONS_COPY.notLinked} />}
        {load.kind === "error" && (
          <SystemState
            kind="load_error"
            what="directions"
            err={load.err}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        )}
      </div>
    );
  }

  const { directions, selection } = load;
  const state: DirectionsNavState = { directionIds: orderedChecked };

  return (
    <div className="screen master-services">
      {header}
      <p className="master-services__note">{DIRECTIONS_COPY.hint}</p>
      {/* Счётчик — от состояния: `checked.size`, не константа и не поле ответа. */}
      <p className="service-select__counter" role="status" data-testid="directions-counter">
        {DIRECTIONS_COPY.selected(checked.size)}
      </p>
      {message && <p role="status">{message}</p>}

      <fieldset className="service-select__group" aria-label={DIRECTIONS_COPY.title}>
        {directions.map((d) => {
          const inDirection = servicesInDirection(selection, d.id);
          return (
            <label key={d.id} className="service-card service-select__item" data-testid={`direction-${d.id}`}>
              <input type="checkbox" checked={checked.has(d.id)} onChange={() => toggle(d.id)} />
              {d.icon && (
                <span className="service-card__icon" aria-hidden="true">
                  {d.icon}
                </span>
              )}
              <span className="service-card__name">{d.name}</span>
              {inDirection > 0 && (
                <span className="service-card__meta">{DIRECTIONS_COPY.hasServices(inDirection)}</span>
              )}
            </label>
          );
        })}
      </fieldset>

      <button
        type="button"
        className="btn-primary"
        disabled={checked.size === 0}
        title={checked.size === 0 ? DIRECTIONS_COPY.nextDisabled : undefined}
        onClick={() => navigate(SELECT_PATH_FROM_DIRECTIONS, { state })}
      >
        {DIRECTIONS_COPY.next}
      </button>

      <section className="master-services__section master-services__own" aria-label={DIRECTIONS_COPY.other}>
        {ownNotLinked ? (
          <p className="callout" role="status">
            {NOT_LINKED_MESSAGE}
          </p>
        ) : otherFormOpen ? (
          <OwnServiceForm
            availability="available"
            initialDraft={{ description: `${DIRECTIONS_COPY.directionPrefix}${otherName.trim()}` }}
            onCreated={() => {
              setOtherFormOpen(false);
              setOtherOpen(false);
              setOtherName("");
              setMessage(SENT_MESSAGE);
            }}
            onSelected={(next) => {
              setOtherFormOpen(false);
              setOtherOpen(false);
              setOtherName("");
              setMessage(pickedMessage(next.selected));
              setLoad((prev) => (prev.kind === "ready" ? { ...prev, selection: next } : prev));
            }}
            onNotLinked={() => setOwnNotLinked(true)}
            onSubmitStart={() => setMessage(null)}
          />
        ) : otherOpen ? (
          <div className="service-select__other">
            <p className="master-services__note">{DIRECTIONS_COPY.otherHint}</p>
            <label className="service-select__search">
              {DIRECTIONS_COPY.otherField}
              <input
                type="text"
                value={otherName}
                onChange={(e) => setOtherName(e.target.value)}
                maxLength={80}
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={otherName.trim().length === 0}
              onClick={() => setOtherFormOpen(true)}
            >
              {DIRECTIONS_COPY.otherContinue}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setOtherOpen(false);
                setOtherName("");
              }}
            >
              {DIRECTIONS_COPY.otherCancel}
            </button>
          </div>
        ) : (
          <button type="button" className="btn-secondary" onClick={() => setOtherOpen(true)}>
            {`+ ${DIRECTIONS_COPY.other}`}
          </button>
        )}
      </section>
    </div>
  );
}
