/**
 * Экран 03 онбординга мастера — выбор услуг по направлению (DRF-1809, M17).
 *
 * Route: /solo/services/select. Вход — пункт готовности «services» при нуле
 * выбранных и кнопка «Выбрать из каталога» на экране 04; выход — экран 04
 * (/solo/services), который сам читает выбор.
 *
 * Всё — из ответов сервера (прокси бота, M7 и M8a):
 *   - направления — `GET /services/directions`: ни их числа, ни кодов экран не
 *     знает. Оговорка #454 / G7: сегодня это корни канона, а не шесть
 *     направлений экрана 02 — ответ G7 меняет данные каталога, а не экран;
 *   - шаблоны направления — `GET /services/templates?direction_id=`, группы по
 *     подкатегории; поиск — по имени внутри уже полученного ответа;
 *   - уже выбранное и «Выбрано: N» — `GET/POST /services/selection`; после
 *     «Сохранить» состояние берётся из ответа, а не дописывается;
 *   - 3.4 «Готово с «…»!» — итог по этому направлению из ответа выбора.
 *
 * Цен и длительности на этом шаге нет (они на экране 04); фото у шаблонов
 * канона нет — не рисуется. Уже выбранную услугу здесь не снимают: «Убрать»
 * на экране 04, где видны будущие записи.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  NOT_LINKED_MESSAGE,
  OWN_TITLE,
  OwnServiceForm,
  SENT_MESSAGE,
  pickedMessage,
  type PickAvailability,
} from "../components/OwnServiceForm";
import { ApiError } from "../lib/api";
import {
  getServiceDirections,
  getServiceSelection,
  getServiceTemplates,
  selectServices,
  type ServiceDirection,
  type ServiceSelectionState,
  type ServiceTemplate,
} from "../lib/master-api";

export const SELECT_COPY = {
  title: "Выберите услуги",
  directionsTitle: "Направления",
  selected: (n: number) => `Выбрано: ${n}`,
  inDirection: (n: number) => `Выбрано: ${n}`,
  search: "Поиск по названию",
  nothingFound: "Ничего не нашлось.",
  noTemplates: "В этом направлении пока нет услуг каталога.",
  save: "Сохранить выбор",
  alreadySelected: "Уже в ваших услугах",
  done: (name: string) => `Готово с «${name}»!`,
  doneSummary: (n: number) => `Услуг в этом направлении: ${n}`,
  nextDirection: "Следующее направление",
  backToDirections: "К списку направлений",
  toPrices: "Перейти к ценам",
  addOwn: "+ Добавить свою услугу",
  loadError: "Не удалось загрузить каталог услуг.",
  templatesError: "Не удалось загрузить услуги направления.",
  saveError: "Не получилось сохранить выбор.",
  retry: "Повторить",
  salonManaged: "Услуги салона ведёт владелец салона.",
  notLinked: "Профиль ещё не привязан — привязку выполнит оператор.",
} as const;

export const PRICES_PATH = "/solo/services";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; directions: ServiceDirection[]; selection: ServiceSelectionState }
  | { kind: "salon_managed" }
  | { kind: "not_linked" }
  | { kind: "error" };

type TemplatesLoad =
  | { kind: "loading" }
  | { kind: "ready"; templates: ServiceTemplate[] }
  | { kind: "error" };

function refusalReason(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const reason = e.details?.reason;
  return typeof reason === "string" ? reason : e.slug;
}

/** Сколько выбранных услуг сервера лежит в направлении — из ответа выбора. */
export function selectedInDirection(selection: ServiceSelectionState, directionId: string): number {
  return selection.services.filter((s) => s.direction_id === directionId && s.is_active).length;
}

function groupByCategory(templates: ServiceTemplate[]): Array<{ name: string; items: ServiceTemplate[] }> {
  const groups = new Map<string, { name: string; items: ServiceTemplate[] }>();
  for (const t of templates) {
    const key = t.category_id ?? "";
    let group = groups.get(key);
    if (!group) {
      group = { name: t.category_name ?? "", items: [] };
      groups.set(key, group);
    }
    group.items.push(t);
  }
  return Array.from(groups.values());
}

function DirectionPicker({
  direction,
  selection,
  onSaved,
  onDone,
}: {
  direction: ServiceDirection;
  selection: ServiceSelectionState;
  onSaved: (state: ServiceSelectionState) => void;
  onDone: () => void;
}) {
  const [load, setLoad] = useState<TemplatesLoad>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoad({ kind: "loading" });
    getServiceTemplates(direction.id)
      .then((res) => {
        if (alive) setLoad({ kind: "ready", templates: res.templates });
      })
      .catch(() => {
        if (alive) setLoad({ kind: "error" });
      });
    return () => {
      alive = false;
    };
  }, [direction.id, reloadKey]);

  const already = useMemo(
    () => new Set(selection.services.filter((s) => s.is_active).map((s) => s.template_id)),
    [selection],
  );

  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = async () => {
    const ids = Array.from(checked).filter((id) => !already.has(id));
    if (ids.length === 0) {
      onDone();
      return;
    }
    setBusy(true);
    setSaveError(null);
    try {
      const state = await selectServices(ids);
      setChecked(new Set());
      onSaved(state);
      onDone();
    } catch {
      setSaveError(SELECT_COPY.saveError);
    } finally {
      setBusy(false);
    }
  };

  if (load.kind === "loading") {
    return <div className="skeleton service-card service-card--skel" aria-busy="true" />;
  }
  if (load.kind === "error") {
    return (
      <div className="callout callout--danger" role="alert">
        <p>{SELECT_COPY.templatesError}</p>
        <button type="button" className="btn-secondary" onClick={() => setReloadKey((k) => k + 1)}>
          {SELECT_COPY.retry}
        </button>
      </div>
    );
  }

  const needle = query.trim().toLocaleLowerCase("ru");
  const visible = needle
    ? load.templates.filter((t) => t.name.toLocaleLowerCase("ru").includes(needle))
    : load.templates;

  return (
    <section className="service-select__direction" aria-label={direction.name}>
      <h2 className="master-services__section-title">{direction.name}</h2>
      <label className="service-select__search">
        {SELECT_COPY.search}
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      {load.templates.length === 0 ? (
        <p className="master-services__empty">{SELECT_COPY.noTemplates}</p>
      ) : visible.length === 0 ? (
        <p className="master-services__empty">{SELECT_COPY.nothingFound}</p>
      ) : (
        groupByCategory(visible).map((group) => (
          <fieldset key={group.name} className="service-select__group">
            {group.name && <legend className="service-select__group-title">{group.name}</legend>}
            {group.items.map((t) => {
              const isAlready = already.has(t.id);
              return (
                <label key={t.id} className="service-card service-select__item">
                  <input
                    type="checkbox"
                    checked={isAlready || checked.has(t.id)}
                    disabled={isAlready || busy}
                    onChange={() => toggle(t.id)}
                  />
                  <span className="service-card__name">{t.name}</span>
                  {isAlready && <span className="service-card__meta">{SELECT_COPY.alreadySelected}</span>}
                </label>
              );
            })}
          </fieldset>
        ))
      )}
      {saveError && (
        <p className="master-services__field-error" role="alert">
          {saveError}
        </p>
      )}
      <button type="button" className="btn-primary" disabled={busy} onClick={() => void save()}>
        {SELECT_COPY.save}
      </button>
    </section>
  );
}

export function MasterServiceSelectScreen() {
  const navigate = useNavigate();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [doneId, setDoneId] = useState<string | null>(null);
  const [ownOpen, setOwnOpen] = useState(false);
  const [ownNotLinked, setOwnNotLinked] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoad({ kind: "loading" });
    Promise.all([getServiceDirections(), getServiceSelection()])
      .then(([directions, selection]) => {
        if (alive) setLoad({ kind: "ready", directions: directions.directions, selection });
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

  const applySelection = useCallback((selection: ServiceSelectionState) => {
    setLoad((prev) => (prev.kind === "ready" ? { ...prev, selection } : prev));
  }, []);

  const header = (
    <header className="master-services__header">
      <h1>{SELECT_COPY.title}</h1>
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
            {SELECT_COPY.salonManaged}
          </p>
        )}
        {load.kind === "not_linked" && (
          <p className="callout" role="status">
            {SELECT_COPY.notLinked}
          </p>
        )}
        {load.kind === "error" && (
          <div className="callout callout--danger" role="alert">
            <p>{SELECT_COPY.loadError}</p>
            <button type="button" className="btn-secondary" onClick={() => setReloadKey((k) => k + 1)}>
              {SELECT_COPY.retry}
            </button>
          </div>
        )}
      </div>
    );
  }

  const { directions, selection } = load;
  const active = directions.find((d) => d.id === activeId) ?? null;
  const done = directions.find((d) => d.id === doneId) ?? null;
  const doneIndex = done ? directions.indexOf(done) : -1;
  const nextDirection = doneIndex >= 0 ? directions[doneIndex + 1] ?? null : null;
  const availability: PickAvailability = "available";

  return (
    <div className="screen master-services">
      {header}
      <p className="service-select__counter" role="status">
        {SELECT_COPY.selected(selection.selected)}
      </p>
      {message && <p role="status">{message}</p>}

      {done ? (
        <section className="service-select__done" aria-label={SELECT_COPY.done(done.name)}>
          <h2 className="master-services__section-title">{SELECT_COPY.done(done.name)}</h2>
          <p>{SELECT_COPY.doneSummary(selectedInDirection(selection, done.id))}</p>
          {nextDirection ? (
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                setDoneId(null);
                setActiveId(nextDirection.id);
              }}
            >
              {SELECT_COPY.nextDirection}
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={() => navigate(PRICES_PATH)}>
              {SELECT_COPY.toPrices}
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={() => setDoneId(null)}>
            {SELECT_COPY.backToDirections}
          </button>
        </section>
      ) : active ? (
        <>
          <DirectionPicker
            key={active.id}
            direction={active}
            selection={selection}
            onSaved={applySelection}
            onDone={() => {
              setActiveId(null);
              setDoneId(active.id);
            }}
          />
          <button type="button" className="btn-secondary" onClick={() => setActiveId(null)}>
            {SELECT_COPY.backToDirections}
          </button>
        </>
      ) : (
        <section className="service-select__directions" aria-label={SELECT_COPY.directionsTitle}>
          <h2 className="master-services__section-title">{SELECT_COPY.directionsTitle}</h2>
          {directions.map((d) => (
            <button
              key={d.id}
              type="button"
              className="service-card service-select__direction-row"
              onClick={() => setActiveId(d.id)}
            >
              <span className="service-card__name">{d.name}</span>
              <span className="service-card__meta">
                {SELECT_COPY.inDirection(selectedInDirection(selection, d.id))}
              </span>
            </button>
          ))}
          <button type="button" className="btn-primary" onClick={() => navigate(PRICES_PATH)}>
            {SELECT_COPY.toPrices}
          </button>
        </section>
      )}

      <section className="master-services__section master-services__own" aria-label={OWN_TITLE}>
        {ownNotLinked ? (
          <p className="callout" role="status">
            {NOT_LINKED_MESSAGE}
          </p>
        ) : ownOpen ? (
          <OwnServiceForm
            availability={availability}
            onCreated={() => {
              setOwnOpen(false);
              setMessage(SENT_MESSAGE);
            }}
            onSelected={(state) => {
              setOwnOpen(false);
              setMessage(pickedMessage(state.selected));
              applySelection(state);
            }}
            onNotLinked={() => setOwnNotLinked(true)}
          />
        ) : (
          <button type="button" className="btn-secondary" onClick={() => setOwnOpen(true)}>
            {SELECT_COPY.addOwn}
          </button>
        )}
      </section>
    </div>
  );
}
