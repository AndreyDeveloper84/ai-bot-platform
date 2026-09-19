/**
 * Лист аватара — куда уходят разделы, снятые с нижней панели (DRF-2115, §50).
 *
 * Нижняя панель салонной админки — ровно три: Сегодня | Расписание | Ayla.
 * Всё остальное — «Управление салоном» (Команда, Услуги), «Чаты с мастерами»
 * и «Настройки» — открывается из аватара справа вверху. Состав — по роли и
 * только из этого набора: финансовых, юридических и владельческих пунктов
 * здесь нет (даже серых) — их сегодня нет как экранов, и сторож в тесте
 * держит набор закрытым.
 *
 * «Профиль» — только когда у человека есть мастерский профиль
 * (`is_master` → `/master/profile`): экрана профиля администратора не
 * существует, а пункт без экрана — обещание (§33).
 *
 * Модуль переиспользуемый: мастерская поверхность (DRF-2121) подставит
 * свой список пунктов в тот же лист.
 */
import type { AdminRoleFlags } from "./admin-tabs";

export type AvatarSheetItemKey =
  | "profile"
  | "team"
  | "services"
  | "staffChats"
  | "studio"
  | "settings";

export interface AvatarSheetItem {
  key: AvatarSheetItemKey;
  label: string;
  to: string;
  /** Группа «Управление салоном» — подпункты рисуются под общим заголовком. */
  group?: "manage";
}

export const AVATAR_SHEET_COPY = {
  trigger: "Меню профиля",
  title: "Меню",
  profile: "Профиль",
  manage: "Управление салоном",
  team: "Команда",
  services: "Услуги",
  staffChats: "Чаты с мастерами",
  /** Мастер: внутренний чат со студией (DRF-2121). */
  studio: "Со студией",
  settings: "Настройки",
  close: "Закрыть",
} as const;

export const MASTER_PROFILE_PATH = "/master/profile";

export type AvatarSheetRoleFlags = AdminRoleFlags & { is_master?: boolean };

/** Пункты листа для владельца/администратора; ресепшн — пусто (§35). */
export function avatarSheetItemsFor(me: AvatarSheetRoleFlags): AvatarSheetItem[] {
  if (!(me.is_owner || me.is_admin)) return [];
  const items: AvatarSheetItem[] = [];
  if (me.is_master) {
    items.push({ key: "profile", label: AVATAR_SHEET_COPY.profile, to: MASTER_PROFILE_PATH });
  }
  items.push(
    { key: "team", label: AVATAR_SHEET_COPY.team, to: "/admin/team", group: "manage" },
    { key: "services", label: AVATAR_SHEET_COPY.services, to: "/admin/services", group: "manage" },
    { key: "staffChats", label: AVATAR_SHEET_COPY.staffChats, to: "/admin/internal-chat" },
    { key: "settings", label: AVATAR_SHEET_COPY.settings, to: "/admin/settings" },
  );
  return items;
}

/**
 * Пункты листа для МАСТЕРА (DRF-2121; §28 п.3): Профиль · Со студией ·
 * Настройки. Набор закрыт сторожем; «Диалоги» (переписка мастер↔клиент,
 * DRF-1039/1255) сюда не входят намеренно.
 */
export function masterAvatarSheetItems(): AvatarSheetItem[] {
  return [
    { key: "profile", label: AVATAR_SHEET_COPY.profile, to: MASTER_PROFILE_PATH },
    { key: "studio", label: AVATAR_SHEET_COPY.studio, to: "/master/internal-chat" },
    { key: "settings", label: AVATAR_SHEET_COPY.settings, to: "/master/settings" },
  ];
}

/** «Ирина Петрова» → «ИП»; пустое имя → «•». */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "");
  return letters.join("") || "•";
}
