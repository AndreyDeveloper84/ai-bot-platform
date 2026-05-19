/** F4 — My profile.
 *
 * Per docs/design/handoffs/2026-05-18-customer-first-time-handoff.md §12.
 * Header → identity card → favorites (read-only) → 4 preference toggles
 * → birthday + allergies → Save → privacy section with 2-step delete.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { StickyCta } from "../components/StickyCta";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import {
  deleteAccount,
  fetchProfile,
  type Preferences,
  type Profile,
  updateProfile,
} from "../lib/api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; profile: Profile; draft: Draft; dirty: boolean; saving: boolean }
  | { kind: "error"; err: unknown };

interface Draft {
  client_name: string;
  preferences: Preferences;
}

function toDraft(p: Profile): Draft {
  return {
    client_name: p.client_name,
    preferences: { ...p.preferences },
  };
}

export function ProfileScreen() {
  const navigate = useNavigate();
  const haptics = useHaptics();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [deleteOpen, setDeleteOpen] = useState(false);

  useBackButton({ onBack: () => navigate(-1) });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    fetchProfile()
      .then((profile) => {
        if (!cancelled)
          setState({ kind: "ok", profile, draft: toDraft(profile), dirty: false, saving: false });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  const setDraft = useCallback((updater: (d: Draft) => Draft) => {
    setState((s) => (s.kind === "ok" ? { ...s, draft: updater(s.draft), dirty: true } : s));
  }, []);

  const onSave = useCallback(async () => {
    if (state.kind !== "ok" || !state.dirty || state.saving) return;
    setState({ ...state, saving: true });
    haptics.selection();
    try {
      const updated = await updateProfile({
        client_name: state.draft.client_name,
        ...state.draft.preferences,
      });
      haptics.notify("success");
      setState({ kind: "ok", profile: updated, draft: toDraft(updated), dirty: false, saving: false });
    } catch (err) {
      haptics.notify("error");
      setState({ ...state, saving: false });
    }
  }, [state, haptics]);

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Профиль">
        <DelayedSkeleton loading>
          <div className="skeleton" style={{ height: 80, marginBottom: 16 }} />
          <div className="skeleton" style={{ height: 200 }} />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }
  if (state.kind === "error") {
    return (
      <ScreenLayout title="Профиль">
        <StateError err={state.err} onRetry={load} screenId="profile" />
      </ScreenLayout>
    );
  }

  const p = state.profile;
  const d = state.draft;

  return (
    <ScreenLayout
      title="Профиль"
      cta={
        state.dirty ? (
          <StickyCta onClick={onSave} disabled={state.saving}>
            {state.saving ? "Сохраняем…" : "Сохранить"}
          </StickyCta>
        ) : null
      }
    >
      <section className="profile-identity">
        <input
          className="profile-input"
          aria-label="Имя"
          value={d.client_name}
          placeholder={p.display_name || "Ваше имя"}
          onChange={(e) => setDraft((dr) => ({ ...dr, client_name: e.target.value }))}
        />
        {p.phone_masked ? (
          <div className="profile-phone">{p.phone_masked}</div>
        ) : (
          <div className="profile-phone profile-phone--missing">Телефон не указан</div>
        )}
      </section>

      {(p.favorites.master_name || p.favorites.service_name) && (
        <section className="profile-favorites">
          {p.favorites.master_name && (
            <div>
              <span className="profile-favorites__label">Любимый мастер:</span>{" "}
              {p.favorites.master_name}
            </div>
          )}
          {p.favorites.service_name && (
            <div>
              <span className="profile-favorites__label">Любимая услуга:</span>{" "}
              {p.favorites.service_name}
            </div>
          )}
        </section>
      )}

      <section className="profile-section">
        <h2 className="profile-section__title">Настройки помощника</h2>
        <Toggle
          label="Напоминания о визитах"
          checked={d.preferences.notify_reminders}
          onChange={(v) => setDraft((dr) => ({ ...dr, preferences: { ...dr.preferences, notify_reminders: v } }))}
        />
        <Toggle
          label="«Время обновить» — мягкие напоминания"
          checked={d.preferences.notify_retention}
          onChange={(v) => setDraft((dr) => ({ ...dr, preferences: { ...dr.preferences, notify_retention: v } }))}
        />
        <Toggle
          label="Промо-предложения"
          checked={d.preferences.notify_promo}
          onChange={(v) => setDraft((dr) => ({ ...dr, preferences: { ...dr.preferences, notify_promo: v } }))}
        />
        <Toggle
          label="Поздравления с днём рождения"
          checked={d.preferences.notify_birthday}
          onChange={(v) => setDraft((dr) => ({ ...dr, preferences: { ...dr.preferences, notify_birthday: v } }))}
        />
      </section>

      <section className="profile-section">
        <label className="profile-field">
          <span className="profile-field__label">День рождения</span>
          <input
            type="date"
            className="profile-input"
            value={d.preferences.birthday_date ?? ""}
            onChange={(e) =>
              setDraft((dr) => ({
                ...dr,
                preferences: { ...dr.preferences, birthday_date: e.target.value || null },
              }))
            }
          />
        </label>
        <label className="profile-field">
          <span className="profile-field__label">Аллергии и противопоказания</span>
          <textarea
            className="profile-input profile-input--multiline"
            rows={3}
            value={d.preferences.allergies}
            placeholder="Расскажите о важном, что нужно знать мастеру"
            onChange={(e) =>
              setDraft((dr) => ({
                ...dr,
                preferences: { ...dr.preferences, allergies: e.target.value },
              }))
            }
          />
          <span className="profile-field__hint">Передадим мастеру для вашей безопасности</span>
        </label>
      </section>

      <section className="profile-section profile-section--privacy">
        <h2 className="profile-section__title">Приватность</h2>
        <button
          type="button"
          className="btn-secondary btn-secondary--danger"
          onClick={() => setDeleteOpen(true)}
        >
          Удалить все мои данные
        </button>
      </section>

      {deleteOpen && (
        <DeleteConfirm
          onCancel={() => setDeleteOpen(false)}
          onConfirmed={() => {
            haptics.notify("success");
            navigate("/");
          }}
        />
      )}
    </ScreenLayout>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="profile-toggle">
      <span className="profile-toggle__label">{label}</span>
      <input
        type="checkbox"
        className="profile-toggle__input"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </label>
  );
}

function DeleteConfirm({
  onCancel,
  onConfirmed,
}: {
  onCancel: () => void;
  onConfirmed: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  const valid = typed === "УДАЛИТЬ";

  const confirm = async () => {
    if (!valid || submitting) return;
    setSubmitting(true);
    setErr(null);
    try {
      await deleteAccount();
      onConfirmed();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Не получилось удалить — попробуйте позже";
      setErr(msg);
      setSubmitting(false);
    }
  };

  // Esc → cancel; Tab/Shift+Tab → focus stays inside the card.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key === "Tab" && cardRef.current) {
        const focusables = cardRef.current.querySelectorAll<HTMLElement>(
          'input:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        const arr = Array.from(focusables);
        if (arr.length === 0) return;
        const first = arr[0]!;
        const last = arr[arr.length - 1]!;
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Restore focus to whatever opened the modal — usually the
      // "Удалить мои данные" button. Guard for unmount in mid-delete.
      previouslyFocused?.focus?.();
    };
  }, [onCancel]);

  return (
    <div
      className="modal-scrim"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-confirm-title"
      onClick={(e) => {
        // Click on the dim backdrop (not on the card itself) cancels.
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="modal-card" ref={cardRef}>
        <h2 className="modal-card__title" id="delete-confirm-title">
          Удалить все данные?
        </h2>
        <p className="modal-card__body">
          Мы сотрём ваше имя, телефон и предпочтения. История визитов останется у салона
          для отчётности, но без привязки к вам. Действие необратимо.
        </p>
        <p className="modal-card__body">
          Чтобы подтвердить, введите слово <strong>УДАЛИТЬ</strong>:
        </p>
        <input
          autoFocus
          className="profile-input"
          aria-label="Подтверждение удаления"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="УДАЛИТЬ"
        />
        {err && <div className="callout callout--danger">{err}</div>}
        <div className="modal-card__actions">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            Отмена
          </button>
          <button
            type="button"
            className="btn-danger"
            disabled={!valid || submitting}
            onClick={confirm}
          >
            {submitting ? "Удаляем…" : "Удалить"}
          </button>
        </div>
      </div>
    </div>
  );
}
