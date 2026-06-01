/**
 * Support-entry sheet — Profile R2 deferred export + delete (Variant 3).
 *
 * Spec: `docs/screens/customer-profile-flow.md` §4.3 (export) + §4.4
 * (delete) + §0 «Pilot scope & backend reality» — both endpoints exist
 * but are NOT wired for pilot. Customer is routed to support; operator
 * runs manual dual-system delete / export per deployment runbook.
 *
 * # Why one component, two presets
 *
 * The two sheets share an identical shape: a calm headline, a calm
 * body paragraph that explicitly does NOT promise an in-app action,
 * and two buttons («Написать в поддержку» + «Отмена»). The copy
 * differs but the modal mechanics + focus management + a11y wiring
 * are identical, so they share one component with a `preset` prop.
 *
 * # Anti-patterns avoided per spec §14
 *   - NO 30-day grace promise (founder-locked, remains forbidden)
 *   - NO full-deletion-one-tap promise (two systems unconnected — §0)
 *   - NO timeframe promise («через 24 часа» etc.)
 *   - NO «ВНИМАНИЕ! Это действие необратимо!» fear-mongering
 *   - NO auto-focus on the destructive CTA (WCAG 2.5.5 + spec §14)
 *
 * # WCAG 2.2 AA
 *   - `role="dialog"` + `aria-modal="true"` + `aria-labelledby`
 *   - Focus trap (Tab cycles within sheet; Shift+Tab reverses)
 *   - Escape closes
 *   - Backdrop click closes (no destructive side effects, so safe)
 *   - Restore focus to opener on close
 *   - Primary action («Написать в поддержку») NOT auto-focused per
 *     spec §13.5 + §14 — initial focus lands on the Cancel button
 */

import { useCallback, useEffect, useRef } from "react";

import { SUPPORT_DEEPLINK } from "../lib/customer-profile";

export type SupportPreset = "export" | "delete";

interface CopyBlock {
  headlineId: string;
  headline: string;
  body: string[];
  primaryLabel: string;
}

const COPY: Record<SupportPreset, CopyBlock> = {
  export: {
    headlineId: "profile-support-export-headline",
    headline: "Нужна копия твоих данных?",
    body: [
      "Я не делаю выгрузку прямо здесь — пока это запрос через поддержку. Напиши нам, и оператор подготовит твои данные по закону (152-ФЗ, право на доступ).",
    ],
    primaryLabel: "Написать в поддержку",
  },
  delete: {
    headlineId: "profile-support-delete-headline",
    headline: "Хочешь удалить аккаунт?",
    body: [
      "Удаление пока оформляется через поддержку — так я уверена, что уберу данные во всех системах правильно. Напиши нам, и оператор всё сделает по закону.",
      "Часть данных по записям и оплатам может храниться дольше, если это требует закон.",
    ],
    primaryLabel: "Написать в поддержку",
  },
};

interface Props {
  /** Visible preset; pass `null` to hide the sheet. */
  preset: SupportPreset | null;
  /** Caller's open trigger element — focus restores here on close. */
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
}

export function SupportEntrySheet({ preset, triggerRef, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const primaryRef = useRef<HTMLAnchorElement | null>(null);

  const close = useCallback(() => {
    onClose();
    // Restore focus to the opener for WCAG 2.4.3 focus order.
    if (triggerRef.current) {
      try {
        triggerRef.current.focus();
      } catch {
        /* opener may have unmounted */
      }
    }
  }, [onClose, triggerRef]);

  // Initial focus + Escape + Tab trap.
  useEffect(() => {
    if (preset === null) return;
    // Initial focus on Cancel — explicit per spec §13.5 (NOT primary).
    cancelRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables: HTMLElement[] = [];
      if (cancelRef.current) focusables.push(cancelRef.current);
      if (primaryRef.current) focusables.push(primaryRef.current);
      if (focusables.length === 0) return;
      const active = document.activeElement as HTMLElement | null;
      const idx = active ? focusables.indexOf(active) : -1;
      if (e.shiftKey) {
        if (idx <= 0) {
          e.preventDefault();
          const last = focusables[focusables.length - 1];
          if (last) last.focus();
        }
      } else if (idx === focusables.length - 1) {
        e.preventDefault();
        const first = focusables[0];
        if (first) first.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [preset, close]);

  if (preset === null) return null;
  const copy = COPY[preset];

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
        aria-labelledby={copy.headlineId}
      >
        <h2 id={copy.headlineId} className="profile-support-sheet__headline">
          {copy.headline}
        </h2>
        {copy.body.map((paragraph, idx) => (
          <p key={`body-${idx}`} className="profile-support-sheet__body">
            {paragraph}
          </p>
        ))}
        <div className="profile-support-sheet__actions">
          <button
            ref={cancelRef}
            type="button"
            className="btn-secondary profile-support-sheet__cancel"
            onClick={close}
          >
            Отмена
          </button>
          <a
            ref={primaryRef}
            href={SUPPORT_DEEPLINK}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-primary profile-support-sheet__primary"
            onClick={() => {
              // After deeplink open, close the sheet.
              window.setTimeout(close, 0);
            }}
          >
            {copy.primaryLabel}
          </a>
        </div>
      </div>
    </div>
  );
}
