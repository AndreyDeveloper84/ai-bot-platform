/**
 * R3 «Что Ayla помнит» — список фактов с правом забыть (DRF-2133, Память-2).
 *
 * Заменяет `ComingSoonCard` в профиле клиента: сервер памяти есть
 * (`GET/DELETE /memory/`, `POST /memory/forget-all/`), а решение владельца
 * 19.09 (В2) — «до пилота».
 *
 * Что здесь есть и чего нет — по листу:
 *   • каждый факт — подпись чата + происхождение («ты сказал(а) 19.09» /
 *     «мы предположили»); предположение помечено, но не скрыто;
 *   • «Забыть» у каждого факта — без подтверждения (одна строка, обратимого
 *     ничего нет, повтор — новая реплика в чате);
 *   • «Забыть всё» внизу — с листом подтверждения: это те же три обязательства,
 *     что у «забудь всё» в чате (память, переписка обезличится, профиль у Ayla);
 *   • пустое состояние — приглашение сказать факт в чате;
 *   • `deletion_pending` — одна честная строка вместо списка;
 *   • никаких тумблеров «разрешить запоминать» (ADR-0011: согласие дано при входе).
 *
 * WCAG (как у соседей на экране): кнопки ≥44dp, список — `<ul>`, статус
 * загрузки/ошибки — `role="status"` / `role="alert"`, лист — `SheetChrome`
 * (фокус-ловушка, Esc, возврат фокуса на кнопку).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  factText,
  fetchMemory,
  forgetAll,
  forgetEntry,
  provenanceLabel,
  shortDate,
  type GreenFact,
  type HealthFact,
  type MemoryResponse,
} from "../lib/customer-memory";
import { SheetChrome } from "./PersonalDataSheets";

type View = "loading" | "ready" | "error";

export const MEMORY_EMPTY_TEXT =
  "Я пока ничего не запомнила. Скажи мне в чате, например: «я не ем молочное».";
export const MEMORY_PENDING_TEXT =
  "Ты попросил(а) забыть всё. Удалю в течение часа, переписка обезличится.";

export function MemoryCard() {
  const [view, setView] = useState<View>("loading");
  const [data, setData] = useState<MemoryResponse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const forgetAllRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setView("loading");
    setRowError(null);
    try {
      setData(await fetchMemory());
      setView("ready");
    } catch {
      setView("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const forget = useCallback(
    async (id: string) => {
      setBusyId(id);
      setRowError(null);
      try {
        await forgetEntry(id);
        setData((prev) =>
          prev
            ? {
                ...prev,
                green: prev.green.filter((f) => f.id !== id),
                health: prev.health.filter((f) => f.id !== id),
              }
            : prev,
        );
      } catch {
        setRowError("Не получилось забыть — попробуй ещё раз.");
      } finally {
        setBusyId(null);
      }
    },
    [],
  );

  const onForgotAll = useCallback(() => {
    setSheetOpen(false);
    setData((prev) =>
      prev ? { ...prev, green: [], health: [], status: "deletion_pending" } : prev,
    );
  }, []);

  if (view === "loading") {
    return (
      <div className="profile-memory" role="status" aria-live="polite">
        <p className="profile-memory__muted">Смотрю, что помню…</p>
      </div>
    );
  }

  if (view === "error" || !data) {
    return (
      <div className="profile-memory" role="alert">
        <p className="profile-memory__muted">Не получилось загрузить память.</p>
        <button type="button" className="btn-secondary profile-memory__retry" onClick={load}>
          Повторить
        </button>
      </div>
    );
  }

  if (data.status === "deletion_pending") {
    return (
      <div className="profile-memory" role="note">
        <p className="profile-memory__muted">{MEMORY_PENDING_TEXT}</p>
      </div>
    );
  }

  const isEmpty = data.green.length === 0 && data.health.length === 0;
  if (isEmpty) {
    return (
      <div className="profile-memory" role="note">
        <p className="profile-memory__muted">{MEMORY_EMPTY_TEXT}</p>
      </div>
    );
  }

  return (
    <div className="profile-memory">
      <ul className="profile-memory__list" aria-label="Что Ayla помнит">
        {data.green.map((fact) => (
          <MemoryRow
            key={fact.id}
            id={fact.id}
            text={factText(fact)}
            meta={provenanceLabel(fact)}
            inferred={fact.provenance === "inferred"}
            busy={busyId === fact.id}
            onForget={forget}
          />
        ))}
      </ul>

      {data.health.length > 0 && (
        <>
          <h3 className="profile-memory__subheading">Здоровье</h3>
          <ul className="profile-memory__list" aria-label="Здоровье">
            {data.health.map((fact: HealthFact) => (
              <MemoryRow
                key={fact.id}
                id={fact.id}
                text={fact.value ?? fact.kind}
                meta={`ты сказал(а) ${shortDate(fact.said_at)}`}
                inferred={false}
                busy={busyId === fact.id}
                onForget={forget}
              />
            ))}
          </ul>
        </>
      )}

      {rowError && (
        <p className="profile-memory__error" role="alert">
          {rowError}
        </p>
      )}

      <button
        ref={forgetAllRef}
        type="button"
        className="btn-secondary profile-memory__forget-all"
        onClick={() => setSheetOpen(true)}
      >
        Забыть всё
      </button>

      <ForgetAllSheet
        open={sheetOpen}
        triggerRef={forgetAllRef}
        onClose={() => setSheetOpen(false)}
        onDone={onForgotAll}
      />
    </div>
  );
}

interface RowProps {
  id: string;
  text: string;
  meta: string;
  inferred: boolean;
  busy: boolean;
  onForget: (id: string) => void;
}

function MemoryRow({ id, text, meta, inferred, busy, onForget }: RowProps) {
  return (
    <li className="profile-memory__row">
      <div className="profile-memory__body">
        <p className="profile-memory__text">{text}</p>
        <p className={`profile-memory__meta${inferred ? " profile-memory__meta--inferred" : ""}`}>
          {meta}
        </p>
      </div>
      <button
        type="button"
        className="btn-secondary profile-memory__forget"
        disabled={busy}
        aria-label={`Забыть: ${text}`}
        onClick={() => onForget(id)}
      >
        {busy ? "…" : "Забыть"}
      </button>
    </li>
  );
}

type SheetView = "confirm" | "busy" | "error";

interface ForgetAllSheetProps {
  open: boolean;
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
  onDone: () => void;
}

export function ForgetAllSheet({ open, triggerRef, onClose, onDone }: ForgetAllSheetProps) {
  const [view, setView] = useState<SheetView>("confirm");

  useEffect(() => {
    if (open) setView("confirm");
  }, [open]);

  const start = useCallback(async () => {
    setView("busy");
    try {
      await forgetAll();
      onDone();
    } catch {
      setView("error");
    }
  }, [onDone]);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="memory-forget-all-headline"
      headline="Забыть всё, что я о тебе помню?"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view !== "error" ? (
        <>
          <p className="profile-support-sheet__body">
            Удалю в течение часа всё, что запомнила из наших разговоров, и анкету
            предпочтений — вернуть будет нельзя. Переписка обезличится: текст
            останется без твоих контактов. Бронирования, оплаты и настройки
            уведомлений останутся.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              disabled={busy}
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              disabled={busy}
              onClick={start}
            >
              {busy ? "Забываю…" : "Забыть всё"}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="profile-support-sheet__body">
            Не получилось. Ничего не удалено — попробуй ещё раз или напиши мне в
            чате «забудь всё».
          </p>
          <div className="profile-support-sheet__actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Закрыть
            </button>
            <button type="button" className="btn-primary" onClick={start}>
              Повторить
            </button>
          </div>
        </>
      )}
    </SheetChrome>
  );
}

export type { GreenFact };
