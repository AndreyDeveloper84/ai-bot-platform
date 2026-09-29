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
  | "customers"
  | "reviews"
  | "salon"
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
  /** Соло (DRF-2127): разделы, снятые с пятивкладочной панели. */
  customers: "Клиенты",
  reviews: "Отзывы",
  /** Соло с владельческой ролью — вход в салонную админку (DRF-1149). */
  salon: "Управление салоном",
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

export type MasterSurface = "master" | "solo";

/**
 * Пункты листа для МАСТЕРА по поверхности.
 *
 * `master` (DRF-2121; §28 п.3): Профиль · Со студией · Настройки.
 * «Диалоги» (переписка мастер↔клиент) сняты вовсе — DRF-1255 (OD-7).
 *
 * `solo` (DRF-2127): то, что ушло с пятивкладочной панели и листа «Ещё»
 * и имеет живой экран — Профиль · Клиенты · Услуги · Отзывы · Настройки;
 * при владельческой роли — «Управление салоном» → `/admin/team`
 * (DRF-1149). НЕ рисуется «Доходы» (экран-заглушка, §33) — адрес по
 * прямой ссылке; «AI-помощник» (переписка с клиентами) снят DRF-1255.
 * «Со студией» соло не применимо.
 * Набор закрыт сторожем.
 */
export function masterAvatarSheetItems(
  opts: { surface?: MasterSurface; salonAdmin?: boolean; selfService?: boolean } = {},
): AvatarSheetItem[] {
  if (opts.surface === "solo") {
    const soloItems: AvatarSheetItem[] = [
      { key: "profile", label: AVATAR_SHEET_COPY.profile, to: "/solo/profile" },
      { key: "customers", label: AVATAR_SHEET_COPY.customers, to: "/solo/customers" },
      { key: "services", label: AVATAR_SHEET_COPY.services, to: "/solo/services" },
      { key: "reviews", label: AVATAR_SHEET_COPY.reviews, to: "/solo/reviews" },
    ];
    // DRF-2254: «Услуги» — только когда каталог не назвал пространство салоном.
    const items = soloItems.filter(
      (item) => opts.selfService !== false || item.key !== "services",
    );
    if (opts.salonAdmin) {
      items.push({ key: "salon", label: AVATAR_SHEET_COPY.salon, to: "/admin/team" });
    }
    items.push({ key: "settings", label: AVATAR_SHEET_COPY.settings, to: "/solo/settings" });
    return items;
  }
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
