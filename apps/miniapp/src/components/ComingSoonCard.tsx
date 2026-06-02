/**
 * R3 coming-soon card — Profile tab memory transparency (deferred).
 *
 * Spec: `docs/screens/customer-profile-flow.md` §5 — backend memory
 * layer NOT built (no `UserPersonalContext` / `MemoryEntry` tables,
 * no summary endpoint, no clear endpoint). For pilot we render a
 * calm coming-soon block; no data surface, no clear action, no
 * fake bullet list of memory categories.
 *
 * Anti-pattern avoided per spec §14: «Fake "Что Ayla знает"
 * data-surface или clear-кнопка без backend».
 *
 * Voice tone per spec §10.2 — calm, honest, «ты», no marketing
 * («Скоро…», не «Уже умею всё!»).
 */

export function ComingSoonCard() {
  return (
    <div className="profile-coming-soon" role="note">
      <p className="profile-coming-soon__primary">
        Скоро я смогу показать тебе здесь, что я о тебе помню — любимые
        услуги, удобное время, настройки — и дать это очистить.
      </p>
      <p className="profile-coming-soon__secondary">
        Пока этот раздел готовится. Я не показываю лишнего и не делаю
        вид, что уже всё умею.
      </p>
    </div>
  );
}
