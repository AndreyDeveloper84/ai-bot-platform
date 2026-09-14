/**
 * Число непрочитанных на значке — одно правило на все места, где его видно
 * (DRF-1848, карта кабинета D02: «число в шапке = число на табе»).
 *
 * Источник числа — `tab_badges.conversations_unread` дашборда; здесь только
 * то, как оно пишется: ноль и мусор — значка нет, больше 99 — «99+».
 */
export function unreadBadgeText(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null;
  return count > 99 ? "99+" : String(Math.floor(count));
}
