/**
 * F1 Capture — Customer Food Scanner Phase B (Tier 1 Priority 7).
 *
 * Route: `/customer/food-scanner/capture`
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §2 (F1 layout) +
 * §11 (a11y). Wraps a 152-ФЗ consent gate (spec §2 + memory
 * `project_variant_b_wellness_mvp`); once accepted, never re-prompts.
 *
 * # Why both camera + gallery inputs
 *
 * Spec §2 + Q-BACK-3: MAX webview MAY ignore `capture="environment"`
 * on iOS; the only safe path is two distinct `<input type="file">`
 * elements, both `accept="image/*"`, the camera one ALSO carrying
 * `capture`. iOS auto-falls back to the file picker when capture is
 * absent — calm degradation, no surprise dialog.
 *
 * # Flow
 *
 *   ↳ consent gate (first visit) → accept / decline (= back to dashboard)
 *   ↳ meal-type chip selection (default by local time bucket)
 *   ↳ photo picker (camera OR gallery)
 *   ↳ on file pick → navigate F2 with the photo in router state
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  ImageStripUnsupportedError,
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  defaultMealTypeForHour,
  fetchFoodPhotoScanEnabled,
  readConsentAt,
  saveConsentAccepted,
  stripImageMetadata,
  type MealType,
} from "../lib/food-scanner";

const MEAL_TYPES: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

interface RouterIn {
  /** When set, consent-accept bounces back here instead of staying on F1. */
  returnTo?: string;
  mealType?: MealType;
}

export function FoodScannerCaptureScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const incoming = (location.state ?? {}) as RouterIn;
  const [consentAt, setConsentAt] = useState<string | null>(() =>
    readConsentAt(),
  );
  const [mealType, setMealType] = useState<MealType>(() =>
    defaultMealTypeForHour(new Date().getHours()),
  );
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [error, setError] = useState<string | null>(null);
  // Processing flag — re-encode + strip takes 200-800ms on mid-tier
  // hardware; without disabling the buttons the customer can fire a
  // second pick + race two async navigations (adversarial CR P3).
  const [processing, setProcessing] = useState<boolean>(false);
  // Photo-scan gate (Path B off-state per TL 2026-06-02). Null = still
  // resolving; redirect happens in effect below. While loading we
  // render nothing — capture screen needs the gate to even know what
  // to show, and the fetch resolves synchronously in DEV / fail-safe
  // OFF in prod, so the window is sub-frame.
  const [photoEnabled, setPhotoEnabled] = useState<boolean | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const galleryInputRef = useRef<HTMLInputElement | null>(null);

  // Off-state guard — Path B per TL 2026-06-02. When
  // FOOD_PHOTO_SCAN_ENABLED is off, the photo path is unreachable.
  // We resolve the flag on mount but DEFER the redirect to /manual
  // until AFTER the consent gate is satisfied — otherwise a customer
  // without prior consent ping-pongs:
  //
  //   /capture (no consent + photo off) → redirect /manual
  //     → /manual sees no consent → redirect /capture (consent gate)
  //     → consent accept → photo-off triggers → redirect /manual
  //     → ... loop
  //
  // By presenting the consent gate first, then redirecting in
  // handleAcceptConsent based on the resolved flag, we get one
  // linear flow: consent → either photo UI or /manual.
  useEffect(() => {
    let cancelled = false;
    fetchFoodPhotoScanEnabled().then((enabled) => {
      if (cancelled) return;
      setPhotoEnabled(enabled);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Returning customer (consent already accepted) + photo gate off →
  // redirect immediately. New customers without consent fall through
  // to the consent gate first; handleAcceptConsent handles the
  // photo-off branch from there.
  useEffect(() => {
    if (consentAt !== null && photoEnabled === false) {
      navigate("/customer/food-scanner/manual", {
        replace: true,
        state: { mealType: incoming.mealType ?? mealType },
      });
    }
  }, [consentAt, photoEnabled, navigate, incoming.mealType, mealType]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const handleAcceptConsent = useCallback(() => {
    const now = saveConsentAccepted();
    setConsentAt(now);
    // After consent accepted, three exit paths in priority order:
    //   1. Deep-link returnTo (e.g. customer came from /manual) wins
    //      so the customer lands where they intended.
    //   2. Photo gate off → /manual (Path B off-state per TL 2026-06-02)
    //   3. Photo gate on → stay on /capture for the photo UI (default)
    if (incoming.returnTo) {
      navigate(incoming.returnTo, {
        replace: true,
        state: { mealType: incoming.mealType },
      });
      return;
    }
    if (photoEnabled === false) {
      navigate("/customer/food-scanner/manual", {
        replace: true,
        state: { mealType: incoming.mealType ?? mealType },
      });
    }
  }, [
    navigate,
    incoming.returnTo,
    incoming.mealType,
    photoEnabled,
    mealType,
  ]);

  const handleDeclineConsent = useCallback(() => {
    navigate("/customer/main");
  }, [navigate]);

  const handleFile = useCallback(
    async (file: File | null | undefined) => {
      if (!file) return;
      if (processing) return; // ignore second tap mid-strip
      // Client-side size guard — spec §13 item 1; backend caps at 10 MiB.
      const MAX_BYTES = 10 * 1024 * 1024;
      if (file.size > MAX_BYTES) {
        setError("Фото слишком большое. Можно отправить файл до 10 МБ.");
        return;
      }
      if (!file.type.startsWith("image/")) {
        setError("Это не похоже на фото. Можно картинку?");
        return;
      }
      setError(null);
      setProcessing(true);
      // Strip EXIF (incl. GPS) BEFORE the photo leaves the device.
      // Follow-up #957 — without this, geo-tagged JPEGs leak meal
      // location to the backend before the delete-after-recognition
      // runs. Server-side strip is a separate W4 hardening; this is
      // defence-in-depth layer 1.
      //
      // FAIL-CLOSED: if strip fails (HEIC on Safari, decode error,
      // encode error), we REFUSE the upload rather than silent-pass
      // the original file with GPS inside. Customer gets a clear copy
      // and can switch camera format to JPEG.
      let cleanFile: File;
      try {
        cleanFile = await stripImageMetadata(file);
      } catch (e) {
        if (e instanceof ImageStripUnsupportedError) {
          setError(
            "Не получилось обработать фото. Если у тебя iPhone, попробуй переключить формат камеры на JPEG (Настройки → Камера → Форматы → «Наиболее совместимый») и сделать снимок ещё раз.",
          );
        } else {
          setError("Не получилось обработать фото. Попробуй ещё раз.");
        }
        setProcessing(false);
        return;
      }
      // Photo flows to F2 via router state (no global store needed).
      navigate("/customer/food-scanner/processing", {
        state: { photo: cleanFile, mealType },
      });
    },
    [navigate, mealType, processing],
  );

  // ── render branches ────────────────────────────────────────────────
  // Photo gate still resolving — render nothing for the (sub-frame)
  // moment until either the redirect-to-manual fires or photoEnabled
  // flips true. This prevents a flash of the photo UI before the
  // off-state navigate replaces the screen.
  if (photoEnabled === null) {
    return null;
  }
  if (consentAt === null) {
    return (
      <ConsentGate
        onAccept={handleAcceptConsent}
        onDecline={handleDeclineConsent}
      />
    );
  }

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate("/customer/main")}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Скан еды</h1>
      </header>

      {offline && (
        <div className="records-screen__offline-banner" role="status">
          Для распознавания нужна сеть. Попробуй когда вернётся.
        </div>
      )}

      <main className="food-scanner-screen__main">
        <h2 className="food-scanner-screen__greeting">Что ешь сейчас?</h2>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-meal-type-h2"
        >
          <h3
            id="food-meal-type-h2"
            className="food-scanner-screen__section-heading"
          >
            Когда
          </h3>
          <div
            className="food-scanner-screen__meal-chips"
            role="radiogroup"
            aria-labelledby="food-meal-type-h2"
          >
            {MEAL_TYPES.map((mt) => (
              <button
                key={mt}
                type="button"
                role="radio"
                aria-checked={mealType === mt}
                className={`food-scanner-screen__chip${
                  mealType === mt ? " food-scanner-screen__chip--active" : ""
                }`}
                onClick={() => setMealType(mt)}
              >
                <span aria-hidden="true">{MEAL_TYPE_ICON[mt]}</span>
                <span>{MEAL_TYPE_LABEL[mt]}</span>
              </button>
            ))}
          </div>
        </section>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-photo-h2"
        >
          <h3
            id="food-photo-h2"
            className="food-scanner-screen__section-heading"
          >
            Фото
          </h3>
          <div className="food-scanner-screen__capture-zone">
            <p className="food-scanner-screen__capture-hint">
              Сделай фото или выбери из галереи
            </p>
            <div
              className="food-scanner-screen__capture-actions"
              aria-busy={processing}
            >
              <button
                type="button"
                className="btn-primary food-scanner-screen__capture-btn"
                disabled={offline || processing}
                onClick={() => cameraInputRef.current?.click()}
              >
                {processing ? "Обрабатываю фото…" : "Сделать фото"}
              </button>
              <button
                type="button"
                className="btn-secondary food-scanner-screen__capture-btn"
                disabled={offline || processing}
                onClick={() => galleryInputRef.current?.click()}
              >
                Из галереи
              </button>
            </div>
            {/* Camera input — iOS uses `capture` to open the native
                Camera app; if ignored (some iOS Safari versions),
                falls back to file picker silently. */}
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              style={{ display: "none" }}
              onChange={(e) => handleFile(e.target.files?.[0])}
              aria-hidden="true"
            />
            <input
              ref={galleryInputRef}
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={(e) => handleFile(e.target.files?.[0])}
              aria-hidden="true"
            />
          </div>
        </section>

        {error && (
          <div
            className="callout callout--danger food-scanner-screen__error"
            role="alert"
          >
            {error}
          </div>
        )}

        <p className="food-scanner-screen__privacy">
          Фото нужно только чтобы узнать блюдо — удаляю сразу.
        </p>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 152-ФЗ consent gate (first visit only) — spec §2 + Profile R2 voice.
// ---------------------------------------------------------------------------

function ConsentGate({
  onAccept,
  onDecline,
}: {
  onAccept: () => void;
  onDecline: () => void;
}) {
  const navigate = useNavigate();
  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate("/customer/main")}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Скан еды</h1>
      </header>
      <main className="food-scanner-screen__main">
        <section
          className="food-scanner-consent"
          aria-labelledby="food-consent-h2"
        >
          <h2 id="food-consent-h2" className="food-scanner-consent__headline">
            Можно показать тебе фото-скан?
          </h2>
          <p className="food-scanner-consent__body">
            Я возьму фото только чтобы узнать блюдо — посчитаю примерные
            калории и БЖУ. Удаляю фото сразу после распознавания,
            мастер и салон его не видят.
          </p>
          <p className="food-scanner-consent__body">
            Если ты не хочешь — это нормально. Можно вернуться на главную
            и продолжать без сканера.
          </p>
          <div className="food-scanner-consent__actions">
            <button
              type="button"
              className="btn-primary food-scanner-consent__accept"
              onClick={onAccept}
            >
              Хорошо, разрешаю
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={onDecline}
            >
              Не сейчас
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
