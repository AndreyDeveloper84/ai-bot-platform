/**
 * R5 notifications card — channel + soft timing + entry to the REAL
 * customer notification-preferences screen (issue #948 / P-8).
 *
 * Spec: `docs/screens/customer-profile-flow.md` §7. Channel is
 * MAX-only for pilot per memory `project_max_only_pilot`. Timing copy
 * is the SOFT phrasing per `customer-reminders-voice` cut #5 —
 * explicitly NOT a schedule promise («за 24 часа и за 2 часа» =
 * backend SLA-dependent — spec §7.1).
 *
 * The entry button navigates to `/customer/notification-settings`
 * (real toggles on GET/PATCH /me). The support-route sheet is gone —
 * the screen exists now, so routing to support would be the same
 * lie class as fake data.
 */

interface Props {
  /** Navigate to the real notification-settings screen. */
  onOpenSettings: () => void;
}

export function NotificationCard({ onOpenSettings }: Props) {
  return (
    <section className="profile-notifications" aria-labelledby="profile-r5-h2">
      <h2 id="profile-r5-h2" className="profile-section__heading">
        Уведомления
      </h2>
      <dl className="profile-notifications__facts">
        <div className="profile-notifications__row">
          <dt className="profile-notifications__term">Канал</dt>
          <dd className="profile-notifications__value">MAX</dd>
        </div>
        <div className="profile-notifications__row">
          <dt className="profile-notifications__term">Когда</dt>
          <dd className="profile-notifications__value">
            Перед визитом и если что-то изменится по записи.
          </dd>
        </div>
      </dl>
      <button
        type="button"
        className="btn-secondary profile-notifications__cta"
        onClick={onOpenSettings}
      >
        Открыть настройки уведомлений
      </button>
    </section>
  );
}
