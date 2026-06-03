/**
 * Customer wellness dashboard — Tier 1 Priority 2 Phase B.
 *
 * Spec: `docs/screens/customer-main-wellness-dashboard.md` §3 Variant B
 * (Compact Hero v2) + §5 (5 states) + §6 (Backend data needs) + §7
 * (Brand) + §8 (WCAG 2.2 AA) + §11 (10 explicit impl notes).
 *
 * Memory refs:
 *   - `project_variant_b_wellness_mvp` — founder pivot 2026-05-25
 *   - `project_cross_domain_insight_safety_gap` — factual-only widgets,
 *     NO insight cards (Block 6 removed per Q-BACK-4)
 *   - `project_pilot_scope_discipline` — pilot-scope cuts
 *   - `project_ayla_personal_ai` — first-person Ayla voice, «ты»,
 *     lowercase «ayla» wordmark
 *
 * # Layout (TL extension to Tau §3 — block re-numbering)
 *
 *   Block 1 — Greeting + human one-liner (Tau §3 / §11.9 + §11.10)
 *   Block 2 — Pulse strip (Питание + Вода + Цель)  — Tau §3 + §11.1
 *   Block 3 — Quick actions 2×2 (📸 + 💧 + 🎯 + 📅) — Tau §3 + §11.5+§11.7+§11.8
 *   Block 4 — Цели сегодня (text actions, no progress bars) — Tau §3
 *   Block 5 — Ближайшая запись (multi-record indicator) — Tau §3 + §11.3
 *   Block 6 — Прогресс недели (cold-start ≥3 days) — Tau §3 + §11.4
 *   Block 7 — Recommendations embed (TL extension; reuses Phase B stub)
 *   Bottom nav — 5 tabs (🏠 Главная / ☀ День / 📅 Записи / 💅 Услуги / 👤 Я)
 *
 * NOTE: Tau's literal Block 7 is the bottom nav. TL re-numbering treats
 * the bottom nav as separate and adds a new Block 7 (Recommendations
 * embed) above it. Implemented per TL ticket; deviation documented.
 *
 * # Anti-pattern enforcement
 *
 * Anti-patterns FORBIDDEN in any rendered string from this screen:
 *   - «рекомендуем» / «лучший» / «спонсировано» / «премиум» / «топ»
 *   - «Ayla заметила…» — insight cards REMOVED per Q-BACK-4
 *   - «ты пьёшь мало воды!» — pressure messaging
 *   - Medical content of any kind
 *
 * All copy is verbatim-from-spec OR factual data labels. The human
 * one-liner is selected via STATIC template — see `pickOneLiner` in
 * `lib/customer-wellness.ts`. NEVER LLM-generated.
 *
 * # WCAG 2.2 AA (Tau §8 — 6 BLOCKERS addressed inline)
 *
 *   1. Target Size — every tap target ≥44×44dp via padding (CSS).
 *   2. Contrast Minimum — token colors used; brand sage NOT used for
 *      body text (which would fail 4.5:1).
 *   3. Non-text content — water dots / progress bars wrapped in
 *      `role="progressbar"` + aria-valuenow/max/label. Visual glyphs
 *      `aria-hidden="true"`.
 *   4. Info & Relationships — Pulse rows composite-labelled.
 *   5. Language of page + parts — Mini App ships `<html lang="ru">`;
 *      «ayla» wordmark wrapped `<span lang="en">Ayla</span>` per §8.5.
 *   6. Non-text Contrast — progress-bar track + borders use
 *      explicit verified tokens (handled in globals.css).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import {
  enqueueWaterLog,
  flushWaterQueue,
  getRecentActivity,
  getWellnessToday,
  isOnboardingDismissed,
  markOnboardingDismissed,
  pickGreeting,
  pickOneLiner,
  readWaterQueue,
  type RecentActivity,
  type WellnessToday,
} from "../lib/customer-wellness";
import {
  getCatalogRecommendations,
  type CatalogLayer2Item,
  type CatalogRecommendations,
} from "../lib/customer-booking";
import { fetchFoodPhotoScanEnabled } from "../lib/food-scanner";

// ---------------------------------------------------------------------------
// Loading + error state model — per-block isolation for «Partial» state
// (Tau §5 State 5). Each remote slice carries its own status so a
// failed bookings call doesn't blank the pulse strip.
// ---------------------------------------------------------------------------

type Slice<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; reason: "network" | "server" | "other" };

function isOnline(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine !== false;
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export function CustomerWellnessDashboardScreen() {
  const navigate = useNavigate();

  const [today, setToday] = useState<Slice<WellnessToday>>({ kind: "loading" });
  const [activity, setActivity] = useState<Slice<RecentActivity>>({
    kind: "loading",
  });
  const [recs, setRecs] = useState<Slice<CatalogRecommendations>>({
    kind: "loading",
  });

  const [online, setOnline] = useState<boolean>(isOnline());
  const [waterQueueLen, setWaterQueueLen] = useState<number>(
    () => readWaterQueue().length,
  );
  const [waterToast, setWaterToast] = useState<string | null>(null);
  const [photoToast, setPhotoToast] = useState<string | null>(null);
  // FOOD_PHOTO_SCAN_ENABLED flag (Path B off-state per TL 2026-06-02).
  // Initial state matches the eventual fail-safe default per build
  // mode (adversarial CR PRE_PILOT #3): DEV starts at null so the
  // existing photo-flow QA renders the photo CTA until the URL gate
  // (?photo_gate=off) flips it; PROD starts at false so the first
  // render already shows the off-state label + icon + route. This
  // eliminates the «Сфотографируй еду» → tap → /capture → /manual
  // cascade and the visible label flash on slower MAX webview.
  // FOLLOW_UP #170: swap fetchFoodPhotoScanEnabled to real /me extension.
  // FOLLOW_UP #171: Tau-finalised copy for off-state CTA label.
  const [photoEnabled, setPhotoEnabled] = useState<boolean | null>(() =>
    import.meta.env.DEV ? null : false,
  );
  const [onboardingDismissed, setOnboardingDismissed] = useState<boolean>(
    () => isOnboardingDismissed(),
  );

  // ── data fetch ────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    setToday({ kind: "loading" });
    setActivity({ kind: "loading" });
    setRecs({ kind: "loading" });

    // Per-slice isolation — Promise.allSettled so one failure doesn't
    // blank all three blocks. (Tau §5 State 5 partial render.)
    const [todayRes, activityRes, recsRes] = await Promise.allSettled([
      getWellnessToday(),
      getRecentActivity(),
      getCatalogRecommendations(),
    ]);

    if (todayRes.status === "fulfilled") {
      setToday({ kind: "ok", data: todayRes.value });
    } else {
      const e = todayRes.reason;
      setToday({
        kind: "error",
        reason:
          e instanceof ApiError && e.status >= 500
            ? "server"
            : e instanceof ApiError
              ? "other"
              : "network",
      });
    }

    if (activityRes.status === "fulfilled") {
      setActivity({ kind: "ok", data: activityRes.value });
    } else {
      const e = activityRes.reason;
      setActivity({
        kind: "error",
        reason:
          e instanceof ApiError && e.status >= 500
            ? "server"
            : e instanceof ApiError
              ? "other"
              : "network",
      });
    }

    if (recsRes.status === "fulfilled") {
      setRecs({ kind: "ok", data: recsRes.value });
    } else {
      // Recommendations errors hide the whole block silently per spec.
      setRecs({ kind: "error", reason: "other" });
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    let cancelled = false;
    fetchFoodPhotoScanEnabled().then((enabled) => {
      if (!cancelled) setPhotoEnabled(enabled);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── online / offline transitions ─────────────────────────────────────
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => {
      setOnline(true);
      // Auto-flush water queue on reconnect (Tau §11.8).
      void flushWaterQueue().then(() => {
        setWaterQueueLen(readWaterQueue().length);
      });
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Auto-clear transient toasts after 3s.
  useEffect(() => {
    if (!waterToast) return;
    const t = setTimeout(() => setWaterToast(null), 3000);
    return () => clearTimeout(t);
  }, [waterToast]);
  useEffect(() => {
    if (!photoToast) return;
    const t = setTimeout(() => setPhotoToast(null), 3000);
    return () => clearTimeout(t);
  }, [photoToast]);

  // ── quick-action handlers ────────────────────────────────────────────
  const onPhotoTap = useCallback(() => {
    // §11.7 — disable + toast when offline.
    if (!online) {
      setPhotoToast("Для распознавания нужна сеть. Попробуйте позже.");
      return;
    }
    // Path B off-state (photoEnabled=false) routes directly to manual
    // entry. The capture screen also has its own off-state redirect
    // (defence-in-depth), but skipping the redirect hop saves ~1
    // render cycle + avoids the photo headline flashing on Dashboard
    // taps when the customer is heading to manual anyway.
    if (photoEnabled === false) {
      navigate("/customer/food-scanner/manual");
      return;
    }
    navigate("/customer/food-scanner/capture");
  }, [navigate, online, photoEnabled]);

  const onWaterTap = useCallback(() => {
    // §11.8 — offline: queue to localStorage 24h TTL. Real POST is
    // wired by W4; STUB MODE simulates instant accept when online.
    if (!online) {
      const len = enqueueWaterLog(250);
      setWaterQueueLen(len);
      setWaterToast(
        `+1 стакан · ${len} ${ruPluralWater(len)} ${ruPluralWaterWaits(len)} синхронизации`,
      );
      return;
    }
    // Online — also enqueue + immediately flush. This keeps the queue
    // as a single durable code path while STUB doesn't have a real
    // endpoint. Once W4 ships, the online branch will skip enqueue.
    enqueueWaterLog(250);
    void flushWaterQueue().then(() => {
      setWaterQueueLen(readWaterQueue().length);
    });
    setWaterToast("+1 стакан зачтён");
  }, [online]);

  const onGoalTap = useCallback(() => {
    // §11.2 — context-aware: «Моя цель» if any, «Выбери цель» if none.
    // Until the goal-editor screen ships (post-pilot), bounce to bot DM
    // for ambient onboarding. Until that screen exists in Mini App we
    // keep the action ambient — navigate to root chooser. The handler
    // is kept stable so a future wiring change is single-site.
    navigate("/");
  }, [navigate]);

  const onCatalogTap = useCallback(() => {
    navigate("/customer/catalog");
  }, [navigate]);

  const onDismissOnboarding = useCallback(() => {
    markOnboardingDismissed();
    setOnboardingDismissed(true);
  }, []);

  // ── derived state ────────────────────────────────────────────────────
  const todayData = today.kind === "ok" ? today.data : null;
  const activityData = activity.kind === "ok" ? activity.data : null;
  const recsData = recs.kind === "ok" ? recs.data : null;

  const now = new Date();
  const greeting = pickGreeting(now);
  const displayName = todayData?.display_name ?? "";
  const waterRatio = todayData
    ? todayData.water_glasses_target > 0
      ? todayData.water_glasses_eaten / todayData.water_glasses_target
      : 0
    : 0;
  const hasAnyLogs =
    !!todayData &&
    (todayData.calories_eaten > 0 || todayData.water_glasses_eaten > 0);
  const oneLiner = todayData
    ? pickOneLiner({
        hour: now.getHours(),
        hint: todayData.day_pattern_hint,
        waterRatio,
        hasAnyLogs,
        hasNextBooking: !!activityData?.next_booking,
      })
    : "";

  // Onboarding card visible iff: data loaded, no logs anywhere, no
  // bookings, no dismiss flag (§11.6). Auto-hide after first action is
  // handled implicitly: enqueueWaterLog drives waterQueueLen > 0 →
  // hasAnyLogs becomes true on next data refresh, OR user taps dismiss.
  const showOnboarding =
    !onboardingDismissed &&
    todayData !== null &&
    !hasAnyLogs &&
    !activityData?.next_booking;

  // Block 6 cold-start gate — show iff active_days_count >= 3 (§11.4).
  const showWeekly =
    !!activityData &&
    activityData.weekly_progress.active_days_count >= 3;

  // Block 4 actionable targets visibility.
  const waterRemaining = todayData
    ? Math.max(0, todayData.water_glasses_target - todayData.water_glasses_eaten)
    : 0;
  const proteinDeficit =
    todayData?.pfc && todayData.pfc.protein_target_g
      ? Math.max(0, todayData.pfc.protein_target_g - todayData.pfc.protein_g)
      : 0;
  const showTodayGoals = waterRemaining > 0 || proteinDeficit > 0;

  // ── render ────────────────────────────────────────────────────────────
  return (
    <div className="wellness-dash" lang="ru">
      {/* Skip link — WCAG 2.4.1 (Tau §8 IMPORTANT #7). */}
      <a className="wellness-dash__skip-link" href="#wellness-main">
        К основному содержимому
      </a>

      {/* Header — 56dp. */}
      <header className="wellness-dash__header" role="banner">
        <div className="wellness-dash__brand">
          {/* «ayla» = English wordmark per Tau §7 — wrap in lang="en"
              to keep RU TTS from pronouncing it «Айла» (WCAG 3.1.2). */}
          <span className="wellness-dash__wordmark" lang="en">
            ayla
          </span>
          <span aria-hidden="true"> ✨</span>
        </div>
        <button
          type="button"
          className="wellness-dash__ask"
          aria-label="Спросить у ayla"
          onClick={() => navigate("/")}
        >
          спросить
        </button>
        <div className="wellness-dash__header-icons">
          <button
            type="button"
            className="wellness-dash__icon-btn"
            aria-label="Профиль"
            onClick={() => navigate("/customer/profile")}
          >
            <span aria-hidden="true">👤</span>
          </button>
          <button
            type="button"
            className="wellness-dash__icon-btn"
            aria-label="Настройки"
            onClick={() => navigate("/customer/profile")}
          >
            <span aria-hidden="true">⚙</span>
          </button>
        </div>
      </header>

      {/* Offline banner — §5 State 4. */}
      {!online && (
        <div
          className="wellness-dash__offline-banner"
          role="status"
          aria-live="polite"
        >
          <span aria-hidden="true">⚡</span> Без сети — показываю последние
          данные
        </div>
      )}

      <main id="wellness-main" className="wellness-dash__main">
        {/* Block 1 — Greeting + human one-liner (§11.9 + §11.10). */}
        <section className="wellness-dash__greeting" aria-label="Приветствие">
          {today.kind === "loading" ? (
            <>
              <div
                className="skeleton wellness-dash__skel-line"
                style={{ width: "60%", height: 24 }}
                aria-hidden="true"
              />
              <div
                className="skeleton wellness-dash__skel-line"
                style={{
                  width: "85%",
                  height: 16,
                  marginTop: "var(--s-2)",
                }}
                aria-hidden="true"
              />
            </>
          ) : (
            <>
              <h1 className="wellness-dash__greeting-title">
                {greeting}
                {displayName ? `, ${displayName}` : ""}{" "}
                <span aria-hidden="true">🌿</span>
              </h1>
              {oneLiner && (
                <p className="wellness-dash__one-liner">{oneLiner}</p>
              )}
            </>
          )}
        </section>

        {/* Onboarding card — first-time empty state (§5 State 2 + §11.6). */}
        {showOnboarding && (
          <section
            className="wellness-dash__onboarding"
            aria-label="Подсказка для начала"
          >
            <div className="wellness-dash__onboarding-body">
              <p className="wellness-dash__onboarding-title">Начнём с малого?</p>
              <p className="wellness-dash__onboarding-text">
                Сфотографируй первый приём, добавь воду или выбери цель.
              </p>
              <button
                type="button"
                className="wellness-dash__onboarding-dismiss"
                aria-label="Скрыть подсказку"
                onClick={onDismissOnboarding}
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
          </section>
        )}

        {/* Block 2 — Pulse strip (§11.1 — conditional БЖУ). */}
        <section className="wellness-dash__pulse" aria-label="Сегодня">
          {today.kind === "loading" && <PulseSkeleton />}
          {today.kind === "error" && (
            <BlockError
              reason={today.reason}
              onRetry={() => void fetchAll()}
            />
          )}
          {today.kind === "ok" && (
            <PulseStrip data={today.data} />
          )}
        </section>

        {/* Block 3 — Quick actions 2×2 (§11.5 + §11.7 + §11.8). */}
        <section
          className="wellness-dash__quick-actions"
          aria-labelledby="qa-header"
        >
          <h2 id="qa-header" className="wellness-dash__section-header">
            Что сделаем сейчас
          </h2>
          <div className="wellness-dash__qa-grid">
            <button
              type="button"
              className="wellness-dash__qa-btn"
              aria-label={
                photoEnabled === false ? "Записать еду" : "Сфотографируй еду"
              }
              aria-disabled={!online}
              aria-describedby={!online ? "qa-photo-hint" : undefined}
              onClick={onPhotoTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                {photoEnabled === false ? "📝" : "📸"}
              </span>
              <span className="wellness-dash__qa-label">
                {/*
                  Path B placeholder copy pending Tau finalisation
                  (FOLLOW_UP #171). «Записать еду» matches voice spec
                  §10 («Записать в дневник» precedent) without making
                  a coming-soon promise (§14 anti-pattern). Tau swap
                  may refine to «Что съела?» / «Добавить приём».
                */}
                {photoEnabled === false
                  ? "Записать еду"
                  : "Сфотографируй еду"}
              </span>
              {!online && (
                <span
                  id="qa-photo-hint"
                  className="wellness-dash__qa-hint"
                >
                  (нужна сеть)
                </span>
              )}
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn"
              aria-label="Добавить стакан воды 250 мл"
              onClick={onWaterTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                💧
              </span>
              <span className="wellness-dash__qa-label">
                + стакан 250 мл
              </span>
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn"
              aria-label={
                todayData && todayData.active_goals.length > 0
                  ? "Моя цель"
                  : "Выбери цель"
              }
              onClick={onGoalTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                🎯
              </span>
              <span className="wellness-dash__qa-label">
                {/* §11.2 — context-aware label */}
                {todayData && todayData.active_goals.length > 0
                  ? "Моя цель"
                  : "Выбери цель"}
              </span>
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn"
              aria-label="Найди услугу"
              onClick={onCatalogTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                📅
              </span>
              <span className="wellness-dash__qa-label">Найди услугу</span>
            </button>
          </div>

          {/* Sync indicator for offline water queue (§11.8). */}
          {waterQueueLen > 0 && (
            <p
              className="wellness-dash__queue-indicator"
              role="status"
              aria-live="polite"
            >
              +{waterQueueLen} {ruPluralWater(waterQueueLen)}{" "}
              {ruPluralWaterWaits(waterQueueLen)} синхронизации
            </p>
          )}
          {/* Transient toasts. */}
          {waterToast && (
            <div
              className="wellness-dash__toast"
              role="status"
              aria-live="polite"
            >
              {waterToast}
            </div>
          )}
          {photoToast && (
            <div
              className="wellness-dash__toast"
              role="status"
              aria-live="polite"
            >
              {photoToast}
            </div>
          )}
        </section>

        {/* Block 4 — Цели сегодня (text actions). */}
        {showTodayGoals && today.kind === "ok" && (
          <section
            className="wellness-dash__today-goals"
            aria-labelledby="tg-header"
          >
            <h2 id="tg-header" className="wellness-dash__section-header">
              Цели сегодня
            </h2>
            <ul className="wellness-dash__goal-list">
              {waterRemaining > 0 && (
                <li className="wellness-dash__goal-item">
                  <span aria-hidden="true">💧</span>{" "}
                  <span>
                    Ещё {waterRemaining}{" "}
                    {ruPluralWater(waterRemaining)} до цели
                  </span>
                </li>
              )}
              {proteinDeficit > 0 && (
                <li className="wellness-dash__goal-item">
                  <span aria-hidden="true">🍽</span>{" "}
                  <span>Добрать белок · ещё {proteinDeficit} г</span>
                </li>
              )}
            </ul>
          </section>
        )}

        {/* Block 5 — Ближайшая запись (+ multi-record indicator §11.3). */}
        <section
          className="wellness-dash__booking"
          aria-labelledby="booking-header"
        >
          <h2 id="booking-header" className="wellness-dash__section-header">
            Ближайшая запись
          </h2>
          {activity.kind === "loading" && <BookingSkeleton />}
          {activity.kind === "error" && (
            <BlockError
              reason={activity.reason}
              onRetry={() => void fetchAll()}
            />
          )}
          {activity.kind === "ok" && !activity.data.next_booking && (
            <BookingEmpty onFind={onCatalogTap} />
          )}
          {activity.kind === "ok" && activity.data.next_booking && (
            <BookingCard
              data={activity.data}
              onOpen={() =>
                activity.data.next_booking &&
                navigate(
                  `/customer/records/${activity.data.next_booking.booking_id}`,
                )
              }
              onReschedule={() =>
                activity.data.next_booking &&
                navigate(
                  `/my-visits/${activity.data.next_booking.booking_id}/reschedule`,
                )
              }
              onAll={() => navigate("/customer/records")}
            />
          )}
        </section>

        {/* Block 6 — Прогресс недели (cold-start gate §11.4). */}
        {showWeekly && activity.kind === "ok" && (
          <section
            className="wellness-dash__weekly"
            aria-labelledby="weekly-header"
          >
            <h2 id="weekly-header" className="wellness-dash__section-header">
              Прогресс недели
            </h2>
            <ul className="wellness-dash__weekly-list">
              <li>
                <span aria-hidden="true">💧</span> Вода:{" "}
                {activity.data.weekly_progress.water_days_logged} из 7 дней
              </li>
              <li>
                <span aria-hidden="true">🍽</span> Питание:{" "}
                {activity.data.weekly_progress.food_days_logged} из 7 дней
              </li>
              <li>
                <span aria-hidden="true">📅</span> Активность:{" "}
                {activity.data.weekly_progress.active_days_count} дней
              </li>
            </ul>
            <button
              type="button"
              className="btn-secondary wellness-dash__weekly-more"
              onClick={() => navigate("/")}
            >
              Подробнее в Дне
            </button>
          </section>
        )}

        {/* Block 7 — Recommendations embed (TL extension; Phase B stub).
            Hide silently on error / empty (spec: no error UI). */}
        {recsData &&
          recsData.layer_2_ayla_picks.length > 0 && (
            <section
              className="wellness-dash__recos"
              aria-labelledby="recos-header"
            >
              <h2 id="recos-header" className="wellness-dash__section-header">
                <span aria-hidden="true">✨ </span>
                <span lang="en">Ayla</span> подобрала тебе
              </h2>
              <ul className="wellness-dash__reco-list">
                {recsData.layer_2_ayla_picks.slice(0, 3).map((item) => (
                  <li key={item.id}>
                    <RecoCard
                      item={item}
                      onOpen={() =>
                        navigate(`/customer/masters/${item.id}`)
                      }
                    />
                  </li>
                ))}
              </ul>
            </section>
          )}
      </main>

      {/* Bottom nav — 5 tabs. */}
      <nav className="wellness-dash__nav" aria-label="Основная навигация">
        <button
          type="button"
          className="wellness-dash__nav-tab wellness-dash__nav-tab--active"
          aria-current="page"
          aria-label="Главная"
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            🏠
          </span>
          <span className="wellness-dash__nav-label">Главная</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="День"
          onClick={() => navigate("/")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            ☀
          </span>
          <span className="wellness-dash__nav-label">День</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Записи"
          onClick={() => navigate("/customer/records")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            📅
          </span>
          <span className="wellness-dash__nav-label">Записи</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Услуги"
          onClick={onCatalogTap}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            💅
          </span>
          <span className="wellness-dash__nav-label">Услуги</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Я"
          onClick={() => navigate("/customer/profile")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            👤
          </span>
          <span className="wellness-dash__nav-label">Я</span>
        </button>
      </nav>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function PulseSkeleton() {
  return (
    <div className="wellness-dash__pulse-card" aria-hidden="true">
      <div className="skeleton wellness-dash__skel-line" style={{ height: 18 }} />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "80%", marginTop: "var(--s-2)" }}
      />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "60%", marginTop: "var(--s-2)" }}
      />
    </div>
  );
}

function PulseStrip({ data }: { data: WellnessToday }) {
  const caloriesPct =
    data.calories_target > 0
      ? Math.round((data.calories_eaten / data.calories_target) * 100)
      : 0;
  const waterPct =
    data.water_glasses_target > 0
      ? Math.round(
          (data.water_glasses_eaten / data.water_glasses_target) * 100,
        )
      : 0;
  const goal = data.active_goals[0];

  return (
    <div className="wellness-dash__pulse-card">
      {/* Питание row */}
      <div
        className="wellness-dash__pulse-row"
        aria-label={`Питание: ${data.calories_eaten} из ${data.calories_target} килокалорий, ${caloriesPct} процентов${
          data.pfc
            ? `. Белки ${data.pfc.protein_g}, жиры ${data.pfc.fat_g}, углеводы ${data.pfc.carbs_g} граммов`
            : ""
        }`}
      >
        <div className="wellness-dash__pulse-head">
          <span aria-hidden="true">🍽 </span>Питание
        </div>
        <div className="wellness-dash__pulse-numbers" aria-hidden="true">
          {data.calories_eaten === 0 && data.water_glasses_eaten === 0
            ? "Ещё ничего не залогировано"
            : `${data.calories_eaten} / ${data.calories_target} ккал · ${caloriesPct} %`}
        </div>
        {/* §11.1 — БЖУ row hidden when pfc absent. */}
        {data.pfc && (
          <div className="wellness-dash__pulse-pfc" aria-hidden="true">
            Б {data.pfc.protein_g} · Ж {data.pfc.fat_g} · У {data.pfc.carbs_g} г
          </div>
        )}
        <div
          className="wellness-dash__progress"
          role="progressbar"
          aria-valuenow={data.calories_eaten}
          aria-valuemin={0}
          aria-valuemax={data.calories_target}
          aria-label={`Калории: ${data.calories_eaten} из ${data.calories_target}`}
        >
          <div
            className="wellness-dash__progress-fill"
            style={{ width: `${Math.min(100, caloriesPct)}%` }}
            aria-hidden="true"
          />
        </div>
      </div>

      <hr className="wellness-dash__pulse-divider" aria-hidden="true" />

      {/* Вода row */}
      <div
        className="wellness-dash__pulse-row"
        aria-label={`Вода: ${data.water_glasses_eaten} из ${data.water_glasses_target} стаканов`}
      >
        <div className="wellness-dash__pulse-head">
          <span aria-hidden="true">💧 </span>Вода
        </div>
        <div className="wellness-dash__pulse-numbers" aria-hidden="true">
          {data.water_glasses_eaten} / {data.water_glasses_target} стаканов
        </div>
        <div
          className="wellness-dash__water-dots"
          role="progressbar"
          aria-valuenow={data.water_glasses_eaten}
          aria-valuemin={0}
          aria-valuemax={data.water_glasses_target}
          aria-label={`Вода: ${data.water_glasses_eaten} из ${data.water_glasses_target} стаканов (${waterPct} процентов)`}
        >
          <span aria-hidden="true">
            {Array.from({ length: data.water_glasses_target }).map((_, i) => (
              <span key={i}>{i < data.water_glasses_eaten ? "●" : "○"}</span>
            ))}
          </span>
        </div>
      </div>

      <hr className="wellness-dash__pulse-divider" aria-hidden="true" />

      {/* Goal row — §11.2 */}
      <div
        className="wellness-dash__pulse-row"
        aria-label={
          goal
            ? `Цель: ${goal.title}, ${goal.week_num}-я неделя, ${goal.progress_pct} процентов`
            : "Цель не выбрана"
        }
      >
        <div className="wellness-dash__pulse-head">
          <span aria-hidden="true">🎯 </span>
          {goal ? `${goal.title} · ${goal.week_num}-я неделя` : "Цель не выбрана"}
        </div>
        {goal ? (
          <>
            <div
              className="wellness-dash__progress"
              role="progressbar"
              aria-valuenow={goal.progress_pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${goal.title}: ${goal.progress_pct} процентов`}
            >
              <div
                className="wellness-dash__progress-fill"
                style={{ width: `${Math.min(100, goal.progress_pct)}%` }}
                aria-hidden="true"
              />
            </div>
            <div className="wellness-dash__pulse-numbers" aria-hidden="true">
              {goal.progress_pct} %
            </div>
          </>
        ) : (
          <div className="wellness-dash__pulse-numbers">
            Расскажи о себе — точнее советую
          </div>
        )}
      </div>
    </div>
  );
}

function BookingSkeleton() {
  return (
    <div className="wellness-dash__booking-card" aria-hidden="true">
      <div className="skeleton wellness-dash__skel-line" style={{ height: 18 }} />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "70%", marginTop: "var(--s-2)" }}
      />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "50%", marginTop: "var(--s-2)" }}
      />
    </div>
  );
}

function BookingEmpty({ onFind }: { onFind: () => void }) {
  return (
    <div className="wellness-dash__booking-card">
      <p className="wellness-dash__booking-empty">
        Подобрать услугу под твою цель — расскажу что подойдёт.
      </p>
      <button type="button" className="btn-secondary" onClick={onFind}>
        Найди услугу
      </button>
    </div>
  );
}

function BookingCard({
  data,
  onOpen,
  onReschedule,
  onAll,
}: {
  data: RecentActivity;
  onOpen: () => void;
  onReschedule: () => void;
  onAll: () => void;
}) {
  const b = data.next_booking;
  if (!b) return null;
  const moreThisWeek = data.this_week_booking_count > 1;
  return (
    <div className="wellness-dash__booking-card">
      <div className="wellness-dash__booking-when">{b.date_human}</div>
      <div className="wellness-dash__booking-what">
        {b.service_name} · {b.duration_min} мин
      </div>
      <div className="wellness-dash__booking-who">
        у {b.master_name} · {b.salon_name}
      </div>
      <div className="wellness-dash__booking-where">{b.address}</div>

      {/* §11.3 — multi-record indicator. */}
      {moreThisWeek && (
        <div className="wellness-dash__booking-more">
          Ещё {data.this_week_booking_count - 1}{" "}
          {ruPluralBooking(data.this_week_booking_count - 1)} на этой неделе
        </div>
      )}

      <div className="wellness-dash__booking-actions">
        <button
          type="button"
          className="btn-secondary wellness-dash__booking-primary"
          onClick={onOpen}
          aria-label="Открыть запись"
        >
          Открыть запись
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={onReschedule}
          aria-label="Перенести запись"
        >
          Перенести
        </button>
      </div>
      {moreThisWeek && (
        <button
          type="button"
          className="wellness-dash__booking-all"
          onClick={onAll}
          aria-label="Все записи"
        >
          Все записи →
        </button>
      )}
    </div>
  );
}

function RecoCard({
  item,
  onOpen,
}: {
  item: CatalogLayer2Item;
  onOpen: () => void;
}) {
  const titleId = `wd-reco-title-${item.id}`;
  const reasoningId = `wd-reco-reason-${item.id}`;
  return (
    <article className="wellness-dash__reco-card">
      <button
        type="button"
        className="wellness-dash__reco-btn"
        onClick={onOpen}
        aria-labelledby={titleId}
        aria-describedby={reasoningId}
      >
        <div id={titleId} className="wellness-dash__reco-title">
          {item.title}
        </div>
        <div id={reasoningId} className="wellness-dash__reco-reasoning">
          {/* reasoning_text rendered VERBATIM from backend stub —
              anti-pattern audit at the stub layer (customer-booking.ts
              comments §10.3). */}
          {item.reasoning_text}
        </div>
        <div className="wellness-dash__reco-meta">
          от {item.starting_price.toLocaleString("ru-RU")} ₽ ·{" "}
          {item.next_available_slot}
        </div>
      </button>
    </article>
  );
}

function BlockError({
  reason,
  onRetry,
}: {
  reason: "network" | "server" | "other";
  onRetry: () => void;
}) {
  // Warm copy per Tau §5 State 3 + §7 brand voice.
  const text =
    reason === "network"
      ? "Не получилось загрузить. Проверь интернет и попробуй ещё раз."
      : "Что-то пошло не так. Попробуй ещё раз через минуту.";
  return (
    <div className="wellness-dash__block-error" role="status" aria-live="polite">
      <p>{text}</p>
      <button type="button" className="btn-secondary" onClick={onRetry}>
        Обновить
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Russian plural helpers — keep copy natural.
// ---------------------------------------------------------------------------

function ruPluralWater(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "стаканов";
  if (mod10 === 1) return "стакан";
  if (mod10 >= 2 && mod10 <= 4) return "стакана";
  return "стаканов";
}

// Verb agreement for "стакан(а/ов) ждёт/ждут синхронизации".
// Singular forms (1, 21, 31, ...) take "ждёт"; rest take "ждут".
// Excludes the 11-14 teen-irregular range.
function ruPluralWaterWaits(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "ждут";
  if (mod10 === 1) return "ждёт";
  return "ждут";
}

function ruPluralBooking(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "записей";
  if (mod10 === 1) return "запись";
  if (mod10 >= 2 && mod10 <= 4) return "записи";
  return "записей";
}
