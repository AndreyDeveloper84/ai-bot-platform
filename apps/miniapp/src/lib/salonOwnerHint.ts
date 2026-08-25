/**
 * Who the master should contact when something in the studio needs an admin.
 *
 * Master-facing copy used to name a person literally («Карина…»). That name is
 * not in any payload — it was a spec example that leaked into shipped strings,
 * so every master on every tenant read about a person who does not exist for
 * them. The backend surfaces no owner first name at all: the only identity we
 * receive is the salon name (`DashboardSalon.name`, `SalonInfo.name`).
 *
 * So the hint is the salon name, and `DEFAULT_SALON_OWNER_HINT` covers the two
 * cases where we have nothing: a blank/absent name, and the screens whose
 * payloads carry no salon at all (schedule, notification prefs). An empty
 * string dropped into the middle of a sentence reads as a rendering bug; a
 * generic-but-true noun does not.
 *
 * Grammar contract for callers: the hint is used in the NOMINATIVE case only,
 * as the subject of a NON-PAST verb. Russian past tense agrees in gender
 * («Карина назначила» / «Салон назначил») and oblique cases require declining
 * the name — neither is possible for an arbitrary tenant string. Phrase around
 * it: «… может попросить», «… добавит вас», «… увидит и подтвердит».
 */

/** Used when no salon name is available. Nominative, gender-neutral. */
export const DEFAULT_SALON_OWNER_HINT = "Администратор студии";

/**
 * Resolve the owner hint from a salon name.
 *
 * @param salonName salon name from the API, or null/undefined when the screen's
 *   payload does not carry one.
 * @returns the trimmed salon name, or {@link DEFAULT_SALON_OWNER_HINT}.
 */
export function salonOwnerHint(salonName: string | null | undefined): string {
  const trimmed = salonName?.trim();
  return trimmed ? trimmed : DEFAULT_SALON_OWNER_HINT;
}
