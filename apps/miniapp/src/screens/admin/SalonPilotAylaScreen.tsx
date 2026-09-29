/**
 * «Ayla» — третий раздел пилотной салонной админки (DRF-1235 → DRF-2119).
 *
 * Адрес: `/admin/ayla`. Принцип владельца, §50 п.5: «Ayla — помощник
 * администратора». Диалог с ассистентом салона — не список клиентских
 * переписок и не лента уведомлений.
 *
 * # Половина А (DRF-2119)
 *
 * Тройка `admin/assistant/{history,ask,confirm}` — админский субъект на
 * том же цикле, что у мастера. Три инструмента: найти запись (карточки
 * дня, без телефонов), подготовить запись (черновик → форма
 * `/admin/booking/new` с предзаполнением; запись создаёт человек в
 * форме), подготовить изменение графика (предложение → «Изменить
 * график» → подтверждение на сервере). «Проверить свободное время» —
 * половина Б, после DRF-1637; сюда не заходит.
 *
 * # Каркас общий
 *
 * `AylaChat` — тот же, что у `MasterAylaScreen`; здесь — только чей это
 * ассистент и куда ведёт дверь черновика (`onOpen` → навигация).
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { AylaChat, type AylaChatApi } from "../../components/AylaChat";
import {
  askAdminAyla,
  confirmAdminAylaAction,
  getAdminAylaHistory,
  type MeResponse,
} from "../../lib/admin-api";
import { SalonPilotFrame } from "./SalonPilotFrame";

const GREETING =
  "Спросите про записи салона, попросите подготовить запись или изменить график мастера.";

export function SalonPilotAylaScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const api = useMemo<AylaChatApi>(
    () => ({
      history: () => getAdminAylaHistory(),
      ask: (text) => askAdminAyla(text),
      confirm: (token) => confirmAdminAylaAction(token),
    }),
    [],
  );

  return (
    <SalonPilotFrame me={me} title="Ayla">
      <div className="ayla-screen ayla-screen--salon">
        <AylaChat
          api={api}
          greeting={GREETING}
          logLabel="Диалог салона с Ayla"
          onOpen={(url) => navigate(url)}
        />
      </div>
    </SalonPilotFrame>
  );
}
