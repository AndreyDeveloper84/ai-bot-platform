/**
 * Кадр C04.1 «Моё лучшее направление» в приложении (DRF-1769, К-3 N3).
 *
 * Экран ничего не решает и ничего не собирает: он рисует ЗАПИСЬ,
 * которую человек уже увидел в чате (`lib/recommendation-card.ts`).
 * Поэтому здесь нет ни правил сборки причин, ни таблицы направлений —
 * и R11 («ни услуги, ни мастера, ни цены, ни слота») держится по
 * построению: в записи этого нет, и рисовать нечего.
 *
 * Три кадра одного экрана, как в макете DRF-1270:
 * C04.1 — направление и причины; C04.3 — раскрытие причин («Почему»);
 * C04.2 — другие подходы («Другой вариант»). Четвёртый, C04.4, — не
 * кадр этого экрана, а другое состояние записи (`kind: "absence"`), и
 * говорится он словами владельца, общими с DM.
 *
 * «Подобрать вариант» ведёт в поток записи СРАЗУ, без второго
 * подтверждения: человек пришёл сюда по ссылке из чата, и путь до
 * подбора обязан быть в один тап — иначе кадр читается как тупик.
 * До DRF-2178 кнопка вела в каталог: шага «подходящий вариант» не
 * существовало, и каталог был единственным честным продолжением.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ScreenLayout } from "../components/ScreenLayout";
import { ApiError } from "../lib/api";
import { OPTION_ROUTE } from "../lib/booking-flow";
import { returnToChat } from "../lib/max-sdk";
import { NO_VERIFIED_EVIDENCE_TEXT } from "../lib/recommendation-absence";
import {
  ALT_HEAD,
  ALT_OTHERS_HEAD,
  ALT_PRIMARY_HEAD,
  BUTTON_ALT,
  BUTTON_PICK,
  BUTTON_SKIP,
  BUTTON_WHY,
  CARD_HEAD,
  GONE_TEXT,
  MAX_REASONS,
  WHY_HEAD,
  WHY_MORE_HEAD,
  fetchRecommendation,
  type RecommendationCard,
} from "../lib/recommendation-card";
import { backByAction } from "../lib/screen-back";

/** Какой кадр карточки открыт. Один экран, три состояния макета. */
type Frame = "card" | "why" | "alternatives";

export function RecommendationCardScreen() {
  const { recommendationId } = useParams<{ recommendationId: string }>();
  const navigate = useNavigate();
  const [card, setCard] = useState<RecommendationCard | null>(null);
  const [gone, setGone] = useState(false);
  const [frame, setFrame] = useState<Frame>("card");

  useEffect(() => {
    if (!recommendationId) return;
    let alive = true;
    fetchRecommendation(recommendationId)
      .then((loaded) => {
        if (alive) setCard(loaded);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        // 404 — чужая, стёртая или несуществующая запись, одним ответом.
        // Всё остальное — тоже не повод показать пустой экран: человек
        // пришёл по ссылке и обязан услышать что-то, а не ничего.
        if (!(error instanceof ApiError)) console.error(error);
        setGone(true);
      });
    return () => {
      alive = false;
    };
  }, [recommendationId]);

  // Возврат — заданное действие, а не смещение по истории (DRF-1493):
  // человек пришёл сюда по ссылке из чата, и переходов у него нет. Туда
  // же и возвращаем — в чат, откуда карточка пришла; вне MAX закрывать
  // нечего, и тогда это Главная.
  const back = backByAction(
    useCallback(() => {
      // DRF-2268: в чат — returnToChat; не вышло — Главная.
      if (returnToChat() === "stuck") navigate("/customer/main");
    }, [navigate]),
  );

  if (gone) {
    return (
      <ScreenLayout back={back}>
        <p className="callout" role="status">
          {GONE_TEXT}
        </p>
      </ScreenLayout>
    );
  }

  if (card === null) {
    return (
      <ScreenLayout back={back}>
        <p role="status" aria-live="polite">
          Загружаю…
        </p>
      </ScreenLayout>
    );
  }

  // C04.4 — не кадр карточки, а её отсутствие: те же слова владельца,
  // что человек услышал бы в чате. Два действия, которые работают.
  if (card.kind === "absence") {
    return (
      <ScreenLayout back={back}>
        <p className="callout" role="status">
          {NO_VERIFIED_EVIDENCE_TEXT}
        </p>
        <button type="button" className="btn-primary" onClick={() => navigate("/customer/catalog")}>
          Посмотреть услуги
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => navigate("/customer/goal-select")}
        >
          Уточнить запрос
        </button>
      </ScreenLayout>
    );
  }

  const reasons = card.why.slice(0, MAX_REASONS);

  if (frame === "why") {
    return (
      <ScreenLayout back={back}>
        <h1>{WHY_MORE_HEAD}</h1>
        <ul>
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
        <button type="button" className="btn-secondary" onClick={() => setFrame("card")}>
          Назад к направлению
        </button>
      </ScreenLayout>
    );
  }

  if (frame === "alternatives") {
    return (
      <ScreenLayout back={back}>
        <p>{ALT_HEAD}</p>
        <h2>{ALT_PRIMARY_HEAD}</h2>
        <p>{card.what}</p>
        {card.subline ? <p>{card.subline}</p> : null}
        <h2>{ALT_OTHERS_HEAD}</h2>
        {/* «Показать больше подходов» здесь нет: больше двух не бывает
            по построению, и кнопка обещала бы продолжение. */}
        <ul>
          {card.alternatives.map((alt) => (
            <li key={alt.what}>
              <span>{alt.what}</span>
              {alt.subline ? <span>{alt.subline}</span> : null}
            </li>
          ))}
        </ul>
        <button type="button" className="btn-secondary" onClick={() => setFrame("card")}>
          Назад к направлению
        </button>
      </ScreenLayout>
    );
  }

  return (
    <ScreenLayout back={back}>
      <p>{CARD_HEAD}</p>
      <h1>{card.what}</h1>
      {card.subline ? <p>{card.subline}</p> : null}

      <h2>{WHY_HEAD}</h2>
      <ul>
        {reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>

      <button type="button" className="btn-primary" onClick={() => navigate(OPTION_ROUTE)}>
        {BUTTON_PICK}
      </button>
      <button type="button" className="btn-secondary" onClick={() => setFrame("why")}>
        {BUTTON_WHY}
      </button>
      {/* Кнопки «Другой вариант» нет, когда подходов нет: она открыла бы
          кадр, в котором нечего выбирать. */}
      {card.alternatives.length > 0 ? (
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setFrame("alternatives")}
        >
          {BUTTON_ALT}
        </button>
      ) : null}
      <button
        type="button"
        className="btn-secondary"
        onClick={() => {
          // DRF-2268: в чат — returnToChat; не вышло — Главная.
          if (returnToChat() === "stuck") navigate("/customer/main");
        }}
      >
        {BUTTON_SKIP}
      </button>
    </ScreenLayout>
  );
}
