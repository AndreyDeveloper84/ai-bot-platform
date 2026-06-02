/**
 * F2 Processing — Customer Food Scanner Phase B.
 *
 * Route: `/customer/food-scanner/processing` (router state carries
 * `{ photo: File, mealType: MealType }` from F1).
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §3 (F2 layout) +
 * §11 (a11y).
 *
 * # State machine
 *
 *   t=0           render Ayla line + pulsing dots
 *   t≥3000ms      show «Если занимает дольше…» + cancel button
 *   scan resolve  navigate F3 with ScanResponse + photo preview
 *   scan reject   navigate to F1 (FoodNotRecognized → manual entry
 *                 path), or surface error state inline
 *   user cancel   navigate back to F1 with photo preserved
 *   t≥10000ms     timeout — treat as NutritionUnavailableError
 *
 * Pulsing dot animation respects `prefers-reduced-motion` via CSS.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  FoodNotRecognizedError,
  NutritionUnavailableError,
  PhotoBytesMissingError,
  scanPhoto,
  type MealType,
} from "../lib/food-scanner";

interface RouterState {
  photo?: File;
  mealType?: MealType;
}

type ProcessingState =
  | { kind: "scanning" }
  | { kind: "showCancel" }
  | { kind: "error"; err: unknown };

const SHOW_CANCEL_AFTER_MS = 3000;
const TIMEOUT_MS = 10000;

export function FoodScannerProcessingScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  const photo = state.photo;
  const mealType = state.mealType ?? "lunch";

  const [phase, setPhase] = useState<ProcessingState>({ kind: "scanning" });
  const abortRef = useRef<AbortController | null>(null);
  const cancelTimerRef = useRef<number | null>(null);
  const timeoutTimerRef = useRef<number | null>(null);
  const previewUrl = useMemo(
    () => (photo ? URL.createObjectURL(photo) : null),
    [photo],
  );

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    // Preserve photo + mealType so the user does not re-pick.
    navigate("/customer/food-scanner/capture", {
      replace: true,
      state: { photo, mealType },
    });
  }, [navigate, photo, mealType]);

  useEffect(() => {
    // Guard — if user landed here without a photo (deep link refresh),
    // bounce back to F1 so we never call scan with an empty payload.
    if (!photo) {
      navigate("/customer/food-scanner/capture", { replace: true });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;

    cancelTimerRef.current = window.setTimeout(() => {
      setPhase((p) => (p.kind === "scanning" ? { kind: "showCancel" } : p));
    }, SHOW_CANCEL_AFTER_MS);

    timeoutTimerRef.current = window.setTimeout(() => {
      controller.abort();
      setPhase({ kind: "error", err: new NutritionUnavailableError() });
    }, TIMEOUT_MS);

    (async () => {
      try {
        const result = await scanPhoto(photo);
        if (controller.signal.aborted) return;
        navigate("/customer/food-scanner/result", {
          replace: true,
          state: { result, photo, mealType, previewUrl },
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setPhase({ kind: "error", err });
      }
    })();

    return () => {
      controller.abort();
      if (cancelTimerRef.current !== null)
        window.clearTimeout(cancelTimerRef.current);
      if (timeoutTimerRef.current !== null)
        window.clearTimeout(timeoutTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── error branches per spec §7 ─────────────────────────────────────
  if (phase.kind === "error") {
    return (
      <ScanErrorScreen
        err={phase.err}
        photo={photo ?? null}
        mealType={mealType}
        previewUrl={previewUrl}
      />
    );
  }

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <span className="records-screen__title">Распознавание</span>
      </header>
      <main
        className="food-scanner-screen__main food-scanner-processing"
        role="status"
        aria-live="polite"
      >
        {previewUrl && (
          <div className="food-scanner-processing__preview">
            <img
              src={previewUrl}
              alt="Фото блюда"
              className="food-scanner-processing__photo"
            />
          </div>
        )}
        <p className="food-scanner-processing__line">Узнаю что на фото</p>
        <div
          className="food-scanner-processing__dots"
          aria-hidden="true"
        >
          <span />
          <span />
          <span />
        </div>
        {phase.kind === "showCancel" && (
          <>
            <p className="food-scanner-processing__hint">
              Если занимает дольше обычного, можно отменить — фото
              останется на месте.
            </p>
            <button
              type="button"
              className="btn-secondary food-scanner-processing__cancel"
              onClick={cancel}
            >
              Отменить
            </button>
          </>
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline error screen — three branches per spec §7.
// ---------------------------------------------------------------------------

function ScanErrorScreen({
  err,
  photo,
  mealType,
  previewUrl,
}: {
  err: unknown;
  photo: File | null;
  mealType: MealType;
  previewUrl: string | null;
}) {
  const navigate = useNavigate();
  const isNotRecognized = err instanceof FoodNotRecognizedError;
  const isPhotoFailed = err instanceof PhotoBytesMissingError;
  const headline = isNotRecognized
    ? "Не разобралась"
    : isPhotoFailed
      ? "Не получилось загрузить"
      : "Сервис недоступен";
  const body = isNotRecognized
    ? "Фото немного сложное — не разобралась. Можно переснять поближе или просто написать, что было."
    : isPhotoFailed
      ? "Фото пришло, но скачать не получилось — пришли ещё раз, пожалуйста."
      : "Сервис распознавания временно недоступен. Попробуй через минуту.";
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
        <h1 className="records-screen__title">{headline}</h1>
      </header>
      <main className="food-scanner-screen__main">
        {previewUrl && (
          <div className="food-scanner-processing__preview">
            <img
              src={previewUrl}
              alt="Фото блюда"
              className="food-scanner-processing__photo"
            />
          </div>
        )}
        <div className="callout food-scanner-screen__error" role="alert">
          {body}
        </div>
        <div className="food-scanner-screen__cta-stack">
          {!isPhotoFailed && (
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                navigate("/customer/food-scanner/capture", {
                  replace: true,
                  state: { photo, mealType },
                })
              }
            >
              Переснять
            </button>
          )}
          {isPhotoFailed && (
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                navigate("/customer/food-scanner/capture", { replace: true })
              }
            >
              Сделать заново
            </button>
          )}
          {!isPhotoFailed && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                navigate("/customer/food-scanner/manual", {
                  state: { mealType },
                })
              }
            >
              Написать вручную
            </button>
          )}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate("/customer/main")}
          >
            Назад на главную
          </button>
        </div>
      </main>
    </div>
  );
}
