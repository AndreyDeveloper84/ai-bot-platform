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
 * Вид — по макету владельца «Чат с Ayla.png» (DRF-2953): шапка вложенного
 * экрана со стрелкой и дверью в профиль, лента — `CustomerAylaChat`. До
 * макета экран носил общий `AylaChat` мастера; разговор от смены вида не
 * изменился: те же клиентские `assistant/history` и `assistant/ask`. Сервер
 * отвечает тем же ходом, что в чате бота, — та же безопасность и та же нить,
 * поэтому «Последняя тема» на Главной и история здесь — один и тот же разговор.
 *
 * Нижней панели здесь нет: чат — вложенный экран с возвратом на Главную, а
 * состав панели закреплён (пятая вкладка — «Профиль»). Вкладка «Ayla» с
 * макета — отдельное решение о составе панели.
 */

import { useNavigate } from "react-router-dom";

import { CustomerAvatarEntry } from "../components/CustomerAvatarEntry";
import { CustomerAylaChat } from "../components/CustomerAylaChat";
import { RecordIcon } from "../components/records/RecordIcons";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

const GREETING = "Напиши, чем помочь, — продолжим здесь.";

export function CustomerAylaScreen() {
  const onBack = useScreenBack(backTo("/customer/main"));
  // DRF-2885 — кнопки бота «открыть Mini App» здесь ведут на экран внутри него.
  const navigate = useNavigate();
  return (
    <div className="cr-screen cchat-screen">
      <header className="cr-topbar cchat-topbar">
        <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
          <RecordIcon name="back" size={24} />
        </button>
        <div className="cchat-title">
          <h1 className="cchat-title__name">
            Ayla
            <span className="cchat-title__mark" aria-hidden="true">
              <RecordIcon name="ayla" size={20} />
            </span>
          </h1>
          <p className="cchat-title__sub">Запись, уход и питание</p>
        </div>
        <CustomerAvatarEntry />
      </header>

      <main className="cchat-main">
        <CustomerAylaChat greeting={GREETING} onOpen={(route) => navigate(route)} />
      </main>
    </div>
  );
}
