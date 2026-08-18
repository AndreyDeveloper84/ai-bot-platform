/**
 * Master M8 «Настройки» — minimal logout-only.
 *
 * Route: /master/settings
 *
 * Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M8
 * (lines 846-897). This PR ships the LOGOUT portion only — full M8
 * layout (theme / font / help / about / version) is deferred to a
 * post-pilot polish FOLLOW_UP issue per TL plan Step 2.
 *
 * Spec quote (§M8 line 885, verbatim):
 *
 *     «[Выйти из аккаунта]                │  Sheet confirm; clears
 *      master_token»
 *
 * Spec quote (§M8 line 846 + 848, verbatim):
 *
 *     «Screen M8 — App settings … Route: /settings»
 *
 * IN scope (this PR):
 *   - Single destructive «Выйти из аккаунта» button
 *   - Confirm sheet with [Выйти] [Отмена]
 *   - On confirm: removeDeviceStorage(MASTER_SESSION_STORAGE_KEY) +
 *     window.location.replace('/') so the React tree drops and the
 *     onboarding gate re-runs at root
 *   - BackButton → /master/profile
 *   - Hint paragraph teasing the deferred theme/font polish
 *
 * OUT of scope (FOLLOW_UP issue — post-pilot polish):
 *   - Theme selector
 *   - Font-size selector
 *   - Help / support links
 *   - About (version, terms, privacy)
 *
 * Bridge API:
 *   - BackButton.show() → /master/profile
 *   - hapticNotify('warning') on confirm tap
 *   - No enableClosingConfirmation — nothing to lose on this screen
 *
 * Error path: removeDeviceStorage is fire-and-forget (it already
 * swallows DeviceStorage errors internally). We still call
 * window.location.replace('/') unconditionally so a misbehaving
 * bridge never strands the user on this screen with no exit.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SurfaceSwitchButton } from "../components/SurfaceSwitch";
import { MASTER_SESSION_STORAGE_KEY } from "../lib/master-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  removeDeviceStorage,
  setBackButton,
} from "../lib/max-sdk";

// --- Russian copy (VERBATIM from §M8) -----------------------------------

const COPY = {
  header: "Настройки",
  comingSoon:
    "Скоро здесь появятся настройки темы и размера шрифта.",
  logoutButton: "Выйти из аккаунта",
  confirm: {
    title: "Выйти из аккаунта?",
    body:
      "Чтобы вернуться, потребуется новое приглашение от админа.",
    confirm: "Выйти",
    cancel: "Отмена",
  },
};

// --- Component ----------------------------------------------------------

export function MasterSettingsScreen() {
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);

  // --- Bridge: BackButton wiring ---
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/master/profile");
    });
    return () => {
      off();
      setBackButton(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openConfirm = useCallback(() => {
    hapticSelection();
    setConfirmOpen(true);
  }, []);

  const dismissConfirm = useCallback(() => {
    setConfirmOpen(false);
  }, []);

  const performLogout = useCallback(() => {
    // Haptic «warning» — destructive irreversible-from-here action.
    hapticNotify("warning");
    // Best-effort token clear. removeDeviceStorage swallows bridge
    // errors itself; we don't want to gate the page reload on it
    // succeeding so the user is never stuck on this screen.
    try {
      removeDeviceStorage(MASTER_SESSION_STORAGE_KEY);
    } catch (err) {
      // Should never throw (helper catches internally) — log + carry
      // on so the redirect still fires.
      console.warn("[m8] removeDeviceStorage threw unexpectedly", err);
    }
    // Full page reload to root so in-memory React state is dropped
    // + the master-onboarding gate re-runs (it reads the token on
    // mount and reroutes to claim/accept when missing).
    if (typeof window !== "undefined") {
      window.location.replace("/");
    }
  }, []);

  return (
    <div className="screen master-settings">
      <h1 className="screen__title">{COPY.header}</h1>

      <p
        className="master-settings__coming-soon"
        style={{
          color: "var(--c-text-secondary)",
          margin: "var(--s-3) 0",
        }}
      >
        {COPY.comingSoon}
      </p>

      {/* Phase 2b — billing / payout surface (C2/C3, real proxies). */}
      <button
        type="button"
        className="btn-secondary"
        style={{ width: "100%", justifyContent: "center", marginBottom: "var(--s-3)" }}
        onClick={() => navigate("/master/billing")}
      >
        Оплата и выплаты
      </button>

      {/*
        Above the divider, with the ordinary actions: switching surface
        is routine for a dual-role person, not a destructive step. It
        renders nothing at all for someone holding a single role.
      */}
      <SurfaceSwitchButton />

      <hr
        style={{
          border: 0,
          borderTop: "1px solid var(--c-border, rgba(0,0,0,0.08))",
          margin: "var(--s-4) 0",
        }}
      />

      <button
        type="button"
        className="btn-secondary"
        onClick={openConfirm}
        style={{
          color: "var(--c-danger, #d33)",
          width: "100%",
          justifyContent: "center",
        }}
      >
        {COPY.logoutButton}
      </button>

      {confirmOpen ? (
        <LogoutConfirmSheet
          onConfirm={performLogout}
          onCancel={dismissConfirm}
        />
      ) : null}
    </div>
  );
}

// --- Sub-components -----------------------------------------------------

function LogoutConfirmSheet({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="admin-sheet-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={COPY.confirm.title}
      onClick={onCancel}
    >
      <div className="admin-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="admin-sheet__title" style={{ textAlign: "start" }}>
          {COPY.confirm.title}
        </div>
        <p style={{ margin: "var(--s-3) 0", color: "var(--c-text-secondary)" }}>
          {COPY.confirm.body}
        </p>
        <div
          style={{
            display: "flex",
            gap: "var(--s-2)",
            marginTop: "var(--s-3)",
          }}
        >
          <button
            type="button"
            className="btn-secondary"
            style={{ flex: 1 }}
            onClick={onCancel}
          >
            {COPY.confirm.cancel}
          </button>
          <button
            type="button"
            className="cta-bar__button"
            style={{ flex: 1, color: "var(--c-danger, #d33)" }}
            onClick={onConfirm}
          >
            {COPY.confirm.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
