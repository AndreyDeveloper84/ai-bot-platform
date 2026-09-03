/**
 * Первый экран клиента (DRF-1451).
 *
 * Решение владельца 03.09.2026 (поправка A-1 к BOT-001, §24): клиент,
 * впервые открывающий мини-приложение, попадает **на анкету**. До этого
 * он попадал на приветствие, а вопрос о цели встречал в лучшем случае
 * карточкой на домашнем экране.
 *
 * # Решает сервер, а не этот экран
 *
 * Здесь нет ни одной эвристики «новый / не новый». Есть ровно одно
 * чтение серверного документа: `missing` непуст — спрашивать есть что,
 * значит человек идёт на поверхность цели; пуст — идёт на домашний
 * экран. Тот же приём и тот же инвариант, что у `GoalInviteCard`,
 * который так работает с 31.08.
 *
 * Своей последовательности вопросов у экрана нет и быть не может: он
 * даже не монтирует вопросы сам, а отдаёт всё той же `GoalSelectScreen`
 * (non-goal #5 BOT-001, «No independent Mini App conversational
 * implementation», владельцем НЕ отменён — условие C-1 поправки).
 *
 * # Почему поверхность цели рисуется на месте, а не редиректом
 *
 * `/` — корень. Уехать отсюда `Navigate`-ом на `/customer/goal-select`
 * значило бы либо оставить в истории `/`, куда «назад» вернёт и откуда
 * этот же экран отправит обратно (петля), либо заменить её `replace`-ом
 * и получить кнопку «назад», которой некуда идти. Поэтому поверхность
 * монтируется прямо здесь, а `GoalSelectScreen` сама прячет «назад» на
 * корне — канон `useBackButton`: показывать везде, кроме корня.
 *
 * # Один круг к серверу, не два
 *
 * Документ, по которому принято решение, уезжает в `GoalSelectScreen`
 * пропом `initialDoc`. Иначе поверхность запросила бы тот же
 * decision-context второй раз — на первом экране, в самом дорогом
 * месте. Решений это не добавляет: документ тот же.
 *
 * # Сбой не запирает человека
 *
 * `decision-context` не ответил — рисуем прежний `HelloScreen` целиком,
 * со всей его разборчивой копией на случай протухшей сессии. Анкета
 * необязательна; вход в приложение — нет.
 */

import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { ScreenLayout } from "../components/ScreenLayout";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { signalReady } from "../lib/max-sdk";
import { GoalSelectScreen } from "./GoalSelectScreen";
import { HelloScreen } from "./HelloScreen";

type State =
  | { kind: "loading" }
  | { kind: "ask"; doc: DecisionContext }
  | { kind: "home" }
  | { kind: "fallback" };

export function CustomerEntryScreen() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    // MAX ждёт ready от первого экрана независимо от того, какая ветка
    // выиграет ниже — иначе часть входов осталась бы без сигнала.
    signalReady();
    let cancelled = false;
    fetchDecisionContext()
      .then((doc) => {
        if (cancelled) return;
        setState(doc.missing.length > 0 ? { kind: "ask", doc } : { kind: "home" });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "fallback" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Помощник студии">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  // Документ отдаём готовым: поверхность не должна ходить за ним второй
  // раз на первом же экране.
  if (state.kind === "ask") return <GoalSelectScreen initialDoc={state.doc} />;
  if (state.kind === "home") return <Navigate to="/customer/main" replace />;
  return <HelloScreen />;
}
