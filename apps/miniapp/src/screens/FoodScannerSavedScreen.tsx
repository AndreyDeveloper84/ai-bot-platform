/**
 * F4 Saved Confirmation — Customer Food Scanner Phase B.
 *
 * Route: `/customer/food-scanner/saved` (router state from F3 carries
 * `{ dishName, calories, edMode }`).
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §6.
 *
 * # ED-mode rendering
 *
 * If `edMode`, hide all numbers (recap line, daily total, progress
 * bar, macros). Show only «✓ Записала» + dish name + non-numeric
 * dish recap.
 *
 * # No cross-domain insight card
 *
 * Per spec §6 + memory `project_cross_domain_insight_safety_gap` —
 * REMOVED from MVP. Anti-medical safety filter not in production yet.
 * Re-introduce post-pilot after Alpha safety audit.
 *
 * DRF-2954 — вид по доске «Дневник» (кадр 8), решение владельца 12.11 п.7:
 * экран остаётся «Записано», слова прежние; галочка в круге, итог блюда и
 * «Сегодня» карточками. Полоса — только при ориентире (§82, §85).
 */

import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { DiaryLoadError } from "../components/diary/DiaryState";
import { RecordIcon } from "../components/records/RecordIcons";
import { RecordButton } from "../components/records/RecordsState";
import {
  DIARY_OFF_TEXT,
  diaryIsOff,
  getWellnessToday,
  type WellnessToday,
} from "../lib/customer-wellness";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  approxPrefix,
  KCAL_TOTAL_AI_NOTE,
  KCAL_TOTAL_INCOMPLETE,
  KCAL_TOTAL_UNKNOWN,
  kcalTotalMarks,
} from "../lib/format";
import { backToOrigin, originFrom } from "../lib/screen-back";

interface RouterState {
  dishName?: string;
  calories?: number | null;
  edMode?: boolean;
}

export function FoodScannerSavedScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом; адрес прежний, теперь объявленный.
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  // DRF-2349 — последний экран потока: выход ведёт туда, откуда вошли.
  const onBack = useScreenBack(backToOrigin(location.state, "/customer/main"));
  const dishName = state.dishName ?? "Запись";
  const recapCalories = state.calories ?? null;
  // Default ED-mode TRUE — defence-in-depth for deep-link refresh
  // (friendly CR #5). Router state is lost on refresh; if a customer who
  // chose «Без чисел» (DRF-2766; before 04.10 — the eating_disorder flag)
  // hits /saved fresh, we must NOT leak numbers until the diary's own
  // `nutrition_numbers_hidden` says otherwise.
  const [edMode, setEdMode] = useState<boolean>(
    state.edMode === undefined ? true : Boolean(state.edMode),
  );

  const [summary, setSummary] = useState<WellnessToday | null>(null);
  const [err, setErr] = useState<unknown>(null);
  // DRF-2071 — сводка с маркером «контур выключен»: чисел нет и не будет.
  const diaryOff = diaryIsOff(summary);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const s = await getWellnessToday();
      setSummary(s);
      // Признак приходит от источника; отсутствие ключа ПРЯЧЕТ числа —
      // «не смогли спросить» не становится разрешением их показать
      // (§10 Appendix ED Mode). Раньше здесь стояла заглушка, которая
      // на этот вопрос отвечала выдумкой.
      setEdMode(s.nutrition_numbers_hidden !== false);
    } catch (e) {
      setErr(e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Процент считается, ТОЛЬКО когда известны оба числа. Цели нет —
  // нет и шкалы: доля от несуществующей цели это не ноль процентов,
  // это отсутствие ответа (§65).
  const eaten = summary?.calories_eaten;
  const target = summary?.calories_target;
  // DRF-2766 фаза 2 — итог включает оценки ИИ и говорит об этом.
  const marks = kcalTotalMarks(summary ?? {});
  const approx = approxPrefix(marks);
  const progressPct =
    eaten !== undefined && target !== undefined && target > 0
      ? Math.min(100, Math.round((eaten * 100) / target))
      : null;

  return (
    <div className="cr-screen cr-screen--nested diary-screen">
      <header className="cr-topbar diary-topbar">
        <button type="button" className="cr-back" aria-label="На главную" onClick={onBack}>
          <RecordIcon name="back" size={24} />
        </button>
        <h1 className="diary-topbar__title">Записано</h1>
      </header>

      <main className="diary-main">
        <div className="diary-saved__check" role="status">
          <span className="diary-saved__badge">
            <RecordIcon name="check" size={40} />
          </span>
          <span className="diary-saved__check-text">Записала</span>
        </div>

        {edMode ? (
          <p className="diary-saved__recap">{dishName} — записала.</p>
        ) : (
          <p className="diary-saved__recap">
            {dishName}
            {recapCalories != null ? ` — ~${recapCalories} ккал.` : "."}
          </p>
        )}

        {/* DRF-2071 — контур выключили между записью и сводкой: сводка пришла
            с маркером и без чисел (edMode остаётся true — ключа нет). Не сбой,
            повтор ничего не даст; «записала» выше остаётся правдой. */}
        {diaryOff && (
          <p className="diary-caption" role="status">
            {DIARY_OFF_TEXT}
          </p>
        )}

        {!edMode && (
          <section className="diary-totals" aria-labelledby="food-saved-daily-h2">
            <h2 id="food-saved-daily-h2" className="diary-group__heading">
              Сегодня
            </h2>
            {err !== null && <DiaryLoadError err={err} onRetry={load} />}
            {err === null && summary && (
              <>
                {eaten !== undefined && (
                  <p className="diary-totals__total" data-testid="saved-kcal-total">
                    {target !== undefined ? `${approx}${eaten} / ${target} ккал` : `${approx}${eaten} ккал`}
                    {marks.approx ? `, ${KCAL_TOTAL_AI_NOTE}` : ""}
                  </p>
                )}
                {eaten === undefined && marks.incomplete && (
                  <p className="diary-totals__total" data-testid="saved-kcal-total">
                    {KCAL_TOTAL_UNKNOWN}
                  </p>
                )}
                {eaten !== undefined && marks.incomplete && (
                  <p className="diary-totals__note">{KCAL_TOTAL_INCOMPLETE}</p>
                )}
                {progressPct !== null && (
                  <>
                    <div
                      className="diary-bar"
                      role="progressbar"
                      aria-valuenow={progressPct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`Прогресс по калориям: ${progressPct} процентов`}
                    >
                      <div className="diary-bar__fill" style={{ width: `${progressPct}%` }} />
                    </div>
                    <p className="diary-totals__note">{progressPct} %</p>
                  </>
                )}
                {summary.pfc && (
                  <p className="diary-totals__note">
                    Б {summary.pfc.protein_g} · Ж {summary.pfc.fat_g} · У{" "}
                    {summary.pfc.carbs_g} г
                  </p>
                )}
              </>
            )}
          </section>
        )}

        <div className="cr-actions diary-actions">
          {/* DRF-2071 — при выключенном контуре дневник не открывается:
              кнопка вела бы на экран с той же фразой «недоступен». */}
          {!diaryOff && (
            <RecordButton variant="primary" onClick={() => navigate("/customer/food-scanner/diary")}>
              Открыть дневник
            </RecordButton>
          )}
          <RecordButton
            variant="soft"
            onClick={() => navigate(originFrom(location.state) ?? "/customer/main")}
          >
            Готово
          </RecordButton>
        </div>
      </main>
    </div>
  );
}
