/**
 * C5 personal-data sheets (152-ФЗ, pilot 2026-08-15) — export + delete.
 *
 * Frozen contract: `PILOT_CONTRACTS_2026-08-15` §6. Replaces the deferred
 * Variant-3 support route (SupportEntrySheet) for the profile «Запросить
 * данные» / «Удалить аккаунт» CTAs now that W3 ships the aggregating
 * endpoints (`apps/miniapp_api`):
 *
 *   GET    /api/v1/customer/me/personal-data/export/  → JSON attachment
 *   DELETE /api/v1/customer/me/personal-data/         → {status:"deleted"}
 *
 * # Contract obligations implemented here
 *
 * - **UI idempotency** — while a request is in flight both actions are
 *   disabled and Escape/backdrop are ignored, so repeat taps never spawn
 *   repeat requests. Backend repeats stay safe regardless (C5.2).
 * - **Honest partial** — a 502 `{status:"partial", failed_steps}` maps to
 *   humanised step labels (raw backend slugs never render) + retry +
 *   support deeplink (#949 fallback on every failure view).
 * - **Retention boundary** — delete copy states what the pilot cascade
 *   covers (memory, personal context, consents) and that bookings /
 *   payments may be retained per law. No timeframe promises, no 30-day
 *   grace wording (founder-locked anti-patterns, spec §14).
 *
 * # A11y (mirrors SupportEntrySheet, WCAG 2.2 AA)
 *
 * role=dialog + aria-modal + aria-labelledby; initial focus on Cancel —
 * never on the primary/destructive CTA; Tab focus trap across the
 * currently-enabled buttons/links; Escape closes (except mid-request);
 * backdrop click closes (same guard); focus restores to the opener.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import {
  DELETE_CONFIRMATION_TOKEN,
  deletePersonalData,
  exportPersonalData,
  PersonalDataPartialDeleteError,
  triggerDownload,
} from "../lib/personal-data";
import { useSheetKeyNav } from "../hooks/useSheetKeyNav";

// ---------------------------------------------------------------------------
// Shared sheet chrome (internal — the two sheets below are the public API).
// ---------------------------------------------------------------------------

interface SheetChromeProps {
  headlineId: string;
  headline: string;
  /** True while a request is in flight: blocks Escape/backdrop/close. */
  closeDisabled: boolean;
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
  children: React.ReactNode;
}

function SheetChrome({
  headlineId,
  headline,
  closeDisabled,
  triggerRef,
  onClose,
  children,
}: SheetChromeProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => {
    if (closeDisabled) return;
    onClose();
    if (triggerRef.current) {
      try {
        triggerRef.current.focus();
      } catch {
        /* opener may have unmounted */
      }
    }
  }, [closeDisabled, onClose, triggerRef]);

  // Initial focus lands on the element marked data-initial-focus (the
  // Cancel button) — WCAG 2.5.5 / spec §13.5: the primary CTA is never
  // auto-focused. Runs once per mount; per-view re-render keeps focus.
  useEffect(() => {
    const dialog = dialogRef.current;
    const initial = dialog?.querySelector<HTMLElement>("[data-initial-focus]");
    (initial ?? dialog?.querySelector("button"))?.focus();
  }, []);

  // Escape + Tab trap — shared hook (#953).
  useSheetKeyNav(dialogRef, { onClose: close, closeDisabled });

  return (
    <div
      className="profile-support-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        className="profile-support-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headlineId}
      >
        <h2 id={headlineId} className="profile-support-sheet__headline">
          {headline}
        </h2>
        {children}
      </div>
    </div>
  );
}

interface SheetProps {
  /** Render the sheet when true; parent holds visibility state. */
  open: boolean;
  /** Opener element — focus restores here on close. */
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
}

function SupportLink() {
  return (
    <a
      href={SUPPORT_DEEPLINK}
      target="_blank"
      rel="noopener noreferrer"
      className="btn-secondary"
    >
      Написать в поддержку
    </a>
  );
}

// ---------------------------------------------------------------------------
// C5.1 — Export
// ---------------------------------------------------------------------------

type ExportView = "confirm" | "busy" | "done" | "error";

export function PersonalDataExportSheet({ open, triggerRef, onClose }: SheetProps) {
  const [view, setView] = useState<ExportView>("confirm");

  // Reset to the confirm view each time the sheet is (re)opened.
  useEffect(() => {
    if (open) setView("confirm");
  }, [open]);

  const start = useCallback(async () => {
    setView((v) => (v === "busy" ? v : "busy"));
    try {
      const file = await exportPersonalData();
      triggerDownload(file.blob, file.filename);
      setView("done");
    } catch {
      setView("error");
    }
  }, []);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="personal-data-export-headline"
      headline="Скачать мои данные"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          <p className="profile-support-sheet__body">
            Соберу в один файл (JSON): твой профиль, что{" "}
            <span lang="en">Ayla</span> помнит, и твои согласия. Файл
            скачается на устройство — дальше он только в твоих руках.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Скачать данные
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Готовлю файл…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Отмена
            </button>
            <button type="button" disabled className="btn-primary">
              Готовлю файл…
            </button>
          </div>
        </>
      )}
      {view === "done" && (
        <>
          <p className="profile-support-sheet__body">
            Файл скачан. Если не видишь его в загрузках — напиши в
            поддержку, поможем.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось подготовить файл. Попробуй ещё раз — если снова
            не выйдет, напиши в поддержку, мы подготовим данные вручную.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}

// ---------------------------------------------------------------------------
// C5.2 — Delete
// ---------------------------------------------------------------------------

type DeleteView = "confirm" | "busy" | "done" | "partial" | "error";

/** Backend cascade slugs → human copy (raw slugs never render). */
const FAILED_STEP_LABELS: Record<string, string> = {
  ayla_delete: "удалить данные в основной системе",
  memory_delete: "очистить память",
  consent_withdraw: "отозвать согласия",
  profile_pii_erase: "очистить контакты и имя в профиле",
};

function humanizeFailedSteps(steps: string[]): string {
  return steps
    .map((s) => FAILED_STEP_LABELS[s] ?? "завершить один из шагов")
    .join(", ");
}

export function PersonalDataDeleteSheet({ open, triggerRef, onClose }: SheetProps) {
  const [view, setView] = useState<DeleteView>("confirm");
  const [failedSteps, setFailedSteps] = useState<string[]>([]);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (open) {
      setView("confirm");
      setFailedSteps([]);
      setTyped("");
    }
  }, [open]);

  // The server verifies this token too (400 confirmation_mismatch), so the
  // input is real evidence of intent, not decoration.
  const confirmed = typed.trim() === DELETE_CONFIRMATION_TOKEN;

  const start = useCallback(async () => {
    if (!confirmed) return;
    setView("busy");
    try {
      await deletePersonalData(typed.trim());
      setView("done");
    } catch (err) {
      if (err instanceof PersonalDataPartialDeleteError) {
        setFailedSteps(err.failedSteps);
        setView("partial");
      } else {
        setView("error");
      }
    }
  }, [confirmed, typed]);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="personal-data-delete-headline"
      headline="Удалить мои данные?"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          <p className="profile-support-sheet__body">
            Удалю во всех наших системах: что <span lang="en">Ayla</span>{" "}
            помнит о тебе, твои персональные настройки и согласия. Это
            действие нельзя отменить.
          </p>
          <p className="profile-support-sheet__body">
            Записи и оплаты могут храниться дольше, если этого требует
            закон.
          </p>
          <label
            className="profile-support-sheet__body"
            htmlFor="personal-data-delete-confirm"
          >
            Чтобы подтвердить, введи{" "}
            <b>{DELETE_CONFIRMATION_TOKEN}</b>:
          </label>
          <input
            id="personal-data-delete-confirm"
            type="text"
            className="profile-support-sheet__input"
            value={typed}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            aria-describedby="personal-data-delete-headline"
            onChange={(e) => setTyped(e.target.value)}
          />
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              disabled={!confirmed}
              onClick={start}
            >
              Удалить данные
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Удаляю…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Отмена
            </button>
            <button type="button" disabled className="btn-primary">
              Удаляю…
            </button>
          </div>
        </>
      )}
      {view === "done" && (
        <>
          <p className="profile-support-sheet__body">
            Данные удалены. <span lang="en">Ayla</span> больше не
            использует твою память, настройки и согласия.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "partial" && (
        <>
          <p className="profile-support-sheet__body">
            Не всё удалено. Не получилось {humanizeFailedSteps(failedSteps)}.
            Повторное удаление безопасно — попробуй ещё раз, а если снова
            не выйдет, напиши в поддержку.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось удалить данные. Попробуй ещё раз — если снова
            не выйдет, напиши в поддержку, мы удалим вручную.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}
