/**
 * R5 notifications card — channel + soft timing + entry button.
 *
 * Spec: `docs/screens/customer-profile-flow.md` §7 (sign-off
 * 2026-06-01). Channel is MAX-only for pilot per memory
 * `project_max_only_pilot`. Timing copy is the SOFT phrasing per
 * `customer-reminders-voice` cut #5 — explicitly NOT a schedule
 * promise («за 24 часа и за 2 часа» = backend SLA-dependent — spec
 * §7.1).
 *
 * # «Открыть настройки уведомлений» — Phase A Q ambiguity
 *
 * Spec §7 says the entry button deeplinks to the existing
 * notification-preferences-ux full UI. There is no customer-facing
 * notification-preferences screen in `apps/miniapp/src/screens/` for
 * pilot (only `MasterNotificationSettingsScreen`). Phase A recon
 * flagged this; the pilot resolution is to route to support (same
 * deeplink as R2 deferred sheets) — consistent with Variant 3
 * truthfulness rule. FOLLOW_UP issue P-8 (post-pilot): build the
 * real customer-facing notification-prefs screen and flip the entry
 * target here.
 */

import { useRef } from "react";
import { SupportEntrySheet, type SupportPreset } from "./SupportEntrySheet";

interface Props {
  /**
   * Receive open-state from parent so the screen owns sheet lifecycle.
   * Mirrors the R2 sheet pattern.
   */
  supportPreset: SupportPreset | null;
  onOpenSupport: () => void;
  onCloseSupport: () => void;
}

export function NotificationCard({
  supportPreset,
  onOpenSupport,
  onCloseSupport,
}: Props) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
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
        ref={triggerRef}
        type="button"
        className="btn-secondary profile-notifications__cta"
        onClick={onOpenSupport}
      >
        Открыть настройки уведомлений
      </button>
      <SupportEntrySheet
        preset={supportPreset}
        triggerRef={triggerRef}
        onClose={onCloseSupport}
      />
    </section>
  );
}
