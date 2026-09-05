/**
 * Единая точка возврата экрана (DRF-1493).
 *
 * Заводит аппаратную кнопку MAX по объявленному виду экрана и отдаёт
 * обработчик для видимой кнопки, чтобы обе половины возврата вели в
 * одно и то же место. Раньше их было две независимые: `useBackButton`
 * в теле экрана и нарисованная там же стрелка — их можно было завести
 * по-разному, а можно было забыть обе.
 *
 * Возвращает `undefined` ровно для корня: рисовать там нечего и
 * аппаратная кнопка прячется.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import type { BackIntent } from "../lib/screen-back";
import { useBackButton } from "./useBackButton";

export function useScreenBack(back: BackIntent): (() => void) | undefined {
  const navigate = useNavigate();

  // Объект намерения создаётся в теле экрана заново на каждый рендер,
  // поэтому зависимости — примитивы и стабильные функции, а не сам
  // объект: иначе `useBackButton` переподписывался бы каждый рендер.
  const kind = back.kind;
  const to = back.kind === "up" ? back.to : undefined;
  const step = back.kind === "step" ? back.onBack : undefined;

  const onBack = useMemo(() => {
    if (kind === "root") return undefined;
    if (kind === "step") return step;
    if (to === undefined) return undefined;
    return () => navigate(to);
  }, [kind, to, step, navigate]);

  useBackButton({ onBack });

  return onBack;
}
