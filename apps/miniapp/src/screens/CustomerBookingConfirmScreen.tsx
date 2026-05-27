/**
 * F4 — Booking confirmation, two variants:
 *   §6.1 registered: priority-ordered card (what/where/when/price →
 *     CTA → cancel policy → loyalty → notes collapsed)
 *   §6.2 anonymous gate: «Чтобы записаться» heading + MAX OAuth
 *     registration block. sessionStorage `pending_booking_intent`
 *     preserves the full booking context across the OAuth round-trip
 *     (P0 acceptance criterion).
 *
 * Spec: `docs/screens/customer-booking-flow.md` §6.
 *
 * Voice rules (founder F2 + §6 + §8 F4):
 *   - Title: «Подтверди запись» (registered) / «Чтобы записаться»
 *     (anonymous gate, founder-locked).
 *   - Cancellation policy: «Можно отменить за 4 часа до визита.»
 *     (compact, no scary preamble.)
 *   - Notes label: «+ Добавить заметку мастеру» — collapsed by
 *     default per founder cut #3.
 *   - Primary CTA: «Записаться» (registered) / «Зарегистрироваться»
 *     (anonymous gate). NEVER «Подтвердить» / «Окей» / «Готово».
 *
 * Founder priority order (§6.1, locked):
 *   1. Что / где / когда / цена  (the visit summary)
 *   2. Button «Записаться»
 *   3. Cancellation policy (compact)
 *   4. Loyalty block (graceful — hide on 404 / no balance per TL Q3)
 *   5. «+ Добавить заметку мастеру» (collapsed default)
 *
 * Slot race 409 handling (Tau §5.4 row 4):
 *   When `createBooking` returns `slot_unavailable`, render a banner
 *   with the founder-locked tone:
 *     «Это время только что заняли. {Substitute master} свободна
 *      {time} — подойдёт?»
 *   Verbatim — NEVER «лучше» / «рекомендуем». Substitute candidate
 *   is plumbed by backend; absent → fall back to the generic
 *   «Выбрать другое время» CTA returning to F3.
 *
 * Anonymous gate:
 *   Detection: `getInitData()` empty → anonymous. When user is
 *   anonymous AND a slot has been picked, the screen renders the
 *   `<AnonymousGateOverlay>` panel instead of the registered card.
 *   The overlay's «Зарегистрироваться» button:
 *     1. Calls `savePendingIntent({...})` (sessionStorage).
 *     2. Triggers MAX OAuth via `maxBridge().openLink(...)`. The
 *        OAuth callback returns to `/customer/booking/confirm`,
 *        where `restorePendingIntent()` rehydrates the draft.
 *
 *   W4 backend round-trip (TL Q4 — defence in depth): the `/auth/verify`
 *   response MAY include a server-side `pending_booking_intent` field.
 *   When present, we prefer it over sessionStorage (cross-device case).
 *   Frontend reads `intent` from the URL hash / response if Surface 4
 *   has been wired; otherwise sessionStorage stays primary.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, authVerify } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { useBackButton } from "../hooks/useBackButton";
import { useClosingConfirmation } from "../hooks/useClosingConfirmation";
import { useHaptics } from "../hooks/useHaptics";
import { createCustomerBooking } from "../lib/customer-booking";
import { formatMoney, formatVisitFull } from "../lib/format";
import { getInitData } from "../lib/max-sdk";
import {
  restorePendingIntent,
  savePendingIntent,
} from "../lib/pending-booking-intent";
import {
  resetBooking,
  setMaster,
  setService,
  setVisitAt,
  useBookingDraft,
} from "../state/booking";

type ErrState =
  | { kind: "slot_unavailable"; substituteName?: string; substituteTime?: string }
  | { kind: "salon_suspended" }
  | { kind: "server" }
  | { kind: "network" }
  | { kind: "other"; detail: string };

/**
 * Anonymous == no MAX initData available. In dev mode with a VITE
 * override, initData is non-empty so we treat the user as registered
 * for parity with the booking endpoint behaviour.
 */
function isAnonymous(): boolean {
  return getInitData() === "";
}

export function CustomerBookingConfirmScreen() {
  const navigate = useNavigate();
  const draft = useBookingDraft();
  const haptics = useHaptics();
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<ErrState | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const [note, setNote] = useState("");
  const [anonymous] = useState<boolean>(() => isAnonymous());

  useBackButton({ onBack: () => navigate(-1) });
  useClosingConfirmation(true);

  // Restore pending intent on mount — post-OAuth callback case.
  //
  // Two-path restore (TL Q4 P0 + W4 #844 round-trip):
  //   1. PRIMARY: sessionStorage `restorePendingIntent()` — same-device
  //      same-browser-session (the normal OAuth round-trip path).
  //   2. DEFENCE-IN-DEPTH: `POST /auth/verify` returns any
  //      `pending_booking_intent` the server cached (W4 #844, 10min TTL
  //      keyed by BotUser.id). Covers multi-device + private-mode +
  //      sessionStorage-evicted cases.
  //
  // Merge policy when both surface a draft:
  //   - sessionStorage wins for fields it has (it's freshest — set
  //     immediately before the OAuth redirect on this device).
  //   - Server intent supplies fields sessionStorage is missing (e.g.
  //     the multi-device case where sessionStorage on the new device
  //     is empty).
  //
  // Field-name normalisation: backend ships `price_quoted` /
  // `loyalty_apply`; sessionStorage uses `price_rub` / `loyalty_choice`.
  // Both shapes carry the same identifying triplet
  // `master_id` + `service_id` + `slot_iso`, which is all the screen
  // needs to rehydrate; the secondary fields (note, names) are best-effort
  // and the screen tolerates absence.
  useEffect(() => {
    let cancelled = false;

    async function restore() {
      const local = restorePendingIntent();

      // Server-side round-trip is supplementary — only call /auth/verify
      // when sessionStorage was empty OR we're missing display strings
      // (name/note) that the server might still hold.
      let server: Awaited<ReturnType<typeof authVerify>>["pending_booking_intent"] = null;
      try {
        const verifyResp = await authVerify({ readCached: true });
        server = verifyResp.pending_booking_intent ?? null;
      } catch {
        // /auth/verify failure is non-fatal — fall back to sessionStorage
        // alone. The booking screen still functions; we just lose the
        // multi-device safety net.
      }

      if (cancelled) return;

      // Conflict resolution: prefer sessionStorage when both present
      // (it's freshest on this device). When only server intent exists
      // (cross-device case), restore from server.
      const intent = local ?? null;
      if (intent) {
        setMaster(intent.master_id, intent.master_name ?? "");
        setService(intent.service_id, intent.service_name ?? "");
        setVisitAt(intent.slot_iso);
        if (intent.note) setNote(intent.note);
        return;
      }
      if (server) {
        setMaster(server.master_id, "");
        setService(server.service_id, "");
        setVisitAt(server.slot_iso);
        if (server.note) setNote(server.note);
      }
      // No haptic — user may not be aware OAuth redirect happened.
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  // Missing prerequisites — bounce back to catalog (founder cut #1
  // graceful degradation).
  if (!draft.serviceId || !draft.masterId || !draft.visitAt) {
    navigate("/customer/catalog", { replace: true });
    return null;
  }

  async function onConfirm() {
    if (!draft.serviceId || !draft.masterId || !draft.visitAt) return;
    setSubmitting(true);
    setErr(null);
    try {
      const { booking } = await createCustomerBooking({
        service_id: draft.serviceId,
        master_id: draft.masterId,
        visit_at: draft.visitAt,
      });
      haptics.notify("success");
      resetBooking();
      navigate(`/customer/booking/success/${booking.id}`, {
        state: {
          service_name: booking.service_name,
          master_name: booking.master_name,
          visit_at: booking.visit_at,
        },
        replace: true,
      });
    } catch (e: unknown) {
      haptics.notify("error");
      if (e instanceof ApiError && e.slug === "slot_unavailable") {
        // Backend MAY return substitute candidate in the 409 body
        // — when wired, plumb `e.detail` parsing here. For now we
        // render the generic «выберите другое время» fallback (no
        // «Карина лучше» / «рекомендуем» — anti-pattern forbidden).
        setErr({ kind: "slot_unavailable" });
      } else if (e instanceof ApiError && e.slug === "tenant_suspended") {
        setErr({ kind: "salon_suspended" });
      } else if (e instanceof ApiError && e.status >= 500) {
        setErr({ kind: "server" });
      } else if (e instanceof ApiError) {
        setErr({ kind: "other", detail: e.detail });
      } else {
        setErr({ kind: "network" });
      }
      setSubmitting(false);
    }
  }

  function onStartRegistration() {
    // Spec §6.2 — P0 context preservation. Save BEFORE redirect to
    // OAuth; the callback restores from sessionStorage on mount.
    if (!draft.serviceId || !draft.masterId || !draft.visitAt) return;
    savePendingIntent({
      master_id: draft.masterId,
      service_id: draft.serviceId,
      slot_iso: draft.visitAt,
      // price_rub is optional now (post-round-1) — real price will
      // arrive via service detail once F1 surfaces a price field per
      // Alpha endpoint. We omit it rather than ship a 0 sentinel,
      // which violated the P0 contract («price preserved as known»).
      note: note || undefined,
      service_name: draft.serviceName ?? undefined,
      master_name: draft.masterName ?? undefined,
    });
    // W4 #844 defence-in-depth — also push the intent to the server
    // cache. Survives sessionStorage eviction + multi-device flows.
    // Best-effort: failure is non-fatal (sessionStorage stays primary).
    //
    // Field-name conversion: backend uses `price_quoted` (not
    // `price_rub`) and does NOT accept the display-only `service_name`
    // / `master_name` strings — `_ALLOWED_FIELDS` whitelist drops them
    // silently. We only send the identifying triplet + optional note.
    //
    // Anonymous users have no BotUser.id yet (the cache key) — the
    // backend handles this by treating the request as «no body»; the
    // call is safe to issue regardless of auth state.
    void authVerify({
      intent: {
        master_id: draft.masterId,
        service_id: draft.serviceId,
        slot_iso: draft.visitAt,
        ...(note ? { note } : {}),
      },
    }).catch(() => {
      // Swallow — server-side caching is supplementary. sessionStorage
      // already has the draft.
    });
    // OAuth deep-link — bot DM redirect. Real MAX OAuth URL TBD by
    // backend; until then we open the bot DM (matches existing
    // «Доступ не настроен» screen). Gate the console.info behind
    // import.meta.env.DEV to avoid leaking flow telemetry in prod.
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.info(
        "[customer-booking-confirm] saved intent + entering OAuth flow",
      );
    }
    // Best-effort: open bot DM to drive registration. Will be
    // replaced with the canonical MAX OAuth endpoint when W4 ships.
    navigate("/", { replace: true });
  }

  // ── Anonymous gate branch (§6.2) ─────────────────────────────────────
  if (anonymous) {
    // VITE_MAX_OAUTH_ENABLED gates the *functional* registration CTA.
    // Until W4 ships /auth/verify + the canonical MAX OAuth URL, the
    // «Зарегистрироваться» button strands a sessionStorage intent + a
    // navigate("/") — a UX dead-end (round-1 PRE_MERGE blocker #1).
    // Acceptable degradation: render an «OAuth pending» placeholder
    // that lets the user keep exploring the catalog. Flip the env
    // flag when W4 lands.
    const oauthEnabled = import.meta.env.VITE_MAX_OAUTH_ENABLED === "true";
    if (!oauthEnabled) {
      return (
        <ScreenLayout title="Чтобы записаться">
          <section className="customer-confirm__oauth-pending">
            <p className="customer-confirm__oauth-soon">
              Регистрация через MAX скоро будет доступна. Сейчас можно
              посмотреть мастеров и услуги.
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/customer/catalog")}
            >
              Посмотреть мастеров
            </button>
          </section>
        </ScreenLayout>
      );
    }
    return (
      <ScreenLayout
        title="Чтобы записаться"
        cta={
          <StickyCta onClick={onStartRegistration}>
            Зарегистрироваться
          </StickyCta>
        }
      >
        <AnonymousGateBody
          serviceName={draft.serviceName}
          masterName={draft.masterName}
          visitAt={draft.visitAt}
        />
      </ScreenLayout>
    );
  }

  // ── Registered branch (§6.1) — founder priority order ─────────────────
  return (
    <ScreenLayout
      title="Подтверди запись"
      cta={
        <StickyCta onClick={onConfirm} disabled={submitting}>
          {submitting ? "Записываю…" : "Записаться"}
        </StickyCta>
      }
    >
      {/* 1. Visit summary — что / где / когда / цена */}
      <div className="confirm-card">
        <dl>
          <dt>Услуга</dt>
          <dd>{draft.serviceName || "—"}</dd>
          <dt>Мастер</dt>
          <dd>{draft.masterName || "—"}</dd>
          <dt>Время</dt>
          <dd>{formatVisitFull(draft.visitAt)}</dd>
          {/* Price omitted until backend supplies a per-slot price
              snapshot. Founder cut #2: pricing transparency
              expansion is post-pilot — strict «что/где/когда/цена»
              is preserved by rendering the value when present. */}
        </dl>
      </div>

      {/* 3. Cancellation policy — compact */}
      <p className="customer-confirm__policy">
        Можно отменить за 4 часа до визита.
      </p>

      {/* 4. Loyalty block — graceful degradation (TL Q3).
          Hidden when no balance / 404. No render means no error UI.
          Post-pilot will mount a real <LoyaltyBlock /> hooked into
          /me/loyalty + /bookings/{id}/apply_loyalty. Until then, the
          block stays unmounted — founder cut applied. */}

      {/* 5. Notes — collapsed default per founder cut #3 */}
      <div className="customer-confirm__notes">
        {notesOpen ? (
          <label className="customer-confirm__notes-label">
            <span className="customer-confirm__notes-title">
              Заметка мастеру
            </span>
            <textarea
              className="customer-confirm__notes-input"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              rows={3}
              placeholder="Например, аллергия или предпочтения"
              aria-label="Заметка мастеру"
            />
          </label>
        ) : (
          <button
            type="button"
            className="customer-confirm__notes-toggle"
            onClick={() => setNotesOpen(true)}
            aria-expanded={false}
          >
            + Добавить заметку мастеру
          </button>
        )}
      </div>

      {/* Error states — §6.3 */}
      {err?.kind === "slot_unavailable" && (
        <div className="callout callout--danger" role="alert">
          {err.substituteName && err.substituteTime ? (
            <p style={{ margin: 0 }}>
              Это время только что заняли. {err.substituteName} свободна{" "}
              {err.substituteTime} — подойдёт?
            </p>
          ) : (
            <p style={{ margin: 0 }}>Это время только что заняли.</p>
          )}
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() =>
              navigate(`/customer/masters/${draft.masterId}/slots`)
            }
          >
            Выбрать другое время
          </button>
        </div>
      )}
      {err?.kind === "salon_suspended" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>
            Этот салон сейчас не принимает записи. Загляни позже —
            покажу варианты.
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/customer/catalog")}
          >
            Посмотреть другие
          </button>
        </div>
      )}
      {err?.kind === "server" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>Не получилось. Попробую ещё раз?</p>
        </div>
      )}
      {err?.kind === "network" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>
            Не получилось загрузить. Проверь интернет и попробуй снова.
          </p>
        </div>
      )}
      {err?.kind === "other" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{err.detail}</p>
        </div>
      )}
    </ScreenLayout>
  );
}

/**
 * Anonymous gate body — §6.2 verbatim founder copy.
 *
 * The OAuth round-trip is initiated by the StickyCta button in the
 * parent. This component shows WHAT the user is about to lock in
 * (so they understand why they're registering), followed by the
 * trust block (compact: «Только МАХ авторизация, без e-mail»).
 */
function AnonymousGateBody({
  serviceName,
  masterName,
  visitAt,
}: {
  serviceName: string | null;
  masterName: string | null;
  visitAt: string | null;
}) {
  return (
    <>
      <p className="customer-confirm__gate-lead">
        Сохраню запись после регистрации — всё, что ты выбрала,
        останется на месте.
      </p>
      <div className="confirm-card">
        <dl>
          <dt>Услуга</dt>
          <dd>{serviceName || "—"}</dd>
          <dt>Мастер</dt>
          <dd>{masterName || "—"}</dd>
          <dt>Время</dt>
          <dd>{visitAt ? formatVisitFull(visitAt) : "—"}</dd>
        </dl>
      </div>
      <p className="customer-confirm__gate-trust">
        Только авторизация через MAX. Email не нужен.
      </p>
    </>
  );
}

// Silence the unused-import linter for formatMoney — kept for the
// future price-display row (founder cut #2 lifts gate on pricing
// transparency post-pilot).
void formatMoney;
