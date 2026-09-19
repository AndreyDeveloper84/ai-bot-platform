/**
 * Аватар мастера с листом «Профиль · Со студией · Настройки» (DRF-2121, §28 п.3)
 * для разделов, у которых своих данных о мастере нет — Расписание и Ayla.
 * Имя и фото — из `useMasterIdentity` (один запрос на сессию). «Сегодня»
 * рисует тот же `AvatarSheet` из данных дашборда — с точкой «владелец ждёт
 * правок профиля»; здесь точки нет (у этих разделов такого сигнала нет).
 */
import { AvatarSheet } from "./AvatarSheet";
import { useMasterIdentity } from "../hooks/useMasterIdentity";
import { masterAvatarSheetItems } from "../lib/avatar-sheet";

export function MasterAvatar() {
  const identity = useMasterIdentity();
  return (
    <AvatarSheet
      name={identity?.name ?? ""}
      photoUrl={identity?.photoUrl ?? null}
      items={masterAvatarSheetItems()}
    />
  );
}
