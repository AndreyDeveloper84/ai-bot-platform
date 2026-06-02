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
import { useNavigate } from "react-router-dom";

import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  defaultMealTypeForHour,
  readConsentAt,
  saveConsentAccepted,
  type MealType,
} from "../lib/food-scanner";

const MEAL_TYPES: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

export function FoodScannerCaptureScreen() {
  const navigate = useNavigate();
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
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const galleryInputRef = useRef<HTMLInputElement | null>(null);

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
  }, []);

  const handleDeclineConsent = useCallback(() => {
    navigate("/customer/main");
  }, [navigate]);

  const handleFile = useCallback(
    (file: File | null | undefined) => {
      if (!file) return;
      // Client-side size guard — spec §13 item 1; backend caps at 10 MiB.
      const MAX_BYTES = 10 * 1024 * 1024;
      if (file.size > MAX_BYTES) {
        setError(
          "Фото слишком большое. Можно отправить файл до 10 МБ.",
        );
        return;
      }
      if (!file.type.startsWith("image/")) {
        setError("Это не похоже на фото. Можно картинку?");
        return;
      }
      setError(null);
      // Photo flows to F2 via router state (no global store needed).
      navigate("/customer/food-scanner/processing", {
        state: { photo: file, mealType },
      });
    },
    [navigate, mealType],
  );

  // ── render branches ────────────────────────────────────────────────
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
            <div className="food-scanner-screen__capture-actions">
              <button
                type="button"
                className="btn-primary food-scanner-screen__capture-btn"
                disabled={offline}
                onClick={() => cameraInputRef.current?.click()}
              >
                Сделать фото
              </button>
              <button
                type="button"
                className="btn-secondary food-scanner-screen__capture-btn"
                disabled={offline}
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
            калории и БЖУ. Фото удаляется сразу после распознавания,
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
