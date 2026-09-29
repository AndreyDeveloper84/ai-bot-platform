/**
 * Calm coming-soon card for gated stub surfaces (`PilotComingSoonScreen`).
 *
 * Born as the R3 memory placeholder (spec `docs/screens/customer-profile-flow.md`
 * §5). Since DRF-2133 the profile's R3 section renders `MemoryCard` against the
 * real `GET/DELETE /memory/` endpoints; the default copy below is kept only as
 * the fallback wording and is no longer shown on the profile.
 *
 * Anti-pattern avoided per spec §14: «Fake "Что Ayla знает"
 * data-surface или clear-кнопка без backend».
 *
 * Voice tone per spec §10.2 — calm, honest, «ты», no marketing
 * («Скоро…», не «Уже умею всё!»).
 */

interface ComingSoonCardProps {
  /**
   * Optional copy override (defaults: the R3 memory-transparency copy
   * below). Used by `PilotComingSoonScreen` for gated stub surfaces —
   * same calm coming-soon pattern, surface-specific wording.
   */
  primary?: string;
  secondary?: string;
}

export function ComingSoonCard({ primary, secondary }: ComingSoonCardProps = {}) {
  return (
    <div className="profile-coming-soon" role="note">
      <p className="profile-coming-soon__primary">
        {primary ?? (
          <>
            Скоро я смогу показать тебе здесь, что я о тебе помню — любимые
            услуги, удобное время, настройки — и дать это очистить.
          </>
        )}
      </p>
      <p className="profile-coming-soon__secondary">
        {secondary ?? (
          <>
            Пока этот раздел готовится. Я не показываю лишнего и не делаю
            вид, что уже всё умею.
          </>
        )}
      </p>
    </div>
  );
}
