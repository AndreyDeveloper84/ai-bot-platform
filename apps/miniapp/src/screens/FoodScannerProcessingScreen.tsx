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
  PhotoTooLargeError,
  ScanBudgetExhaustedError,
  ScanDailyLimitError,
  StubNotWiredError,
  scanPhoto,
  type MealType,
} from "../lib/food-scanner";
import { ApiError } from "../lib/api";
import { useScreenBack } from "../hooks/useScreenBack";
import { backToOrigin } from "../lib/screen-back";

interface RouterState {
  photo?: File;
  mealType?: MealType;
  /** DRF-2349 — откуда вошли в поток; едет по всем его шагам. */
  returnTo?: string;
}

type ProcessingState =
  | { kind: "scanning" }
  | { kind: "showCancel" }
  | { kind: "error"; err: unknown };

const SHOW_CANCEL_AFTER_MS = 3000;
const TIMEOUT_MS = 10000;

export function FoodScannerProcessingScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом. Своей стрелки экран не рисует
  // (распознавание идёт секунды и его отменяют кнопкой «Отменить»), но
  // аппаратная кнопка MAX существует независимо от разметки — и без
  // объявления увела бы из приложения. Объявление обязательно и здесь.
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  // DRF-2349 — происхождение пришло из съёмки; возврат и все переходы
  // потока везут его дальше, иначе выход уйдёт на Главную.
  const onBack = useScreenBack(backToOrigin(location.state, "/customer/main"));
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
      state: { photo, mealType, returnTo: state.returnTo },
    });
  }, [navigate, photo, mealType, state.returnTo]);

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
      if (controller.signal.aborted) return;
      setPhase((p) => (p.kind === "scanning" ? { kind: "showCancel" } : p));
    }, SHOW_CANCEL_AFTER_MS);

    timeoutTimerRef.current = window.setTimeout(() => {
      // Guard against setState-after-unmount: if effect cleanup
      // already ran, the controller is aborted; bail before touching
      // state (adversarial CR P7).
      if (controller.signal.aborted) return;
      controller.abort();
      setPhase({ kind: "error", err: new NutritionUnavailableError() });
    }, TIMEOUT_MS);

    (async () => {
      try {
        const result = await scanPhoto(photo, { signal: controller.signal });
        if (controller.signal.aborted) return;
        navigate("/customer/food-scanner/result", {
          replace: true,
          state: { result, photo, mealType, previewUrl, returnTo: state.returnTo },
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        // AbortError surfaces through the same rejection path; treat
        // as silent cancel rather than rendering an error screen.
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- снимок обрабатывается один раз за вход на экран; все пять значений приходят навигацией и на монтировании постоянны
  }, []);

  // ── error branches per spec §7 ─────────────────────────────────────
  if (phase.kind === "error") {
    return (
      <ScanErrorScreen
        returnTo={state.returnTo}
        onBack={onBack}
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
        <div className="food-scanner-processing__dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        {phase.kind === "showCancel" && (
          <>
            <p className="food-scanner-processing__hint">
              Если занимает дольше обычного, можно отменить — фото останется на
              месте.
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
  onBack,
  err,
  photo,
  mealType,
  previewUrl,
  returnTo,
}: {
  /**
   * Возврат приходит готовым от экрана (DRF-1493): экран ошибки
   * живёт внутри `FoodScannerProcessingScreen`, который объявление уже
   * сделал. Свой `useScreenBack` здесь давал вторую подписку на
   * мосту MAX — два перехода на одно нажатие.
   */
  onBack: (() => void) | undefined;
  err: unknown;
  /**
   * DRF-2349 — происхождение приходит свойством по той же причине, что и
   * возврат: экран ошибки живёт внутри потока, а состояние маршрута знает
   * только его хозяин.
   */
  returnTo: string | undefined;
  photo: File | null;
  mealType: MealType;
  previewUrl: string | null;
}) {
  const navigate = useNavigate();
  const isNotRecognized = err instanceof FoodNotRecognizedError;
  const isPhotoFailed = err instanceof PhotoBytesMissingError;
  // Ручки `/api/v1/customer/food/{scan,log,daily}` на боевом контуре
  // отвечают 404, и `food-scanner.ts::guardProd` вне DEV бросает
  // `StubNotWiredError` (тот же признак, что снял вход с главной,
  // DRF-1546). Это НЕ сбой сети и НЕ перегрузка: через минуту ничего не
  // изменится, потому что менять нечего — ручки не существует. Текст
  // «сервис временно недоступен, попробуй через минуту» врал о природе
  // отказа и звал человека возвращаться.
  const isNotWired = err instanceof StubNotWiredError;
  // DRF-2098 — два отказа самого прокси: 413 (лимит один на бота, тот же,
  // что у фото в чате) и отказ ворот дневника в полёте (согласие отозвано
  // между экраном и снимком) — тот же экран согласия, что до снимка.
  const isTooLarge = err instanceof PhotoTooLargeError;
  const isConsentGate =
    err instanceof ApiError &&
    (err.slug === "food_diary_consent_required" ||
      err.slug === "consent_required");
  // DRF-2109 — фото выключено флагом (тот же предикат, что у чата): это не
  // сбой и не «через минуту» — писать текстом работает, туда и ведём.
  const isPhotoOff =
    err instanceof ApiError && err.slug === "photo_scan_disabled";
  // DRF-2195 — два штатных отказа по бюджету распознавания. Каталог работает
  // и отвечает осознанно: «через минуту» здесь было бы ложью о природе отказа
  // (счёт снимется в полночь), а «Переснять» — ложью действием: второй снимок
  // упрётся в тот же счётчик. Рабочая дорога рядом — записать словами.
  // Заголовок и тело лежат парой в одном месте: у отказа по бюджету их два,
  // и регистрировать новый вид отказа в двух разных тернарных лестницах —
  // как раз та дорога, на которой 429 когда-то и потерялся.
  const budget =
    err instanceof ScanDailyLimitError
      ? {
          headline: "Сегодня фото не распознаю",
          body: "Сегодня фото больше не распознаю — напиши словами, что было.",
        }
      : err instanceof ScanBudgetExhaustedError
        ? {
            headline: "Распознавание фото недоступно",
            body: "Распознавание фото сейчас недоступно — напиши словами.",
          }
        : null;
  const isBudget = budget !== null;
  const headline = budget
    ? budget.headline
    : isNotRecognized
      ? "Не разобралась"
      : isPhotoFailed
        ? "Не получилось загрузить"
        : isTooLarge
          ? "Фото слишком большое"
          : isConsentGate
            ? "Нужно разрешение"
            : isPhotoOff
              ? "Фото пока не принимаю"
              : isNotWired
                ? "Пока не подключено"
                : "Сервис недоступен";
  const body = budget
    ? budget.body
    : isNotRecognized
      ? "Фото немного сложное — не разобралась. Можно переснять поближе или просто написать, что было."
      : isPhotoFailed
        ? "Фото пришло, но скачать не получилось — пришли ещё раз, пожалуйста."
        : isTooLarge
          ? "Такое фото не пройдёт — попробуй снять ещё раз или выбрать снимок поменьше."
          : isConsentGate
            ? "Чтобы распознавать еду по фото, нужно разрешение на дневник питания — вернись к сканеру и дай его."
            : isPhotoOff
              ? "Распознавание по фото сейчас выключено. Напиши, что было и сколько граммов, — посчитаю и покажу, прежде чем записать."
              : isNotWired
                ? "Распознавание еды по фото в приложении ещё не работает. Дневник питания сейчас ведёт Ayla в чате."
                : "Сервис распознавания временно недоступен. Попробуй через минуту.";
  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
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
          {/* Когда ручек нет, «Переснять» и «Написать вручную» ведут в ту
              же стену: `logMeal` закрыт тем же `guardProd`. Предлагать их
              значило бы врать второй раз, уже действием. */}
          {!isPhotoFailed && !isNotWired && !isPhotoOff && !isBudget && (
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                navigate("/customer/food-scanner/capture", {
                  replace: true,
                  state: { photo, mealType, returnTo },
                })
              }
            >
              Переснять
            </button>
          )}
          {(isPhotoFailed || isTooLarge || isConsentGate) && (
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                navigate("/customer/food-scanner/capture", { replace: true })
              }
            >
              {isConsentGate ? "К сканеру" : "Сделать заново"}
            </button>
          )}
          {!isPhotoFailed && !isNotWired && !isTooLarge && !isConsentGate && (
            <button
              type="button"
              className={
                isPhotoOff || isBudget ? "btn-primary" : "btn-secondary"
              }
              onClick={() =>
                navigate("/customer/food-scanner/manual", {
                  state: { mealType, returnTo },
                })
              }
            >
              Написать вручную
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={onBack}>
            Назад на главную
          </button>
        </div>
      </main>
    </div>
  );
}
