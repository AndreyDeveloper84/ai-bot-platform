/**
 * «Новая запись» — manual booking for the salon front desk.
 *
 * Built to `ayla-knowledge/07 UX/Ayla Master Schedule UX Contract.md`
 * §12–18. The rules live in `lib/booking-draft.ts`; this screen is their
 * surface.
 *
 * ### Shape, and why it is not a wizard
 *
 * §12: «a progressive booking draft on one primary screen, not a
 * mandatory visual stepper or long wizard». So every row is always
 * visible and always tappable — the receptionist who already knows the
 * time can fill the day first and the name last. What the contract fixes
 * is the *business* order (Customer → Service → Date/time), not the
 * order of taps, and the business order is enforced where it actually
 * matters: the slot list refuses to load until service and master are
 * known.
 *
 * ### Deviation from the mock, stated rather than hidden
 *
 * The contract's mock has three rows — Клиент / Услуга / Дата и время —
 * because it was drawn for a master's own app, where the assignment is
 * implicit: the master is the master. The salon front desk books *for*
 * someone, so this screen adds a Мастер row. That is a deviation from
 * the drawing and a faithful reading of the logic: §12 names «duration/
 * assignment context» as what makes an availability query meaningful,
 * and on this surface the assignment has to be chosen.
 *
 * ### The three promises this screen keeps
 *
 * - **Nothing shifts silently** (§12). When a change invalidates the
 *   chosen start, the screen says which change did it.
 * - **The client never computes a slot** (§17). Times come from
 *   `/booking-slots/` and a failure to reach it is rendered as «could
 *   not ask», never as «nothing free».
 * - **«Выбранное окно» is the range we started from, not the booking's
 *   length** (§12) — including the wording of the label.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "../../lib/api";
import {
  createSalonBooking,
  CustomerSearchUnavailable,
  getBookingSlots,
  getCatalogServicesForAdmin,
  listMasters,
  searchSalonCustomers,
  type BookingSlot,
  type CatalogServiceLite,
  type MasterListItem,
  type SalonCustomer,
} from "../../lib/admin-api";
import {
  applyDraftAction,
  canQueryAvailability,
  canReview,
  EMPTY_DRAFT,
  missingSteps,
  outcomeKeepsDraft,
  SUBMIT_OUTCOME_COPY,
  type BookingDraft,
  type DraftAction,
  type SlotInvalidationReason,
  type SubmitOutcome,
} from "../../lib/booking-draft";
import { setBackButton } from "../../lib/max-sdk";

type Sheet = "customer" | "service" | "master" | "time" | null;

const INVALIDATION_COPY: Record<SlotInvalidationReason, string> = {
  service_changed: "Время сброшено — у новой услуги другая длительность.",
  master_changed: "Время сброшено — у другого мастера своё расписание.",
  window_changed: "Время сброшено — вы выбрали другой день.",
};

function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function shiftIsoDate(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12);
  dt.setDate(dt.getDate() + days);
  return toIsoDate(dt);
}

function formatDayTitle(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12);
  const out = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    weekday: "short",
  }).format(dt);
  return out.charAt(0).toUpperCase() + out.slice(1);
}

/** One row of the primary screen: label, current value or «выбрать». */
function DraftRow({
  label,
  value,
  onOpen,
}: {
  label: string;
  value: string | null;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="draft-row"
      onClick={onOpen}
      aria-label={`${label}: ${value ?? "выбрать"}`}
      style={{
        display: "flex",
        width: "100%",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "var(--s-3)",
        padding: "var(--s-3) 0",
        borderBottom: "1px solid var(--c-divider)",
        background: "none",
        border: "none",
        textAlign: "start",
      }}
    >
      <span style={{ color: "var(--c-text-secondary)" }}>{label}</span>
      <span style={{ fontWeight: value ? 600 : 400, textAlign: "end" }}>
        {value ?? "выбрать"}
      </span>
    </button>
  );
}

function Sheet({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
      <div className="sheet__panel">
        <h2 className="sheet__title">{title}</h2>
        {children}
        <button type="button" className="btn-secondary" onClick={onClose}>
          Закрыть
        </button>
      </div>
    </div>
  );
}

export function AdminNewBookingScreen() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [draft, setDraft] = useState<BookingDraft>(EMPTY_DRAFT);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [notice, setNotice] = useState<string>("");

  // The day the time picker is looking at. Seeded from the free interval
  // the draft was started from (§16: «The picker starts with the selected
  // day when entered from a free interval»), else today.
  const [date, setDate] = useState<string>(
    () => searchParams.get("date") || toIsoDate(new Date()),
  );

  const [services, setServices] = useState<CatalogServiceLite[]>([]);
  const [masters, setMasters] = useState<MasterListItem[]>([]);
  const [slots, setSlots] = useState<BookingSlot[] | null>(null);
  const [slotsErr, setSlotsErr] = useState<unknown>(null);
  const [slotsLoading, setSlotsLoading] = useState(false);

  // The salon's timezone, as the schedule reported it. Only the server
  // knows it, and §18 requires the review to state it.
  const [timeZone, setTimeZone] = useState<string>("");

  // Customer search (§13). `searchState` is deliberately four-valued —
  // «found nothing» and «could not look» must never collapse into one.
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SalonCustomer[]>([]);
  const [searchState, setSearchState] = useState<
    "idle" | "searching" | "done" | "unavailable" | "error"
  >("idle");

  // Inline «Новый клиент» form (§14 — the minimum is name + phone).
  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");

  // §18 — the outcome of a submit. `null` means «not submitted», which is a
  // different thing from every value it can hold.
  const [outcome, setOutcome] = useState<SubmitOutcome | null>(null);
  const [outcomeDetail, setOutcomeDetail] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // One key per booking attempt, kept across retries on purpose. Ayla
  // invents a key when the header is absent, so retrying with a fresh one
  // books the customer twice — which is exactly what the «pending» outcome
  // tempts a worried receptionist into doing.
  const idempotencyKey = useRef<string>(crypto.randomUUID());

  useEffect(() => {
    setBackButton(true);
  }, []);

  const dispatch = useCallback((action: DraftAction) => {
    setDraft((current) => {
      const { draft: next, slotInvalidatedBy } = applyDraftAction(current, action);
      // §12 — the system never silently drops the start. If a change took
      // it, the change has to announce itself.
      setNotice(slotInvalidatedBy ? INVALIDATION_COPY[slotInvalidatedBy] : "");
      return next;
    });
  }, []);

  useEffect(() => {
    void (async () => {
      const [svc, mst] = await Promise.allSettled([
        getCatalogServicesForAdmin(),
        listMasters({ is_active: true, limit: 50 }),
      ]);
      if (svc.status === "fulfilled") setServices(svc.value);
      if (mst.status === "fulfilled") setMasters(mst.value.items);
    })();
  }, []);

  // Slots load only once the draft can meaningfully ask (§12/§17).
  const readyForSlots = canQueryAvailability(draft);
  useEffect(() => {
    if (!readyForSlots || sheet !== "time") return;
    const controller = new AbortController();
    setSlotsLoading(true);
    setSlotsErr(null);
    setSlots(null);
    void (async () => {
      try {
        const res = await getBookingSlots(
          {
            masterId: draft.master!.id,
            serviceId: draft.service!.id,
            date,
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setSlots(res.slots);
        setTimeZone(res.timezone);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setSlotsErr(e);
      } finally {
        if (!controller.signal.aborted) setSlotsLoading(false);
      }
    })();
    return () => controller.abort();
  }, [readyForSlots, sheet, date, draft.master, draft.service]);

  // §13 — debounced search. Runs only from two characters: a one-letter
  // query would return most of the salon and disambiguate nothing.
  useEffect(() => {
    if (sheet !== "customer") return;
    const q = query.trim();
    if (q.length < 2) {
      setSearchState("idle");
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setSearchState("searching");
      void (async () => {
        try {
          const found = await searchSalonCustomers(q, { signal: controller.signal });
          if (controller.signal.aborted) return;
          setResults(found);
          setSearchState("done");
        } catch (e) {
          if ((e as DOMException | undefined)?.name === "AbortError") return;
          setResults([]);
          // The capability being absent and the request failing are both
          // «could not look», and neither is «not here» (§13).
          setSearchState(e instanceof CustomerSearchUnavailable ? "unavailable" : "error");
        }
      })();
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, sheet]);

  const submit = useCallback(async () => {
    if (!canReview(draft) || submitting) return;
    setSubmitting(true);
    setOutcome(null);
    try {
      const res = await createSalonBooking({
        master_id: draft.master!.id,
        service_id: draft.service!.id,
        // The schedule's own timestamp. When it sent none we cannot invent
        // one (§17), so the slot is unusable for a commit and the button
        // stays out of reach — see `canSubmit` below.
        start_at: draft.slot!.start_at ?? "",
        idempotency_key: idempotencyKey.current,
        ...(draft.customer!.kind === "existing"
          ? { client_id: draft.customer!.id }
          : {
              client_name: draft.customer!.name,
              client_phone: draft.customer!.phone,
            }),
      });
      setOutcome(res.outcome);
      setOutcomeDetail(res.detail);
      if (res.outcome === "committed") {
        // A fresh key for whatever is booked next; this one is spent.
        idempotencyKey.current = crypto.randomUUID();
        dispatch({ type: "reset" });
      }
    } finally {
      setSubmitting(false);
    }
  }, [draft, submitting, dispatch]);

  const customerLabel = useMemo(() => {
    if (draft.customer === null) return null;
    if (draft.customer.kind === "new") return `${draft.customer.name} (новый)`;
    // The masked phone is absent today — the lookup does not return one.
    // Rendering «Мария · undefined» would be worse than the name alone.
    return draft.customer.phone_masked
      ? `${draft.customer.name} · ${draft.customer.phone_masked}`
      : draft.customer.name;
  }, [draft.customer]);

  const slotLabel = draft.slot ? `${formatDayTitle(date)} · ${draft.slot.time}` : null;

  const missing = missingSteps(draft);
  const ready = canReview(draft);
  // A slot the schedule labelled but did not timestamp cannot be committed:
  // building the instant here would mean the client picking a timezone,
  // which §17 forbids. Rare, and better refused than guessed.
  const commitable = ready && !!draft.slot?.start_at;

  return (
    <div className="screen admin-flow-screen">
      <button
        type="button"
        className="admin-flow-back"
        onClick={() => navigate("/admin/day")}
      >
        ← День салона
      </button>
      <h1 className="screen__title">Новая запись</h1>

      {draft.window && (
        <div className="callout" style={{ marginBottom: "var(--s-3)" }}>
          {/* §12 fixes this label. It is the range the draft started from,
              never the length of the appointment. */}
          <div style={{ color: "var(--c-text-secondary)" }}>Выбранное окно</div>
          <div style={{ fontWeight: 600 }}>
            {formatDayTitle(date)} · {draft.window.start_at}–{draft.window.end_at}
          </div>
        </div>
      )}

      <div className="draft-rows">
        <DraftRow label="Клиент" value={customerLabel} onOpen={() => setSheet("customer")} />
        <DraftRow
          label="Услуга"
          value={draft.service ? draft.service.name : null}
          onOpen={() => setSheet("service")}
        />
        <DraftRow
          label="Мастер"
          value={draft.master ? draft.master.name : null}
          onOpen={() => setSheet("master")}
        />
        <DraftRow label="Дата и время" value={slotLabel} onOpen={() => setSheet("time")} />
      </div>

      {notice && (
        <div className="callout callout--warning" role="status" aria-live="polite">
          {notice}
        </div>
      )}

      {ready && (
        // §18 — review is a confirmation of intent, shown before the
        // irreversible tap and carrying exactly what was chosen.
        <section className="callout" style={{ marginTop: "var(--s-4) " }}>
          <h2 className="section__title">Проверьте запись</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            <li>Клиент: {customerLabel}</li>
            <li>Услуга: {draft.service?.name}</li>
            <li>Мастер: {draft.master?.name}</li>
            <li>
              Когда: {slotLabel}
              {timeZone ? ` (${timeZone})` : ""}
            </li>
            <li>Длительность: {draft.service?.duration_min} мин</li>
          </ul>
          {/* §12 step 5 lists a price snapshot «where available» and adds
              «do not invent missing domain fields». The catalog read this
              screen uses carries no price, so the line is absent rather
              than guessed. */}
        </section>
      )}


      <div style={{ marginTop: "var(--s-5)" }}>
        {!ready && (
          <p style={{ color: "var(--c-text-secondary)", margin: "0 0 var(--s-2)" }}>
            {`Осталось выбрать: ${missing.join(", ")}.`}
          </p>
        )}
        <button
          type="button"
          className="cta-bar__button"
          style={{ width: "100%" }}
          disabled={!commitable || submitting}
          aria-disabled={!commitable || submitting}
          onClick={() => void submit()}
        >
          {submitting ? "Создаю…" : "Создать запись"}
        </button>
        {ready && !commitable && (
          <p style={{ color: "var(--c-text-secondary)", margin: "var(--s-2) 0 0" }}>
            У этого времени нет точной метки от расписания — выберите другое.
          </p>
        )}
      </div>

      {outcome !== null && (
        // §18 — four distinguishable outcomes. `pending` in particular must
        // not read as success: «Do not claim creation». It also must not
        // read as failure, or the receptionist presses again and the client
        // is booked twice.
        <section
          className={outcome === "committed" ? "callout" : "callout callout--warning"}
          role="status"
          aria-live="polite"
          style={{ marginTop: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>{SUBMIT_OUTCOME_COPY[outcome]}</p>
          {outcomeDetail && (
            <p
              style={{
                margin: "var(--s-1) 0 0",
                color: "var(--c-text-secondary)",
                fontSize: "var(--font-size-100)",
              }}
            >
              {outcomeDetail}
            </p>
          )}
          {outcomeKeepsDraft(outcome) && (
            <p style={{ margin: "var(--s-1) 0 0", color: "var(--c-text-secondary)" }}>
              Введённые данные сохранены.
            </p>
          )}
          {outcome === "pending" && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "var(--s-2)" }}
              onClick={() => navigate("/admin/day")}
            >
              Открыть день салона
            </button>
          )}
        </section>
      )}

      {sheet === "customer" && (
        <Sheet title="Клиент" onClose={() => setSheet(null)}>
          {/* §13 — search shows only what disambiguates a person: a name
              and a masked phone. Nothing about their history, and never a
              raw number. */}
          <label>
            Найти клиента
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Имя или телефон"
              aria-label="Поиск клиента"
            />
          </label>

          {searchState === "idle" && (
            <p style={{ color: "var(--c-text-secondary)", margin: 0 }}>
              Введите хотя бы два символа.
            </p>
          )}

          {searchState === "searching" && (
            <div className="callout" role="status">
              Ищу…
            </div>
          )}

          {searchState === "unavailable" && (
            // Not «нет такого клиента». §13: «A failed search is not proof
            // that the customer does not exist» — telling the receptionist
            // otherwise is how duplicates get created.
            <div className="callout callout--warning" role="status">
              Поиск по клиентам пока недоступен. Это не значит, что клиента нет —
              заведите его как нового.
            </div>
          )}

          {searchState === "error" && (
            <div className="callout callout--warning" role="status">
              Не удалось выполнить поиск. Это не значит, что клиента нет.
            </div>
          )}

          {searchState === "done" && results.length === 0 && (
            <div className="callout" role="status">
              Совпадений нет. Возможно, клиент записан под другим именем или
              телефоном.
            </div>
          )}

          {searchState === "done" && results.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {results.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className="sheet__item"
                    onClick={() => {
                      dispatch({
                        type: "customer/set",
                        customer: {
                          kind: "existing",
                          id: c.id,
                          name: c.name,
                          phone_masked: c.phone_masked,
                        },
                      });
                      setSheet(null);
                    }}
                  >
                    {c.phone_masked ? `${c.name} · ${c.phone_masked}` : c.name}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3 className="section__title">Новый клиент</h3>
          <label>
            Имя
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              aria-label="Имя клиента"
            />
          </label>
          <label>
            Телефон
            <input
              type="tel"
              value={newPhone}
              onChange={(e) => setNewPhone(e.target.value)}
              aria-label="Телефон клиента"
            />
          </label>
          <button
            type="button"
            className="cta-bar__button"
            disabled={!newName.trim() || !newPhone.trim()}
            onClick={() => {
              dispatch({
                type: "customer/set",
                customer: { kind: "new", name: newName.trim(), phone: newPhone.trim() },
              });
              setSheet(null);
            }}
          >
            Сохранить клиента
          </button>
        </Sheet>
      )}

      {sheet === "service" && (
        <Sheet title="Услуга" onClose={() => setSheet(null)}>
          {services.length === 0 ? (
            <div className="callout" role="status">
              Список услуг не загрузился. Откройте «Услуги» и проверьте каталог.
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {services.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    className="sheet__item"
                    onClick={() => {
                      dispatch({
                        type: "service/set",
                        service: {
                          id: s.id,
                          name: s.name,
                          duration_min: s.duration_min ?? 0,
                        },
                      });
                      setSheet(null);
                    }}
                  >
                    {s.name}
                    {s.duration_min ? ` · ${s.duration_min} мин` : ""}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Sheet>
      )}

      {sheet === "master" && (
        <Sheet title="Мастер" onClose={() => setSheet(null)}>
          {masters.length === 0 ? (
            <div className="callout" role="status">
              Список мастеров не загрузился. Откройте «Команда» и проверьте состав.
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {masters.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    className="sheet__item"
                    onClick={() => {
                      dispatch({ type: "master/set", master: { id: m.id, name: m.name } });
                      setSheet(null);
                    }}
                  >
                    {m.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Sheet>
      )}

      {sheet === "time" && (
        <Sheet title="Дата и время" onClose={() => setSheet(null)}>
          {!readyForSlots ? (
            // §12 — the query is meaningless without both, so we say what
            // is missing instead of showing an empty or invented list.
            <div className="callout" role="status">
              Сначала выберите услугу и мастера — свободное время зависит от них.
            </div>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "var(--s-2)",
                }}
              >
                <button
                  type="button"
                  className="btn-secondary"
                  aria-label="Предыдущий день"
                  onClick={() => {
                    const next = shiftIsoDate(date, -1);
                    setDate(next);
                    dispatch({ type: "slot/clear" });
                  }}
                >
                  ←
                </button>
                <span style={{ fontWeight: 600 }}>{formatDayTitle(date)}</span>
                <button
                  type="button"
                  className="btn-secondary"
                  aria-label="Следующий день"
                  onClick={() => {
                    const next = shiftIsoDate(date, 1);
                    setDate(next);
                    dispatch({ type: "slot/clear" });
                  }}
                >
                  →
                </button>
              </div>

              {slotsLoading && (
                <div className="callout" role="status">
                  Спрашиваю расписание…
                </div>
              )}

              {!slotsLoading && slotsErr != null && (
                // §16/§17 — «could not ask» is not «nothing free».
                <div className="callout callout--warning" role="status">
                  <p style={{ margin: 0 }}>
                    {slotsErr instanceof ApiError && slotsErr.status === 503
                      ? "Расписание сейчас недоступно — свободное время не показать. Попробуйте через минуту."
                      : "Не удалось получить свободное время. Это не значит, что его нет."}
                  </p>
                </div>
              )}

              {!slotsLoading && slotsErr == null && slots !== null && slots.length === 0 && (
                <div className="callout" role="status">
                  На этот день свободного времени нет. Посмотрите соседний день.
                </div>
              )}

              {!slotsLoading && slotsErr == null && slots !== null && slots.length > 0 && (
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: 0,
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "var(--s-2)",
                  }}
                >
                  {slots.map((s) => (
                    <li key={s.time}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => {
                          dispatch({
                            type: "slot/set",
                            slot: { time: s.time, start_at: s.start_at },
                          });
                          setSheet(null);
                        }}
                      >
                        {s.time}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Sheet>
      )}
    </div>
  );
}
