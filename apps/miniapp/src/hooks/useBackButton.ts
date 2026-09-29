/**
 * Низкий уровень: показать/спрятать аппаратную кнопку MAX и подписать
 * на неё обработчик.
 *
 * # Экраны сюда больше не ходят — им нужен `useScreenBack`
 *
 * Прежний совет из шапки этого файла звучал так:
 * `useBackButton({ onBack: () => navigate(-1) })`. Он и был дефектом
 * DRF-1493: человек попадает в мини-приложение по deep link из бота, и
 * истории переходов у него нет — `navigate(-1)` там либо не делает
 * ничего, либо закрывает приложение. У возврата должно быть ЗАДАННОЕ
 * место, а объявляет его экран через `hooks/useScreenBack` (и
 * обязательное поле `back` у `ScreenLayout`). Клиентское дерево
 * пришпилено `screens/backContract.test.ts`.
 *
 * Правило, которое соблюдает вызывающий: подписка на аппаратную кнопку
 * — РОВНО ОДНА на смонтированное дерево. `onBackButton` копит
 * обработчики, а `setBackButton` — это `show()`/`hide()` без счётчика
 * ссылок: две живые подписки дают два перехода на одно нажатие, а
 * cleanup внутренней прячет кнопку при живой внешней.
 *
 * Per skill-ref Part 6 Pattern 2:
 *   - Show on every screen except root
 *   - Wire to client-side navigation, never close the app silently
 */

import { useEffect } from "react";
import { onBackButton, setBackButton } from "../lib/max-sdk";

interface Options {
  /** If omitted, BackButton is hidden. */
  onBack?: () => void;
}

export function useBackButton(opts: Options = {}): void {
  const { onBack } = opts;
  useEffect(() => {
    if (!onBack) {
      setBackButton(false);
      return;
    }
    setBackButton(true);
    const unsubscribe = onBackButton(onBack);
    return () => {
      unsubscribe();
      setBackButton(false);
    };
  }, [onBack]);
}
