/**
 * Customer notification settings — real preference toggles (issue
 * #948 / P-8, replaces the deferred support-route sheet).
 *
 * Route: `/customer/notification-settings`
 *
 * Data: real `GET /me` + `PATCH /me` preference fields via
 * `lib/api.ts` (`fetchProfile` / `updateProfile`) — same endpoints the
 * master notification-settings screen and the profile read use.
 *
 * Semantics (per customer-profile spec §6.1 + Constitution Ст. XIV —
 * consent classes are independent):
 *   - notify_reminders — visit reminders (T−24h pilot scenario, R1);
 *   - notify_retention — «coming back» nudges when no recent booking;
 *   - notify_promo     — salon promos (opt-in, default off);
 *   - notify_birthday  — birthday greeting.
 * Transactional booking messages (confirmations, reschedules,
 * cancellations) always arrive — they are part of the booking itself,
 * not a preference, and the screen says so plainly.
 *
 * Save semantics mirror the profile marketing toggle: optimistic flip
 * + PATCH; failure reverts + honest snackbar.
 */

import { useCallback, useEffect, useState } from "react";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import { ToggleSwitch } from "../components/ToggleSwitch";
import { fetchProfile, updateProfile, type Preferences } from "../lib/api";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

type State =
  | { kind: "loading" }
  | { kind: "ok"; prefs: Preferences }
  | { kind: "error"; err: unknown };

interface ToggleDef {
  key: keyof Pick<
    Preferences,
    "notify_reminders" | "notify_retention" | "notify_promo" | "notify_birthday"
  >;
  label: string;
  hint: string;
}

const TOGGLES: ToggleDef[] = [
  {
    key: "notify_reminders",
    label: "Напоминания о записях",
    hint: "Перед визитом и если что-то изменится по записи.",
  },
  {
    key: "notify_retention",
    label: "Возвращение к заботе",
    hint: "Иногда напомню про регулярный уход, если давно не было записи.",
  },
  {
    key: "notify_promo",
    label: "Акции и предложения",
    hint: "Специальные предложения салонов. По умолчанию выключено.",
  },
  {
    key: "notify_birthday",
    label: "Поздравление с днём рождения",
    hint: "Одно тёплое сообщение раз в год.",
  },
];

export function CustomerNotificationSettingsScreen() {

  // Возврат (DRF-1493) — в профиль: настройки открывают только оттуда.
  const onBack = useScreenBack(backTo("/customer/profile"));
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const profile = await fetchProfile();
      setState({ kind: "ok", prefs: profile.preferences });
    } catch (err) {
      setState({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onToggle(def: ToggleDef, next: boolean) {
    if (busyKey) return;
    const key = def.key;
    setBusyKey(key);
    // Optimistic flip.
    setState((s) =>
      s.kind === "ok" ? { kind: "ok", prefs: { ...s.prefs, [key]: next } } : s,
    );
    try {
      const updated = await updateProfile({ [key]: next });
      setState({ kind: "ok", prefs: updated.preferences });
    } catch {
      // Revert on failure.
      setState((s) =>
        s.kind === "ok"
          ? { kind: "ok", prefs: { ...s.prefs, [key]: !next } }
          : s,
      );
      setToast("Не получилось сохранить. Попробуй ещё раз.");
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="profile-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
        >
          <span aria-hidden="true">←</span>
        </button>
        <h1 className="records-screen__title">Уведомления</h1>
      </header>

      <main className="profile-screen__main">
        {state.kind === "loading" && (
          <p className="profile-section__caption">Загружаю…</p>
        )}
        {state.kind === "error" && (
          <StateError err={state.err} onRetry={load} />
        )}
        {state.kind === "ok" && (
          <>
            <section className="profile-section" aria-labelledby="notif-toggles-h2">
              <h2 id="notif-toggles-h2" className="profile-section__heading">
                Что присылать в MAX
              </h2>
              <div className="profile-notifications__prefs">
                {TOGGLES.map((def) => (
                  <div key={def.key} className="profile-proactive__row">
                    <span className="profile-proactive__label">
                      {def.label}
                      <span
                        className="profile-section__caption"
                        style={{ display: "block" }}
                      >
                        {def.hint}
                      </span>
                    </span>
                    <ToggleSwitch
                      checked={Boolean(state.prefs[def.key])}
                      onChange={(next) => void onToggle(def, next)}
                      ariaLabel={def.label}
                      disabled={busyKey !== null}
                    />
                  </div>
                ))}
              </div>
            </section>

            <section className="profile-section" aria-labelledby="notif-transactional-h2">
              <h2
                id="notif-transactional-h2"
                className="profile-section__heading"
              >
                Всегда приходят
              </h2>
              <p className="profile-section__caption">
                Важные сообщения по записям (подтверждения, переносы,
                отмены) приходят всегда — это часть самой записи, а не
                рассылка.
              </p>
            </section>
          </>
        )}
      </main>

      <Snackbar
        visible={toast !== null}
        message={toast ?? ""}
        durationMs={4000}
        onTimeout={() => setToast(null)}
      />
    </div>
  );
}
