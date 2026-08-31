/**
 * Admin — «Люди салона»: everyone with a role, and every role they hold.
 *
 * Route: `/admin/team/people`. Backend: `GET /api/v1/admin/staff/`.
 *
 * ### Why this screen exists next to «Команда»
 *
 * `AdminTeamScreen` lists masters — the people who deliver services. It
 * has never listed administrators, because nothing produced that list:
 * access could be granted (`/admin/team/access`) and revoked, and who
 * held it was unreadable outside a psql session. The owner asked for the
 * list, and for the list only.
 *
 * Splitting rather than folding into «Команда» is the same call the
 * backend made between `masters/invite/` and `staff/invite/`: the roster
 * of service providers and the map of who can administer the salon
 * answer different questions, and «Команда» is already a working screen
 * with actions on it that this one deliberately has none of.
 *
 * ### One row per person, never per role
 *
 * Roles are additive and live in two tables (ADR-0008): `TenantStaff`
 * holds owner / admin / receptionist, `CatalogMaster.linked_bot_user`
 * holds master. The owner who also cuts hair is ONE person with TWO
 * roles. The backend merges; this screen renders `person.roles` as a
 * list and must never collapse it to `roles[0]` — that would restore
 * exactly the blindness the screen was built to remove.
 *
 * ### Read-only on purpose
 *
 * No role change, no revoke, no bulk actions. The owner scoped this to
 * the list because there was no list at all; the buttons are a separate
 * decision that has not been made. Revoking already exists as an
 * endpoint (`staff/revoke/`) with no caller, which is where a follow-up
 * would start.
 *
 * ### No phone numbers
 *
 * The endpoint does not return one — DRF-1039 covers clients, nothing
 * covers staff, and inferring permission from silence is how a phone
 * ends up on a screen nobody approved.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { StateError } from "../../components/StateError";
import {
  getStaffRoster,
  type MeResponse,
  type RoleSource,
  type RoleState,
  type StaffRoleGrant,
  type StaffRosterPerson,
} from "../../lib/admin-api";
import { onBackButton, setBackButton } from "../../lib/max-sdk";

interface Props {
  me: MeResponse;
}

const ROLE_LABEL: Record<string, string> = {
  owner: "Владелец",
  admin: "Администратор",
  receptionist: "Ресепшен",
  master: "Мастер",
};

const SOURCE_LABEL: Record<RoleSource, string> = {
  access_code: "по коду доступа",
  master_invite: "по приглашению мастера",
  direct: "добавлен(а) напрямую",
};

/**
 * Suffix on the role chip. `active` says nothing — a live role is the
 * default and does not need a word.
 *
 * `pending` and `revoked` must never share copy: one is somebody who has
 * not arrived yet and needs the invite resent, the other is somebody
 * whose access was taken away. Telling them apart is why the backend
 * returns three states instead of a boolean.
 */
const STATE_SUFFIX: Record<RoleState, string> = {
  active: "",
  pending: " — приглашение не принято",
  revoked: " — доступ отозван",
};

const STATE_CHIP_CLASS: Record<RoleState, string> = {
  active: "admin-chip",
  pending: "admin-chip admin-chip--warn",
  revoked: "admin-chip admin-chip--revoked",
};

const MONTHS_GEN = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

/**
 * «12 августа 2026». The year is kept, unlike the inbox formatter: this
 * screen answers «с каких пор», and a roster read next January must not
 * make a two-year-old grant look like last week.
 *
 * Rendered in the device timezone, like every other date surface in the
 * Mini App (`masterDateFormat.ts`, `AdminAvailabilityRequestsScreen`).
 * A grant made just after midnight salon-time therefore reads as the
 * previous day to an owner whose phone is set elsewhere — off by a day
 * on a field measured in months, and fixing it here alone would make
 * this the one screen that disagrees with the others.
 */
function formatSince(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()] ?? ""} ${d.getFullYear()}`;
}

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  return trimmed
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p.charAt(0).toUpperCase())
    .join("");
}

function roleLine(grant: StaffRoleGrant): string {
  const label = ROLE_LABEL[grant.role] ?? grant.role;
  const source = SOURCE_LABEL[grant.source] ?? "";
  const since = formatSince(grant.since);
  // «Мастер · по приглашению мастера · 12 августа 2026». A missing date
  // is left out rather than filled with a guess: for a master the catalog
  // sync produced, nobody knows when they started.
  return [label, source, since].filter(Boolean).join(" · ");
}

export function AdminPeopleScreen({ me }: Props) {
  const navigate = useNavigate();
  const [items, setItems] = useState<StaffRosterPerson[] | null>(null);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [truncated, setTruncated] = useState<boolean>(false);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => navigate("/admin/team"));
    return () => {
      off();
      setBackButton(false);
    };
  }, [navigate]);

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      // Hooks cannot sit behind the `is_owner` early return below — they
      // must all run on every render — so the gate lives here instead.
      // Without it a non-owner who deep-links to this route fires a
      // request that is guaranteed to come back 403, on every visit.
      if (!me.is_owner) return;
      setErr(null);
      try {
        const res = await getStaffRoster({ signal });
        if (signal?.aborted) return;
        setItems(res.items);
        setTotalCount(res.total_count);
        setTruncated(res.truncated);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setErr(e);
        setItems(null);
      }
    },
    [me.is_owner],
  );

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const manualReload = useCallback(() => {
    void reload();
  }, [reload]);

  // The endpoint is owner-only and answers 403 to everyone else. The
  // check here is so an admin who deep-links sees a sentence instead of
  // an error card; the backend 403 is the actual gate.
  if (!me.is_owner) {
    return (
      <div className="screen">
        <h1 className="screen__title">Люди салона</h1>
        <div className="callout" role="status">
          Список ролей видит только владелец салона.
        </div>
        <AdminTabBar />
      </div>
    );
  }

  if (err) {
    return (
      <div className="screen">
        <h1 className="screen__title">Люди салона</h1>
        <StateError err={err} onRetry={manualReload} />
        <AdminTabBar />
      </div>
    );
  }

  return (
    <div className="screen">
      {/*
        No `screen__header` class here, deliberately. Four neighbouring
        admin screens carry it and it has no rule in src/styles/ — it is
        inert on all of them, with the inline margin below doing the actual
        work. Copying it here would have added a fifth dead class (the
        style-contract guard, DRF-1066). The <header> element keeps the
        semantics; `screen__title` on the h1 is the class that has a rule.
      */}
      <header style={{ marginBottom: "var(--s-2)" }}>
        <h1
          className="screen__title"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-2)",
            margin: 0,
          }}
        >
          Люди салона
          <span className="admin-count-chip" aria-label={`всего: ${totalCount}`}>
            {totalCount}
          </span>
        </h1>
      </header>

      <p
        className="master-card__spec"
        style={{ margin: "0 0 var(--s-3)", display: "block" }}
      >
        Все, у кого есть роль в салоне. Роли складываются: один человек
        может быть и мастером, и администратором.
      </p>

      {items === null && (
        <div className="callout" role="status">
          Загружаем…
        </div>
      )}

      {items && items.length === 0 && (
        <div className="callout" role="status">
          Пока никого нет. Добавьте мастера или выдайте доступ на экране
          «Команда».
        </div>
      )}

      {truncated && (
        <div className="callout callout--danger" role="status">
          Показаны первые {items?.length ?? 0} из {totalCount}. Если людей
          столько быть не должно — напишите нам.
        </div>
      )}

      {items && items.length > 0 && (
        <ul
          className="admin-master-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {items.map((p) => (
            <li key={p.id}>
              {/*
                A div, not a button: there is nothing to tap. A card that
                looks pressable and does nothing is worse than a flat one.
              */}
              <div className="master-card" style={{ cursor: "default" }}>
                <span
                  className="master-card__avatar"
                  style={{
                    background: "var(--c-surface-2)",
                    opacity: p.is_active ? 1 : 0.5,
                  }}
                  aria-hidden="true"
                >
                  {initials(p.name)}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="master-card__name">{p.name}</span>

                  <span
                    style={{
                      display: "flex",
                      gap: "var(--s-2)",
                      margin: "var(--s-1) 0",
                      flexWrap: "wrap",
                    }}
                  >
                    {p.roles.map((grant) => (
                      <span
                        key={`${p.id}:${grant.role}`}
                        className={
                          STATE_CHIP_CLASS[grant.state] ?? "admin-chip"
                        }
                      >
                        {ROLE_LABEL[grant.role] ?? grant.role}
                        {STATE_SUFFIX[grant.state] ?? ""}
                      </span>
                    ))}
                    {p.roles.length === 0 && (
                      <span className="admin-chip admin-chip--revoked">
                        без роли
                      </span>
                    )}
                  </span>

                  {p.roles.map((grant) => (
                    <span
                      key={`line:${p.id}:${grant.role}`}
                      className="master-card__spec"
                      style={{ display: "block" }}
                    >
                      {roleLine(grant)}
                    </span>
                  ))}

                  {/*
                    Suppressed while every role is `pending`: «приглашение
                    не принято» already says the person has no login, and
                    two warning chips saying one thing read as two
                    problems.
                  */}
                  {!p.has_account &&
                    !p.roles.every((r) => r.state === "pending") && (
                      <span
                        className="admin-chip admin-chip--warn"
                        style={{ marginTop: "var(--s-1)" }}
                      >
                        нет входа в приложение
                      </span>
                    )}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      <AdminTabBar />
    </div>
  );
}
