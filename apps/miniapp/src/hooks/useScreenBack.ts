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
 *
 * # РОВНО ОДИН вызов на смонтированное дерево
 *
 * Хук НЕ идемпотентен, и это не мелочь. `onBackButton` в
 * `lib/max-sdk.ts` копит обработчики (`b.onClick(handler)`), а
 * `setBackButton` — это `show()`/`hide()` без счётчика ссылок. Два
 * живых вызова на одном экране дают два перехода на одно нажатие, а
 * когда внутренний размонтируется первым — его cleanup зовёт `hide()`,
 * и кнопка ИСЧЕЗАЕТ при живом внешнем объявлении, потому что
 * перезапускать эффект внешнему нечего. То есть ровно тот дефект,
 * который DRF-1493 и закрывает.
 *
 * Поэтому объявление делает ЭКРАН, а его внутренние части получают
 * готовый обработчик пропом. Так сделаны `ConsentGate` в
 * `FoodScannerCaptureScreen` и `ScanErrorScreen` в
 * `FoodScannerProcessingScreen` — оба сначала звали хук сами.
 */

import { useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";

import type { BackIntent } from "../lib/screen-back";
import { useBackButton } from "./useBackButton";

export function useScreenBack(back: BackIntent): (() => void) | undefined {
  const navigate = useNavigate();

  // Объект намерения создаётся в теле экрана заново на каждый рендер,
  // поэтому зависимости — примитивы, а не сам объект: иначе
  // `useBackButton` переподписывался бы каждый рендер.
  const kind = back.kind;
  const to = back.kind === "up" ? back.to : undefined;
  const action = back.kind === "action" ? back.onBack : undefined;

  // Действие читается через ссылку, а не через зависимость.
  //
  // `backByAction(fn)` вызывается в теле рендера, и `fn` там свободно
  // бывает и нестабильной (инлайновая стрелка), и стабильной, но
  // пересоздаваемой на смену состояния — как `backToCapture` в
  // `FoodScannerResultScreen`, зависящий от выбранного приёма пищи. В
  // зависимостях `useMemo` любой такой случай означал бы
  // `hide()`/`show()` и переподписку на мосту MAX при каждом рендере,
  // то есть моргающую кнопку. Возвращаемый обработчик стабилен, а
  // зовёт всегда последнее объявленное действие.
  const actionRef = useRef(action);
  actionRef.current = action;

  const onBack = useMemo(() => {
    if (kind === "root") return undefined;
    if (kind === "action") return () => actionRef.current?.();
    if (to === undefined) return undefined;
    return () => navigate(to);
  }, [kind, to, navigate]);

  useBackButton({ onBack });

  return onBack;
}
