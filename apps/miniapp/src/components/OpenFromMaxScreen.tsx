/**
 * «Открой Ayla из MAX» — единственный исход без валидного initData (DRF-1893).
 *
 * Решение владельца (раздел U, канон 1319-D): Mini App работает только из MAX
 * с валидным initData. Пустой, испорченный, чужой или просроченный initData —
 * не «гость» и не «ошибка сети», а отказ транспорта. Регистрации, логина и
 * OAuth нет; повтор внутри Mini App не поможет, поэтому кнопки «Попробовать
 * снова» здесь нет — только возврат в MAX.
 *
 * Заголовок — формулировка владельца. Тело и кнопка — рабочие, на
 * подтверждение владельцу; обращение на «вы», как в остальной копии Mini App.
 */

import { ScreenLayout } from "./ScreenLayout";
import { OPEN_FROM_MAX_COPY } from "../lib/auth-error-copy";
import { useState } from "react";

import { returnToChat } from "../lib/max-sdk";
import { ReturnToChatHint } from "./ReturnToChatHint";
import { screenRoot } from "../lib/screen-back";

const BACK = screenRoot(
  "Экран отказа транспорта стоит вместо всего приложения: без initData " +
    "внутри Mini App идти некуда, выход один — вернуться в MAX.",
);

/** «Вернуться в MAX» — одним путём с остальными дверями в чат (DRF-2268). */
function useBackToMax(): [boolean, () => void] {
  const [stuck, setStuck] = useState(false);
  return [stuck, () => setStuck(returnToChat() === "stuck")];
}

/** Тело отказа — для экранов, которые рисуют его внутри своей раскладки. */
export function OpenFromMaxBody() {
  const [stuck, back] = useBackToMax();
  return (
    <div className="hello-error" role="alert">
      <p style={{ margin: 0, fontWeight: 600 }}>{OPEN_FROM_MAX_COPY.title}</p>
      <p style={{ margin: "var(--s-1) 0 0" }}>{OPEN_FROM_MAX_COPY.body}</p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button type="button" className="btn-secondary" onClick={back}>
          {OPEN_FROM_MAX_COPY.action}
        </button>
        {stuck && <ReturnToChatHint />}
      </div>
    </div>
  );
}

/** Полный экран — на месте всего приложения. */
export function OpenFromMaxScreen() {
  const [stuck, back] = useBackToMax();
  return (
    <ScreenLayout back={BACK} title={OPEN_FROM_MAX_COPY.title}>
      <p>{OPEN_FROM_MAX_COPY.body}</p>
      <button type="button" className="btn-secondary" onClick={back}>
        {OPEN_FROM_MAX_COPY.action}
      </button>
      {stuck && <ReturnToChatHint />}
    </ScreenLayout>
  );
}
