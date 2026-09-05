/**
 * F3 Recognition Result + Edit + F3-Clarify Modal — Customer Food Scanner.
 *
 * Route: `/customer/food-scanner/result` (router state from F2 carries
 * `{ result: ScanResponse, photo: File, mealType, previewUrl }`).
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §4 (F3 high/low
 * conf) + §5 (F3-Clarify modal) + §10 (voice rules).
 *
 * # ED-mode rendering (critical)
 *
 * If `health_flags.eating_disorder === true` OR `result.nutrition === null`
 * (Ayla omits numbers in ED mode), hide all numeric nutrition values.
 * Render text-only «Примерно». Portion ± buttons still functional but
 * suppress updated number display.
 *
 * # Beauty insights null-safety
 *
 * `result.beauty_insights` MAY be null — render nothing for that
 * section, never crash. When present, render the 3-field block (vitamin
 * deficits / beauty_impact / recommendation) below the primary CTAs.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useScreenBack } from "../hooks/useScreenBack";
import { backByAction } from "../lib/screen-back";

import { Snackbar } from "../components/Snackbar";
import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  PORTION_STEPS,
  fetchHealthFlags,
  logMeal,
  nextPortion,
  type MealType,
  type ScanResponse,
} from "../lib/food-scanner";

interface RouterState {
  result?: ScanResponse;
  photo?: File;
  mealType?: MealType;
  previewUrl?: string;
}

const MEAL_TYPES: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

type ClarifyPath = "rename" | "portion" | "rephoto";

export function FoodScannerResultScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  const result = state.result;
  const photo = state.photo;
  const initialMealType = state.mealType ?? "lunch";
  const previewUrl = state.previewUrl;

  const [mealType, setMealType] = useState<MealType>(initialMealType);
  // Возврат (DRF-1493) — к съёмке, с восстановлением уже сделанного
  // снимка: без него человек, вернувшийся посмотреть на кадр,
  // фотографировал бы заново. Поэтому не адрес, а заданное действие —
  // но всё так же не `history.back()`.
  const backToCapture = useCallback(
    () =>
      navigate("/customer/food-scanner/capture", {
        replace: true,
        state: { photo, mealType },
      }),
    [navigate, photo, mealType],
  );
  const onBack = useScreenBack(backByAction(backToCapture));
  const [portionMultiplier, setPortionMultiplier] = useState<number>(1.0);
  const [dishName, setDishName] = useState<string>(result?.dish_name ?? "");
  const [editingName, setEditingName] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  // ED-mode initial = true (fail-safe per adversarial CR P1).
  // Spec §10 Appendix mandates UI MUST hide numeric nutrition for
  // customers with eating_disorder flag. Initializing to `false` and
  // letting `fetchHealthFlags()` resolve asynchronously creates a race
  // window where ED-customer sees calorie numbers before the flag
  // arrives. We default to «hide», then `flagsResolved=true` flips ON
  // ONLY if the customer is explicitly NOT in ED mode.
  const [edMode, setEdMode] = useState<boolean>(true);
  const [flagsResolved, setFlagsResolved] = useState<boolean>(false);
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [snack, setSnack] = useState<{ visible: boolean; message: string }>({
    visible: false,
    message: "",
  });
  const clarifyTriggerRef = useRef<HTMLButtonElement | null>(null);
  const portionRowRef = useRef<HTMLDivElement | null>(null);

  // Read health_flags once — ED mode also implied by null nutrition.
  // Default is `edMode=true` (fail-safe); flip OFF only after the
  // fetch confirms the customer is NOT in ED mode. If the fetch fails
  // we stay in safe mode + keep numbers hidden — the cost is one
  // user-visible «Примерно — записала.» instead of calories.
  useEffect(() => {
    let cancelled = false;
    fetchHealthFlags()
      .then((flags) => {
        if (cancelled) return;
        setEdMode(Boolean(flags.health_flags.eating_disorder));
        setFlagsResolved(true);
      })
      .catch(() => {
        if (cancelled) return;
        // Keep edMode=true on failure (defence-in-depth).
        setFlagsResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Defensive: missing state means deep-link refresh; bounce to F1.
  useEffect(() => {
    if (!result) {
      navigate("/customer/food-scanner/capture", { replace: true });
    }
  }, [result, navigate]);

  if (!result) return null;

  // hideNumbers = true while flags are loading (fail-safe), then driven
  // by either the explicit flag OR a null nutrition payload (Ayla omits
  // numbers in ED mode by contract). Either condition suffices.
  const hideNumbers =
    !flagsResolved || edMode || result.nutrition === null;
  const portionGrams =
    result.portion_g != null
      ? Math.round(result.portion_g * portionMultiplier)
      : null;
  const calories = result.nutrition
    ? Math.round(result.nutrition.calories * portionMultiplier)
    : null;
  const proteinG = result.nutrition
    ? round1(result.nutrition.protein_g * portionMultiplier)
    : null;
  const fatG = result.nutrition
    ? round1(result.nutrition.fat_g * portionMultiplier)
    : null;
  const carbsG = result.nutrition
    ? round1(result.nutrition.carbs_g * portionMultiplier)
    : null;
  const isLowConf = result.confidence < 0.6;
  const leadVerb = isLowConf ? "Похоже на" : "Узнала";

  const onSave = useCallback(async () => {
    // Decide name override by comparing current input vs original
    // result — NOT by the `editingName` sticky flag (adversarial CR P2).
    // Previously: opening F3-Clarify > Rename and tapping save without
    // actually editing dropped scan_id permanently, losing the
    // recognition link in analytics. Compare strings → only drop
    // scan_id when the customer really renamed the dish.
    const trimmed = dishName.trim();
    const renamed =
      trimmed.length > 0 && trimmed !== result.dish_name;
    setBusy(true);
    try {
      await logMeal({
        scan_id: renamed ? undefined : result.scan_id,
        dish_name: renamed ? trimmed : undefined,
        meal_type: mealType,
        portion_multiplier: portionMultiplier,
        note: note.trim() || undefined,
      });
      navigate("/customer/food-scanner/saved", {
        replace: true,
        state: {
          dishName: renamed ? trimmed : result.dish_name,
          calories,
          edMode: hideNumbers,
        },
      });
    } catch {
      setSnack({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setBusy(false);
    }
  }, [
    result,
    mealType,
    portionMultiplier,
    note,
    dishName,
    calories,
    hideNumbers,
    navigate,
  ]);

  const onReject = useCallback(() => {
    // Disable all CTAs during the 1800ms toast → navigate window
    // (adversarial CR P11 — without this, a fast «Записать» tap fires
    // a real logMeal in parallel with the silent reject + navigate,
    // leaving an unwanted diary entry).
    setBusy(true);
    setSnack({
      visible: true,
      message: "Поняла, не записываю. Если хочешь — пришли ещё фото.",
    });
    window.setTimeout(() => navigate("/customer/main"), 1800);
  }, [navigate]);

  const openClarify = useCallback(() => setClarifyOpen(true), []);
  const closeClarify = useCallback(() => {
    setClarifyOpen(false);
    clarifyTriggerRef.current?.focus();
  }, []);

  const handleClarifyChoice = useCallback(
    (path: ClarifyPath) => {
      setClarifyOpen(false);
      if (path === "rename") {
        setEditingName(true);
        // Defer to next tick so the input is mounted.
        window.setTimeout(() => {
          const el = document.getElementById("food-dish-rename");
          if (el && "focus" in el) (el as HTMLInputElement).focus();
        }, 0);
        return;
      }
      if (path === "portion") {
        window.setTimeout(() => {
          portionRowRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
        }, 0);
        return;
      }
      // rephoto
      navigate("/customer/food-scanner/capture", {
        state: { mealType, photo: null },
      });
    },
    [mealType, navigate],
  );

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
        <h1 className="records-screen__title">Распознанное</h1>
      </header>

      <main className="food-scanner-screen__main">
        {previewUrl && (
          <div className="food-scanner-result__photo-row">
            <img
              src={previewUrl}
              alt="Фото блюда"
              className="food-scanner-result__photo"
            />
          </div>
        )}

        <h2 className="food-scanner-result__lead">
          {leadVerb}:{" "}
          {editingName ? (
            <input
              id="food-dish-rename"
              type="text"
              className="food-scanner-result__name-input"
              value={dishName}
              onChange={(e) => setDishName(e.target.value)}
              aria-label="Название блюда"
            />
          ) : (
            <span>{dishName}</span>
          )}
        </h2>

        {isLowConf && (
          <p className="food-scanner-result__hedge">
            Прикинула приблизительно — давай уточним вместе.
          </p>
        )}

        <section
          className="food-scanner-result__metrics"
          aria-labelledby="food-result-metrics-h2"
        >
          <h3
            id="food-result-metrics-h2"
            className="food-scanner-screen__section-heading"
          >
            Примерно
          </h3>
          <div
            ref={portionRowRef}
            className="food-scanner-result__portion-row"
          >
            <span className="food-scanner-result__portion-label">
              Порция:{" "}
              {portionGrams != null ? `${portionGrams} г` : "—"}
            </span>
            <div className="food-scanner-result__portion-controls">
              <button
                type="button"
                className="food-scanner-result__portion-btn"
                aria-label="Уменьшить порцию"
                disabled={portionMultiplier <= PORTION_STEPS[0]!}
                onClick={() =>
                  setPortionMultiplier((p) => nextPortion(p, "down"))
                }
              >
                −
              </button>
              {/*
                aria-live moved from this percent indicator to the
                nutrition block below per spec §11.6 + adversarial CR
                P8 — screen readers were announcing duplicate strings
                («100%», then «Калории 480»). Composite announcement
                lives on the metrics block so NVDA reads one coherent
                sentence per portion change.
               */}
              <span className="food-scanner-result__portion-value">
                {Math.round(portionMultiplier * 100)}%
              </span>
              <button
                type="button"
                className="food-scanner-result__portion-btn"
                aria-label="Увеличить порцию"
                disabled={
                  portionMultiplier >=
                  PORTION_STEPS[PORTION_STEPS.length - 1]!
                }
                onClick={() =>
                  setPortionMultiplier((p) => nextPortion(p, "up"))
                }
              >
                +
              </button>
            </div>
          </div>
          {!hideNumbers && calories != null && (
            <div
              className="food-scanner-result__nutrition"
              role="status"
              aria-live="polite"
              aria-label={`Примерно ${
                portionGrams ?? ""
              } граммов, ${calories} килокалорий. Белки ${proteinG}, жиры ${fatG}, углеводы ${carbsG} граммов.`}
            >
              <p className="food-scanner-result__calories" aria-hidden="true">
                Калории: ~{calories} ккал
              </p>
              <p className="food-scanner-result__macros" aria-hidden="true">
                Б {proteinG} · Ж {fatG} · У {carbsG} г
              </p>
            </div>
          )}
          {hideNumbers && (
            <p
              className="food-scanner-result__ed-note"
              role="status"
              aria-live="polite"
            >
              Примерно — записала.
            </p>
          )}
        </section>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-result-mealtype-h3"
        >
          <h3
            id="food-result-mealtype-h3"
            className="food-scanner-screen__section-heading"
          >
            Когда
          </h3>
          <div
            className="food-scanner-screen__meal-chips"
            role="radiogroup"
            aria-labelledby="food-result-mealtype-h3"
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
          aria-labelledby="food-result-note-h3"
        >
          <h3
            id="food-result-note-h3"
            className="food-scanner-screen__section-heading"
          >
            Заметка
          </h3>
          <textarea
            className="food-scanner-result__note"
            placeholder="(необязательно)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            aria-label="Заметка к записи"
          />
        </section>

        {/*
          Beauty insights block intentionally NOT rendered for pilot.
          Spec §6 + Q-BACK-4 verdict 2026-05-25 + memory
          `project_cross_domain_insight_safety_gap` — cross-domain
          insight cards REMOVED from MVP. Anti-medical safety filter is
          not in production yet; sample rule («vit-D deficit 5d → argan
          oil massage») is already medical-adjacent architecturally.
          Re-introduce post-pilot after Alpha safety audit + content
          gates. The `beauty_insights` field in the contract stays so
          backend can pre-wire; frontend ignores it for now.
         */}

        <div className="food-scanner-screen__cta-stack">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={onSave}
          >
            Записать в дневник
          </button>
          <button
            ref={clarifyTriggerRef}
            type="button"
            className={`btn-secondary${
              isLowConf ? " food-scanner-result__cta--hint" : ""
            }`}
            onClick={openClarify}
          >
            Уточнить
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onReject}
          >
            Не то
          </button>
        </div>
      </main>

      {clarifyOpen && (
        <ClarifyModal
          onChoose={handleClarifyChoice}
          onClose={closeClarify}
        />
      )}

      <Snackbar
        visible={snack.visible}
        message={snack.message}
        durationMs={3000}
        onTimeout={() => setSnack({ visible: false, message: "" })}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// F3-Clarify modal — 3 correction paths per spec §5.
// ---------------------------------------------------------------------------

function ClarifyModal({
  onChoose,
  onClose,
}: {
  onChoose: (path: ClarifyPath) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const firstBtnRef = useRef<HTMLButtonElement | null>(null);
  const cancelBtnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancelBtnRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="profile-support-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="profile-support-sheet food-scanner-clarify"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <h2 id={titleId} className="profile-support-sheet__headline">
          Что поправить?
        </h2>
        <div className="food-scanner-clarify__choices">
          <button
            ref={firstBtnRef}
            type="button"
            className="btn-secondary food-scanner-clarify__choice"
            onClick={() => onChoose("rename")}
          >
            Название блюда
          </button>
          <button
            type="button"
            className="btn-secondary food-scanner-clarify__choice"
            onClick={() => onChoose("portion")}
          >
            Вес / порцию
          </button>
          <button
            type="button"
            className="btn-secondary food-scanner-clarify__choice"
            onClick={() => onChoose("rephoto")}
          >
            Сделать новое фото
          </button>
        </div>
        <button
          ref={cancelBtnRef}
          type="button"
          className="btn-secondary food-scanner-clarify__cancel"
          onClick={onClose}
        >
          Отмена
        </button>
      </div>
    </div>
  );
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
