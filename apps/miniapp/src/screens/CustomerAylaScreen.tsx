/**
 * Разговор с Ayla внутри клиентского Mini App (DRF-2799).
 *
 * Адрес: `/customer/ayla`
 *
 * Решение владельца 06.10.2026 (замещает Д2 §172 / DRF-2266 для клиента):
 * «диалог продолжается там, где начат». Кнопки «Продолжить разговор» и
 * «Задать новый вопрос» на Главной больше не закрывают Mini App и не уводят
 * в чат бота — они ведут сюда.
 *
 * Экран — обёртка общего `AylaChat` в той же разметке, что мастерский раздел
 * «Ayla» (`MasterAylaScreen`): макета нет, решение владельца — тот же
 * компонент без новых элементов. Разница только в том, чей это разговор:
 * клиентские `assistant/history` и `assistant/ask`. Сервер отвечает тем же
 * ходом, что в чате бота, — та же безопасность и та же нить, поэтому
 * «Последняя тема» на Главной и история здесь — один и тот же разговор.
 */

import { AylaChat } from "../components/AylaChat";
import { useScreenBack } from "../hooks/useScreenBack";
import { customerAylaApi } from "../lib/customer-assistant";
import { backTo } from "../lib/screen-back";

const GREETING = "Напиши, чем помочь, — продолжим здесь.";

export function CustomerAylaScreen() {
  useScreenBack(backTo("/customer/main"));
  return (
    <main className="screen ayla-screen">
      <header className="ayla-header">
        <div className="ayla-header__top">
          <h1 className="ayla-header__title">Ayla</h1>
        </div>
        <p className="ayla-header__sub">Запись, уход и питание</p>
      </header>

      <AylaChat api={customerAylaApi} greeting={GREETING} logLabel="Разговор с Ayla" />
    </main>
  );
}
