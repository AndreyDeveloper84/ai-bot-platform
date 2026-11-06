import { useCallback, useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import {
  DestructiveConfirmation,
  SystemState,
} from "../../components/OperationalSystemState";
import { useScreenBack } from "../../hooks/useScreenBack";
import {
  cancelSalonBooking,
  getBookingSlots,
  getAdminBookingDetail,
  rescheduleSalonBooking,
  type BookingSlot,
  type AdminBookingDetail,
} from "../../lib/admin-api";
import { backTo } from "../../lib/screen-back";
import { signalReady } from "../../lib/max-sdk";
import { parseSalonWallClock } from "../../lib/format";

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: AdminBookingDetail }
  | { kind: "error"; err: unknown };

type Mode = "idle" | "cancel" | "reschedule" | "pending" | "conflict";

function safeBack(state: unknown): string {
  if (state && typeof state === "object" && "from" in state) {
    const from = (state as { from?: unknown }).from;
    if (typeof from === "string" && (from === "/admin/today" || from === "/admin/schedule")) {
      return from;
    }
  }
  return "/admin/schedule";
}

function wireDate(iso: string): string {
  return parseSalonWallClock(iso)?.ymd ?? iso;
}

function wireTime(iso: string): string {
  return parseSalonWallClock(iso)?.hm ?? iso;
}

function clientName(data: AdminBookingDetail): string {
  const first = data.client_first_name.trim();
  const initial = data.client_last_initial.trim();
  return [first || "Клиент", initial].filter(Boolean).join(" ");
}

export function AdminBookingDetailScreen() {
  const { id = "" } = useParams<{ id: string }>();
  const location = useLocation();
  useScreenBack(backTo(safeBack(location.state)));

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const data = await getAdminBookingDetail(id);
      setPhase({ kind: "ready", data });
    } catch (err) {
      setPhase({ kind: "error", err });
    } finally {
      setBusy(false);
    }
  }, [id]);

  useEffect(() => {
    signalReady();
    setPhase({ kind: "loading" });
    void load();
  }, [load]);

  return (
    <div className="screen">
      <h1 className="screen__title">Запись</h1>
      <div className="screen__body">
        {phase.kind === "loading" ? (
          <SystemState kind="loading" />
        ) : phase.kind === "error" ? (
          <SystemState
            kind="load_error"
            what="booking"
            err={phase.err}
            busy={busy}
            onRetry={() => void load()}
          />
        ) : (
          <BookingDetail data={phase.data} onReadback={load} />
        )}
      </div>
    </div>
  );
}

function BookingDetail({
  data,
  onReadback,
}: {
  data: AdminBookingDetail;
  onReadback: () => Promise<void>;
}) {
  const [mode, setMode] = useState<Mode>("idle");
  const [mutating, setMutating] = useState(false);
  const [message, setMessage] = useState("");
  const [date, setDate] = useState(wireDate(data.start_datetime));
  const [slots, setSlots] = useState<BookingSlot[]>([]);
  const [slotsBusy, setSlotsBusy] = useState(false);
  const [slotsError, setSlotsError] = useState<unknown>(null);
  const [selectedStart, setSelectedStart] = useState<string | null>(null);

  useEffect(() => {
    setDate(wireDate(data.start_datetime));
    if (mode === "pending" || mode === "conflict") {
      setMode("idle");
    }
  }, [data.id, data.version, data.start_datetime, mode]);

  const cancelled = data.status === "cancelled" || data.status === "no_show";
  const actionable = !cancelled && data.status !== "completed";
  const canReschedule = actionable && Boolean(data.master_id && data.service_id);

  const cancel = async () => {
    if (mutating) return;
    setMutating(true);
    setMessage("");
    try {
      const result = await cancelSalonBooking(data.id);
      if (result.outcome === "committed") {
        await onReadback();
        setMode("idle");
      } else if (result.outcome === "pending") {
        setMode("pending");
      } else if (result.outcome === "conflict") {
        setMode("conflict");
        await onReadback();
      } else {
        setMessage("Отменить запись сейчас нельзя.");
        setMode("idle");
      }
    } catch {
      setMessage("Не удалось отменить запись. Обновите данные.");
      setMode("idle");
    } finally {
      setMutating(false);
    }
  };

  const loadSlots = async () => {
    if (!data.master_id || !data.service_id) return;
    setSlotsBusy(true);
    setSlotsError(null);
    setSelectedStart(null);
    try {
      const result = await getBookingSlots({
        masterId: data.master_id,
        serviceId: data.service_id,
        date,
      });
      setSlots(result.slots);
    } catch (err) {
      setSlots([]);
      setSlotsError(err);
    } finally {
      setSlotsBusy(false);
    }
  };

  const reschedule = async () => {
    if (!selectedStart || mutating) return;
    setMutating(true);
    setMessage("");
    try {
      const result = await rescheduleSalonBooking(data.id, data.version, selectedStart);
      if (result.outcome === "committed") {
        await onReadback();
        setMode("idle");
      } else if (result.outcome === "pending") {
        setMode("pending");
      } else if (result.outcome === "conflict") {
        setMode("conflict");
        await onReadback();
      } else {
        setMessage("Перенос сейчас недоступен.");
        setMode("idle");
      }
    } catch {
      setMessage("Не удалось перенести запись. Обновите данные.");
      setMode("idle");
    } finally {
      setMutating(false);
    }
  };

  return (
    <main className="booking-detail__main" aria-labelledby="salon-booking-client">
      <header className="booking-detail__client">
        <h2 className="booking-detail__name" id="salon-booking-client">
          {clientName(data)}
        </h2>
      </header>

      <ul className="booking-detail__facts">
        <Fact title={data.service_name || "Услуга"} />
        <Fact title={data.master_name || "Мастер не определён"} />
        <Fact title={wireDate(data.start_datetime)} />
        <Fact
          title={`${wireTime(data.start_datetime)}–${wireTime(data.end_datetime)}`}
          sub={data.duration_min > 0 ? `${data.duration_min} мин` : undefined}
        />
      </ul>

      {data.is_in_progress && !cancelled ? (
        <section className="booking-detail__state booking-detail__state--accent" role="status">
          <p className="booking-detail__state-title">Сейчас по расписанию</p>
        </section>
      ) : null}

      {message ? <p role="alert">{message}</p> : null}

      {mode === "pending" ? (
        <SystemState
          kind="pending"
          busy={mutating}
          body="Не удалось подтвердить результат действия. Сначала проверьте актуальное состояние записи."
          onRecheck={() => void onReadback()}
        />
      ) : mode === "conflict" ? (
        <SystemState kind="conflict" onPickAnother={() => setMode("reschedule")} />
      ) : mode === "cancel" ? (
        <DestructiveConfirmation
          title="Отменить запись?"
          body="Вы уверены, что хотите отменить запись клиента?"
          confirmLabel="Отменить запись"
          busy={mutating}
          onConfirm={() => void cancel()}
          onCancel={() => setMode("idle")}
        />
      ) : mode === "reschedule" ? (
        <section className="booking-detail__actions booking-detail__reschedule" aria-label="Перенос записи">
          <h2>Перенести запись</h2>
          <label>
            Дата
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <button
            type="button"
            className="btn-secondary"
            disabled={slotsBusy || !canReschedule}
            onClick={() => void loadSlots()}
          >
            {slotsBusy ? "Загружаем…" : "Показать свободное время"}
          </button>
          {slotsError ? (
            <SystemState
              kind="load_error"
              what="schedule"
              err={slotsError}
              busy={slotsBusy}
              onRetry={() => void loadSlots()}
            />
          ) : null}
          {slots.length > 0 ? (
            <div className="booking-detail__slot-list" aria-label="Свободное время">
              {slots.filter((slot) => slot.start_at).map((slot) => (
                <button
                  type="button"
                  key={slot.start_at ?? slot.time}
                  className={selectedStart === slot.start_at ? "btn-primary" : "btn-secondary"}
                  onClick={() => setSelectedStart(slot.start_at)}
                >
                  {slot.time}
                </button>
              ))}
            </div>
          ) : null}
          {selectedStart ? (
            <div className="booking-detail__reschedule-confirm">
              <p>Перенести запись на {wireTime(selectedStart)}?</p>
              <button
                type="button"
                className="btn-primary"
                disabled={mutating}
                onClick={() => void reschedule()}
              >
                Подтвердить перенос
              </button>
            </div>
          ) : null}
          <button type="button" className="btn-secondary" disabled={mutating} onClick={() => setMode("idle")}>
            Назад
          </button>
        </section>
      ) : actionable ? (
        <section className="booking-detail__actions" aria-label="Действия с записью">
          {canReschedule ? (
            <button type="button" className="btn-primary" onClick={() => setMode("reschedule")}>
              Перенести
            </button>
          ) : null}
          <button type="button" className="btn-secondary" onClick={() => setMode("cancel")}>
            Отменить
          </button>
        </section>
      ) : null}
    </main>
  );
}

function Fact({ title, sub }: { title: string; sub?: string }) {
  return (
    <li className="booking-detail__fact">
      <span className="booking-detail__fact-text">
        <span className="booking-detail__fact-title">{title}</span>
        {sub ? <span className="booking-detail__fact-sub">{sub}</span> : null}
      </span>
    </li>
  );
}
