/**
 * Форма «Новая запись» — одна на две поверхности (DRF-2155, М-3).
 *
 * До М-3 это было тело `AdminNewBookingScreen` (салонная стойка, UX-контракт
 * §12–18). Мастеру нужна та же форма по макету DRF-1184 — один экран
 * Клиент / Услуга / Дата и время — но под своей личностью: мастер — субъект
 * initData, не шаг черновика. Тело вынесено 1:1, ветвления — по `subject`;
 * салонный путь рисует то же самое байт в байт — стережёт
 * `AdminNewBookingScreen.snapshot.test.tsx` (три снимка DOM до вынесения).
 *
 * ### Чем отличается мастерская ветка (`subject.kind === "master"`)
 *
 * - нет строки и листа «Мастер» (`DraftRules.requiresMaster = false`);
 * - список клиентов — «Анна П. · была 12.05» / «Анна П. · новый клиент»
 *   (решение владельца 20.09; телефон макета DRF-1184 не переносится,
 *   DRF-1039); поиск — по имени;
 * - «Новый клиент» — имя + телефон + пояснение макета; телефон — только
 *   вход: ни строка «Клиент», ни «Проверьте запись», ни итог его не эхом;
 * - исходы — словами `SystemState` (М-6): «Это время занято» с
 *   вариантами из ответа и целым черновиком; «Проверяем результат» +
 *   «Проверить снова» — повтор с тем же `idempotency_key`.
 *
 * Правила черновика — `lib/booking-draft.ts`; API — через адаптер
 * `BookingFormApi`, чтобы форма не знала, чей это бэкенд.
 *
 * ### The three promises this form keeps (§12, §16/§17, §12)
 *
 * - **Nothing shifts silently.** When a change invalidates the chosen
 *   start, the form says which change did it.
 * - **The client never computes a slot.** Times come from the schedule and
 *   a failure to reach it is rendered as «could not ask», never as
 *   «nothing free».
 * - **«Выбранное окно» is the range we started from, not the booking's
 *   length** — including the wording of the label.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "../../lib/api";
import {
  CustomerSearchUnavailable,
  type BookingSlot,
  type CatalogServiceLite,
  type MasterListItem,
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
  type DraftRules,
  type SlotInvalidationReason,
  type SubmitOutcome,
} from "../../lib/booking-draft";
import { setBackButton } from "../../lib/max-sdk";
import { SystemState } from "../master/SystemState";

/** Кто записывает. Салон — *к кому-то*; мастер — к себе. */
export type BookingSubject =
  { kind: "salon" } | { kind: "master"; isSolo: boolean };

/** Строка выбора клиента — то общее, что отдают оба поиска. */
export interface FormCustomerRow {
  id: string;
  name: string;
  named?: boolean;
  /** Салонный поиск: маска, если пришла (сегодня не приходит). */
  phone_masked?: string;
  /** Мастерский поиск: YYYY-MM-DD последнего визита у этого мастера, null — новый. */
  last_visit_date?: string | null;
}

export interface FormCreateBody {
  /** Только у салона: мастер — из тела; у мастера — субъект initData. */
  master_id?: string;
  service_id: string;
  start_at: string;
  idempotency_key: string;
  client_id?: string;
  client_name?: string;
  client_phone?: string;
}

export interface FormCreateResult {
  outcome: SubmitOutcome;
  detail: string;
  appointment_id?: string;
  reason_code?: string;
  alternatives?: BookingSlot[] | null;
  alternatives_unavailable?: boolean;
  idempotency_key?: string;
}

/** Бэкенд формы — салонный (`admin-api`) или мастерский (`master-api`). */
export interface BookingFormApi {
  listServices(): Promise<CatalogServiceLite[]>;
  /** Салон — список мастеров; мастер — `[]`, строки «Мастер» нет. */
  listMasters(): Promise<MasterListItem[]>;
  searchCustomers(
    q: string,
    opts: { signal: AbortSignal },
  ): Promise<FormCustomerRow[]>;
  getSlots(
    params: { masterId?: string; serviceId: string; date: string },
    opts: { signal: AbortSignal },
  ): Promise<{ slots: BookingSlot[]; timezone: string }>;
  createBooking(body: FormCreateBody): Promise<FormCreateResult>;
}

export interface ReturnTarget {
  path: string;
  back: string;
  open: string;
}

export interface NewBookingFormProps {
  subject: BookingSubject;
  api: BookingFormApi;
  returnTo: ReturnTarget;
  /** Куда ведёт «Открыть запись» после создания (мастер, М-4). */
  bookingHref?: (appointmentId: string) => string;
}

/** «2026-05-12» → «12.05» — так владелец назвал различитель одноимённых. */
function formatLastVisit(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d ?? ""}.${m ?? ""}`;
}

/** Текст листа DRF-2155 для «Проверяем результат» после создания. */
const PENDING_BODY =
  "Не создавайте запись повторно, пока проверка не закончена.";

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
    >
      <span className="draft-row__label">{label}</span>
      <span
        className={
          value ? "draft-row__value draft-row__value--set" : "draft-row__value"
        }
      >
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

export function NewBookingForm({
  subject,
  api,
  returnTo,
  bookingHref,
}: NewBookingFormProps) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isMaster = subject.kind === "master";
  const rules = useMemo<DraftRules>(
    () => ({ requiresMaster: !isMaster }),
    [isMaster],
  );

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
  const [results, setResults] = useState<FormCustomerRow[]>([]);
  const [searchState, setSearchState] = useState<
    "idle" | "searching" | "done" | "unavailable" | "error" | "phone_closed"
  >("idle");

  // Inline «Новый клиент» form (§14 — the minimum is name + phone).
  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");

  // §18 — the outcome of a submit. `null` means «not submitted», which is a
  // different thing from every value it can hold.
  const [outcome, setOutcome] = useState<SubmitOutcome | null>(null);
  const [outcomeDetail, setOutcomeDetail] = useState("");
  // Мастер: варианты из ответа «занято» и id созданной записи для двери.
  const [alternatives, setAlternatives] = useState<BookingSlot[] | null>(null);
  const [createdId, setCreatedId] = useState<string>("");
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
      const { draft: next, slotInvalidatedBy } = applyDraftAction(
        current,
        action,
      );
      // §12 — the system never silently drops the start. If a change took
      // it, the change has to announce itself.
      setNotice(slotInvalidatedBy ? INVALIDATION_COPY[slotInvalidatedBy] : "");
      return next;
    });
  }, []);

  useEffect(() => {
    void (async () => {
      const [svc, mst] = await Promise.allSettled([
        api.listServices(),
        api.listMasters(),
      ]);
      if (svc.status === "fulfilled") setServices(svc.value);
      if (mst.status === "fulfilled") setMasters(mst.value);
    })();
  }, [api]);

  // DRF-2119 — предзаполнение из query, один раз, когда справочники
  // загружены: ассистент администратора («подготовить запись») приводит
  // сюда с `master_id` / `service_id` / `client_name` | `client_id` /
  // `start_at`. Мастер и услуга берутся ТОЛЬКО из загруженных списков —
  // чужой или несуществующий id молча не заполняется. Время из query —
  // это пожелание, не слот: слот выбирается из того, что вернёт
  // `/booking-slots/` (единый источник свободного времени, DRF-1637), а
  // пожелание показывается подсказкой. Запись отсюда не создаётся —
  // человек проходит форму до «Записать» сам.
  const prefilledRef = useRef(false);
  const [prefillHint, setPrefillHint] = useState<string>("");
  useEffect(() => {
    if (prefilledRef.current) return;
    if (masters.length === 0 && services.length === 0) return;
    prefilledRef.current = true;
    // У мастера предзаполнения нет (его зовут только «Сегодня» и «Расписание»
    // с окном); client_id из адреса на этой поверхности не принимается.
    if (isMaster) return;

    const masterId = searchParams.get("master_id") ?? "";
    const serviceId = searchParams.get("service_id") ?? "";
    const clientName = (searchParams.get("client_name") ?? "").trim();
    const clientId = (searchParams.get("client_id") ?? "").trim();
    const startAt = searchParams.get("start_at") ?? "";

    const service = services.find((row) => row.id === serviceId);
    if (service) {
      dispatch({
        type: "service/set",
        service: {
          id: service.id,
          name: service.name,
          duration_min: service.duration_min ?? 0,
        },
      });
    }
    const master = masters.find((row) => row.id === masterId);
    if (master) {
      dispatch({
        type: "master/set",
        master: { id: master.id, name: master.name },
      });
    }
    if (clientId && clientName) {
      dispatch({
        type: "customer/set",
        customer: { kind: "existing", id: clientId, name: clientName },
      });
    } else if (clientName) {
      dispatch({
        type: "customer/set",
        customer: { kind: "new", name: clientName, phone: "" },
      });
    }
    const wishTime = /T(\d{2}:\d{2})/.exec(startAt)?.[1];
    if (wishTime) {
      setPrefillHint(
        `Ayla предложила ${wishTime} — выберите время из доступных.`,
      );
    }
  }, [masters, services, searchParams, dispatch, isMaster]);

  // Мастер: из «Расписания» тап по свободному окну приводит с
  // `?date&from&to` — «Выбранное окно: 14:00–17:00» (DRF-1183, состояние 4).
  // Подпись — диапазон, с которого начали, не длительность записи.
  const windowRef = useRef(false);
  // День окна — из адреса, а не текущий день листа времени: листая ←/→,
  // мастер не должен увидеть «23 сентября · 14:00–17:00» для окна 22-го.
  const [windowDate, setWindowDate] = useState<string>("");
  useEffect(() => {
    if (!isMaster || windowRef.current) return;
    windowRef.current = true;
    const from = searchParams.get("from") ?? "";
    const to = searchParams.get("to") ?? "";
    const day = searchParams.get("date") ?? "";
    if (/^\d{2}:\d{2}$/.test(from) && /^\d{2}:\d{2}$/.test(to)) {
      dispatch({ type: "window/set", window: { start_at: from, end_at: to } });
      setWindowDate(day);
    }
  }, [isMaster, searchParams, dispatch]);

  // Slots load only once the draft can meaningfully ask (§12/§17).
  const readyForSlots = canQueryAvailability(draft, rules);
  useEffect(() => {
    if (!readyForSlots || sheet !== "time") return;
    const controller = new AbortController();
    setSlotsLoading(true);
    setSlotsErr(null);
    setSlots(null);
    void (async () => {
      try {
        const res = await api.getSlots(
          {
            masterId: draft.master?.id,
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
  }, [readyForSlots, sheet, date, draft.master, draft.service, api]);

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
          const found = await api.searchCustomers(q, {
            signal: controller.signal,
          });
          if (controller.signal.aborted) return;
          setResults(found);
          setSearchState("done");
        } catch (e) {
          if ((e as DOMException | undefined)?.name === "AbortError") return;
          setResults([]);
          // The capability being absent and the request failing are both
          // «could not look», and neither is «not here» (§13).
          setSearchState(
            e instanceof CustomerSearchUnavailable
              ? "unavailable"
              : e instanceof ApiError && e.slug === "phone_search_closed"
                ? "phone_closed"
                : "error",
          );
        }
      })();
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, sheet, api]);

  const submit = useCallback(async () => {
    if (!canReview(draft, rules) || submitting) return;
    setSubmitting(true);
    // «Проверить снова» у мастера — тот же запрос под той же карточкой:
    // не снимать «Проверяем результат» на время повтора.
    if (!(isMaster && outcome === "pending")) setOutcome(null);
    setAlternatives(null);
    try {
      const res = await api.createBooking({
        ...(draft.master ? { master_id: draft.master.id } : {}),
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
      if (res.outcome === "conflict") setAlternatives(res.alternatives ?? null);
      if (res.outcome === "committed") {
        setCreatedId(res.appointment_id ?? "");
        // A fresh key for whatever is booked next; this one is spent.
        idempotencyKey.current = crypto.randomUUID();
        dispatch({ type: "reset" });
      }
    } finally {
      setSubmitting(false);
    }
  }, [draft, submitting, dispatch, api, rules, isMaster, outcome]);

  const customerLabel = useMemo(() => {
    if (draft.customer === null) return null;
    if (draft.customer.kind === "new") return `${draft.customer.name} (новый)`;
    // The masked phone is absent today — the lookup does not return one.
    // Rendering «Мария · undefined» would be worse than the name alone.
    return draft.customer.phone_masked
      ? `${draft.customer.name} · ${draft.customer.phone_masked}`
      : draft.customer.name;
  }, [draft.customer]);

  const slotLabel = draft.slot
    ? `${formatDayTitle(date)} · ${draft.slot.time}`
    : null;

  const missing = missingSteps(draft, rules);
  const ready = canReview(draft, rules);
  // A slot the schedule labelled but did not timestamp cannot be committed:
  // building the instant here would mean the client picking a timezone,
  // which §17 forbids. Rare, and better refused than guessed.
  const commitable = ready && !!draft.slot?.start_at;

  return (
    <div className="screen admin-flow-screen">
      <button
        type="button"
        className="admin-flow-back"
        onClick={() => navigate(returnTo.path)}
      >
        {returnTo.back}
      </button>
      <h1 className="screen__title">Новая запись</h1>

      {draft.window && (
        <div className="callout" style={{ marginBottom: "var(--s-3)" }}>
          {/* §12 fixes this label. It is the range the draft started from,
              never the length of the appointment. */}
          <div style={{ color: "var(--c-text-secondary)" }}>Выбранное окно</div>
          <div style={{ fontWeight: 600 }}>
            {formatDayTitle(windowDate || date)} · {draft.window.start_at}–
            {draft.window.end_at}
          </div>
        </div>
      )}

      <div className="draft-rows">
        <DraftRow
          label="Клиент"
          value={customerLabel}
          onOpen={() => setSheet("customer")}
        />
        <DraftRow
          label="Услуга"
          value={draft.service ? draft.service.name : null}
          onOpen={() => setSheet("service")}
        />
        {!isMaster && (
          <DraftRow
            label="Мастер"
            value={draft.master ? draft.master.name : null}
            onOpen={() => setSheet("master")}
          />
        )}
        <DraftRow
          label="Дата и время"
          value={slotLabel}
          onOpen={() => setSheet("time")}
        />
      </div>

      {notice && (
        <div
          className="callout callout--warning"
          role="status"
          aria-live="polite"
        >
          {notice}
        </div>
      )}

      {prefillHint && (
        <div className="callout" role="note" aria-live="polite">
          {prefillHint}
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
            {!isMaster && <li>Мастер: {draft.master?.name}</li>}
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
          <p
            style={{
              color: "var(--c-text-secondary)",
              margin: "0 0 var(--s-2)",
            }}
          >
            {`Осталось выбрать: ${missing.join(", ")}.`}
          </p>
        )}
        <button
          type="button"
          className="cta-bar__button"
          style={{ width: "100%" }}
          disabled={
            !commitable || submitting || (isMaster && outcome === "pending")
          }
          aria-disabled={
            !commitable || submitting || (isMaster && outcome === "pending")
          }
          onClick={() => void submit()}
        >
          {submitting ? "Создаю…" : "Создать запись"}
        </button>
        {ready && !commitable && (
          <p
            style={{
              color: "var(--c-text-secondary)",
              margin: "var(--s-2) 0 0",
            }}
          >
            У этого времени нет точной метки от расписания — выберите другое.
          </p>
        )}
      </div>

      {outcome !== null && isMaster && (
        // Мастер — словами SystemState (М-6, макет DRF-1181 п.10); тексты
        // «Это время занято» / «Проверяем результат» / «Проверить снова» —
        // из словаря, не свои. Черновик при conflict/pending цел.
        <section
          role="status"
          aria-live="polite"
          style={{ marginTop: "var(--s-3)" }}
        >
          {outcome === "conflict" && (
            <>
              <SystemState
                kind="conflict"
                onPickAnother={() => setSheet("time")}
              />
              {alternatives && alternatives.length > 0 && (
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: "var(--s-2) 0 0",
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "var(--s-2)",
                  }}
                  aria-label="Другие варианты времени"
                >
                  {alternatives.map((s) => (
                    <li key={s.time}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => {
                          dispatch({
                            type: "slot/set",
                            slot: { time: s.time, start_at: s.start_at },
                          });
                          setOutcome(null);
                          setAlternatives(null);
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
          {outcome === "pending" && (
            <SystemState
              kind="pending"
              body={PENDING_BODY}
              onRecheck={() => void submit()}
              busy={submitting}
            />
          )}
          {outcome === "committed" && (
            <div className="callout">
              <p style={{ margin: 0 }}>{SUBMIT_OUTCOME_COPY.committed}</p>
              {createdId && bookingHref && (
                <Link
                  to={bookingHref(createdId)}
                  className="btn-secondary"
                  style={{ display: "inline-block", marginTop: "var(--s-2)" }}
                >
                  Открыть запись
                </Link>
              )}
            </div>
          )}
          {(outcome === "blocked" || outcome === "failed") && (
            <div className="callout callout--warning">
              <p style={{ margin: 0 }}>{SUBMIT_OUTCOME_COPY[outcome]}</p>
              {outcomeDetail && (
                <p
                  style={{
                    margin: "var(--s-1) 0 0",
                    color: "var(--c-text-secondary)",
                  }}
                >
                  {outcomeDetail}
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {outcome !== null && !isMaster && (
        // §18 — four distinguishable outcomes. `pending` in particular must
        // not read as success: «Do not claim creation». It also must not
        // read as failure, or the receptionist presses again and the client
        // is booked twice.
        <section
          className={
            outcome === "committed" ? "callout" : "callout callout--warning"
          }
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
            <p
              style={{
                margin: "var(--s-1) 0 0",
                color: "var(--c-text-secondary)",
              }}
            >
              Введённые данные сохранены.
            </p>
          )}
          {outcome === "pending" && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "var(--s-2)" }}
              onClick={() => navigate(returnTo.path)}
            >
              {returnTo.open}
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
              placeholder={isMaster ? "Имя" : "Имя или телефон"}
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
              Поиск по клиентам пока недоступен. Это не значит, что клиента нет
              — заведите его как нового.
            </div>
          )}

          {searchState === "error" && (
            <div className="callout callout--warning" role="status">
              Не удалось выполнить поиск. Это не значит, что клиента нет.
            </div>
          )}

          {searchState === "phone_closed" && (
            // Мастер: поиск по номеру закрыт (DRF-1039) — сказать, как искать.
            <div className="callout" role="status">
              Ищите по имени.
            </div>
          )}

          {searchState === "done" && results.length === 0 && (
            <div className="callout" role="status">
              {isMaster
                ? "Совпадений нет. Возможно, клиент записан под другим именем."
                : "Совпадений нет. Возможно, клиент записан под другим именем или телефоном."}
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
                          // Мастер: маски телефона в черновике не бывает по построению.
                          phone_masked: isMaster ? undefined : c.phone_masked,
                        },
                      });
                      setSheet(null);
                    }}
                  >
                    {isMaster
                      ? `${c.name} · ${c.last_visit_date ? `была ${formatLastVisit(c.last_visit_date)}` : "новый клиент"}`
                      : c.phone_masked
                        ? `${c.name} · ${c.phone_masked}`
                        : c.name}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3 className="section__title">Новый клиент</h3>
          {isMaster && (
            // Макет DRF-1184, дословно. Телефон — вход для каталога; на экран
            // он не возвращается ни в одной форме (DRF-1039).
            <p
              style={{
                color: "var(--c-text-secondary)",
                margin: "0 0 var(--s-2)",
              }}
            >
              Имя и телефон нужны для создания записи и связи по ней.
            </p>
          )}
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
                customer: {
                  kind: "new",
                  name: newName.trim(),
                  phone: newPhone.trim(),
                },
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

      {sheet === "master" && !isMaster && (
        <Sheet title="Мастер" onClose={() => setSheet(null)}>
          {masters.length === 0 ? (
            <div className="callout" role="status">
              Список мастеров не загрузился. Откройте «Команда» и проверьте
              состав.
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {masters.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    className="sheet__item"
                    onClick={() => {
                      dispatch({
                        type: "master/set",
                        master: { id: m.id, name: m.name },
                      });
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
              {isMaster
                ? "Сначала выберите услугу — свободное время зависит от неё."
                : "Сначала выберите услугу и мастера — свободное время зависит от них."}
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

              {!slotsLoading &&
                slotsErr == null &&
                slots !== null &&
                slots.length === 0 && (
                  <div className="callout" role="status">
                    На этот день свободного времени нет. Посмотрите соседний
                    день.
                  </div>
                )}

              {!slotsLoading &&
                slotsErr == null &&
                slots !== null &&
                slots.length > 0 && (
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
                            if (isMaster && outcome === "conflict") {
                              // Новое время выбрано — «Это время занято» больше не про него.
                              setOutcome(null);
                              setAlternatives(null);
                            }
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
