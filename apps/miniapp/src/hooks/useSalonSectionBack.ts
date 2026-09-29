/**
 * Возврат из раздела, снятого с нижней панели, в «Сегодня» (DRF-2115).
 *
 * Команда, Чаты с мастерами и Настройки были вкладками моста — корнями,
 * которые прятали системную кнопку «назад» MAX. Теперь у владельца и
 * администратора они открываются из аватара, и без возврата человек
 * остался бы в разделе с панелью, которая ведёт куда угодно, только не
 * «назад». Одна механика на все три: показать системную кнопку и вести
 * на посадку пилота. Ресепшн — по-прежнему корень моста: кнопки нет.
 *
 * «Услуги» сюда не ходят: у экрана свой обработчик с подтверждением
 * несохранённых правок (`AdminServicesMatrixScreen`), он лишь меняет
 * адрес возврата.
 */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import type { AdminRoleFlags } from "../lib/admin-tabs";
import { onBackButton, setBackButton } from "../lib/max-sdk";
import { SALON_PILOT_LANDING, canOpenSalonPilot } from "../lib/salon-pilot";

export function useSalonSectionBack(me: AdminRoleFlags): void {
  const navigate = useNavigate();
  const pilot = canOpenSalonPilot(me);
  useEffect(() => {
    if (!pilot) {
      setBackButton(false);
      return;
    }
    setBackButton(true);
    const off = onBackButton(() => navigate(SALON_PILOT_LANDING));
    return () => {
      off();
      setBackButton(false);
    };
  }, [pilot, navigate]);
}
