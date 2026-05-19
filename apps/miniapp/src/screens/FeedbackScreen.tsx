/** F5 — post-visit rating form.
 *
 * Deeplinked from a bot DM ("открыть Mini App"/start_param=feedback_<id>)
 * OR a tap on the "Оценить" button in MyVisits. The booking ID arrives
 * via the URL. 5-star picker + optional comment + Save. Low ratings
 * (≤3) swap copy to a calm "we'll reach out" panel — the rating still
 * persists, the backend has already fired the HUMAN_LOCKED handoff
 * (see apps/booking/services/feedback.py).
 */

import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { StateError } from "../components/StateError";
import { StickyCta } from "../components/StickyCta";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import { submitFeedback, type FeedbackResult } from "../lib/api";

const COMMENT_MAX = 500;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Phase =
  | { kind: "form"; rating: number; comment: string; submitting: boolean; err?: unknown }
  | { kind: "thanks"; result: FeedbackResult };

export function FeedbackScreen() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const navigate = useNavigate();
  const haptics = useHaptics();
  useBackButton({ onBack: () => navigate(-1) });

  const validId = useMemo(() => !!bookingId && UUID_RE.test(bookingId), [bookingId]);

  const [phase, setPhase] = useState<Phase>({
    kind: "form",
    rating: 0,
    comment: "",
    submitting: false,
  });

  const onSubmit = useCallback(async () => {
    if (phase.kind !== "form" || phase.rating === 0 || phase.submitting || !bookingId) return;
    setPhase({ ...phase, submitting: true, err: undefined });
    try {
      const result = await submitFeedback(bookingId, {
        rating: phase.rating,
        comment: phase.comment,
      });
      haptics.notify(result.handoff_created ? "warning" : "success");
      setPhase({ kind: "thanks", result });
    } catch (err) {
      haptics.notify("error");
      setPhase({ ...phase, submitting: false, err });
    }
  }, [phase, bookingId, haptics]);

  if (!validId) {
    return (
      <ScreenLayout title="Оценить визит">
        <div className="callout callout--danger" role="alert">
          Ссылка повреждена — не получилось определить визит.
        </div>
      </ScreenLayout>
    );
  }

  if (phase.kind === "thanks") {
    return <ThankYou result={phase.result} onClose={() => navigate("/visits")} />;
  }

  const canSubmit = phase.rating > 0 && !phase.submitting;

  return (
    <ScreenLayout
      title="Оцените визит"
      cta={
        <StickyCta onClick={onSubmit} disabled={!canSubmit}>
          {phase.submitting ? "Отправляем…" : "Отправить"}
        </StickyCta>
      }
    >
      <p className="feedback-intro">
        Ваша оценка поможет студии сделать визиты ещё приятнее.
      </p>

      <StarPicker
        value={phase.rating}
        onChange={(v) => {
          haptics.selection();
          setPhase((p) => (p.kind === "form" ? { ...p, rating: v } : p));
        }}
      />

      <label className="profile-field">
        <span className="profile-field__label">Комментарий (необязательно)</span>
        <textarea
          className="profile-input profile-input--multiline"
          rows={4}
          maxLength={COMMENT_MAX}
          value={phase.comment}
          placeholder="Расскажите, что понравилось или что улучшить"
          onChange={(e) =>
            setPhase((p) => (p.kind === "form" ? { ...p, comment: e.target.value } : p))
          }
        />
        <span className="profile-field__hint">
          {phase.comment.length}/{COMMENT_MAX}
        </span>
      </label>

      {phase.err !== undefined && (
        <StateError err={phase.err} onRetry={onSubmit} screenId="feedback" />
      )}
    </ScreenLayout>
  );
}

function StarPicker({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  // Roving tabindex: only the selected (or first when empty) star takes
  // tab focus. Arrow + 1-5 keys + Home/End move within the group.
  const onKey = (e: React.KeyboardEvent<HTMLButtonElement>, n: number) => {
    const set = (v: number) => {
      e.preventDefault();
      onChange(Math.max(1, Math.min(5, v)));
    };
    switch (e.key) {
      case "ArrowRight":
      case "ArrowUp":
        set(n + 1);
        return;
      case "ArrowLeft":
      case "ArrowDown":
        set(n - 1);
        return;
      case "Home":
        set(1);
        return;
      case "End":
        set(5);
        return;
      case " ":
      case "Enter":
        set(n);
        return;
    }
    if (e.key >= "1" && e.key <= "5") {
      set(Number(e.key));
    }
  };

  return (
    <div className="star-picker" role="radiogroup" aria-label="Оценка">
      {[1, 2, 3, 4, 5].map((n) => {
        const filled = n <= value;
        const checked = value === n;
        // When value is 0 (nothing selected yet), 1st star is tabbable
        // so keyboard users can land on the group; otherwise only the
        // selected one. Standard roving-tabindex pattern.
        const tabIndex = checked || (value === 0 && n === 1) ? 0 : -1;
        return (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={`${n} из 5`}
            tabIndex={tabIndex}
            className={`star-picker__star${filled ? " star-picker__star--filled" : ""}`}
            onClick={() => onChange(n)}
            onKeyDown={(e) => onKey(e, n)}
          >
            {filled ? "★" : "☆"}
          </button>
        );
      })}
    </div>
  );
}

function ThankYou({ result, onClose }: { result: FeedbackResult; onClose: () => void }) {
  const lowRating = result.rating <= 3;
  return (
    <ScreenLayout
      title={lowRating ? "Спасибо за честность" : "Спасибо за оценку!"}
      cta={<StickyCta onClick={onClose}>К моим записям</StickyCta>}
    >
      <div className={`feedback-thanks${lowRating ? " feedback-thanks--lowrating" : ""}`}>
        <div className="feedback-thanks__stars">{"★".repeat(result.rating)}{"☆".repeat(5 - result.rating)}</div>
        <p className="feedback-thanks__body">
          {lowRating
            ? "Мы передали ваш отзыв в студию — с вами свяжутся в ближайшее время, чтобы разобраться и помочь."
            : "Мы рады, что вам понравилось. Будем ждать вас снова!"}
        </p>
      </div>
    </ScreenLayout>
  );
}
