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
 * ### One action, and why it is the only one
 *
 * The screen shipped read-only: the owner had asked for the list and the
 * list only. `staff/revoke/` existed as an endpoint with *no caller* —
 * access was grantable from the Mini App and removable only from a psql
 * session, so a master invited by mistake or a receptionist who left
 * kept their way in until somebody wrote to us.
 *
 * This screen is that caller (DRF-1557, the owner's decision of
 * 07.09.2026), and
 * revoking is all it does. Changing a role is still a decision nobody
 * has made, and granting already lives on «Команда».
 *
 * ### The button is gated on the VIEWER'S ROLE
 *
 * `staff/revoke/` sits behind `require_admin_role` — owner and admin,
 * never the front desk — so `mayRevoke` reads the viewer's role and
 * nothing else. `AdminSalonDayScreen` decides its buttons from visit
 * lifecycle instead, and so gets to disagree with the server about who
 * may act; that is not copied here.
 *
 * Whether the ROW has anything to take away is a second, separate
 * question, and it is kept visibly separate (`STATE_REVOCABLE`,
 * `revokeTarget`). Conflating the two is how the permission check
 * quietly becomes a lifecycle check.
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
  revokeStaffAccess,
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
 * `pending`, `revoked` and `ayla_unlinked` must never share copy: one is
 * somebody who has not arrived yet and needs the invite resent, one is
 * somebody whose access was taken away, and one is a master we failed to
 * link to Ayla. Three different next moves for the owner. Telling them
 * apart is why the backend returns a reason instead of a boolean.
 */
const STATE_SUFFIX: Record<RoleState, string> = {
  active: "",
  pending: " — приглашение не принято",
  revoked: " — доступ отозван",
  // DRF-1540, wording is the owner's decision of 06.09.2026 kept
  // verbatim. It repeats «мастера» after the «Мастер» chip label, and
  // that is the cheaper of the two prices: paraphrasing a decision text
  // is how a status starts meaning something slightly else.
  //
  // It must never read like `pending` or `revoked`. Those two send the
  // owner to the master (resend the invite / restore access); this one
  // sends her to us, because the missing link is ours to fix. One text
  // for two causes would send her to spend an evening on the wrong one.
  ayla_unlinked: " — не удалось связать профиль мастера с Ayla",
};

const STATE_CHIP_CLASS: Record<RoleState, string> = {
  active: "admin-chip",
  pending: "admin-chip admin-chip--warn",
  revoked: "admin-chip admin-chip--revoked",
  // Not `--warn` and not `--revoked`: a revoked role is a fact and a
  // pending invite is a nudge, while this one is a fault that keeps a
  // working master off the storefront until somebody acts.
  ayla_unlinked: "admin-chip admin-chip--fault",
};

/**
 * Is there anything for `staff/revoke/` to take away from a role in this
 * state?
 *
 * A third exhaustive `Record<RoleState, …>` beside the two above, for the
 * same reason they are exhaustive: a new state added to the union must
 * force a decision here rather than default to a button that quietly
 * appears — or quietly stops appearing — on a state nobody thought about.
 *
 * This answers «is there access to take away», which is NOT «may this
 * viewer revoke». That one is `mayRevoke`, and it reads the role. Keeping
 * them apart is the whole point; see the header.
 */
const STATE_REVOCABLE: Record<RoleState, boolean> = {
  active: true,
  // Nobody has taken this up yet — no account exists, so the server would
  // answer 200 with `changed: false` and the row would not move. The
  // owner's next move here is to resend the invite, which «Команда» does.
  pending: false,
  // Already gone. Offering it again would make «доступ отозван» read as
  // though it had not landed.
  revoked: false,
  // DRF-1540 is OUR fault, not the salon's — but the access is real: she
  // accepted, she is linked, she can open the Mini App. The chip already
  // tells the owner this one is ours to fix; it does not follow that she
  // may not remove a master who is leaving anyway.
  ayla_unlinked: true,
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

/**
 * May THIS VIEWER revoke anybody at all?
 *
 * Mirrors `require_admin_role`, which admits owner and admin and refuses
 * the front desk. One line, reading `me` and nothing else — a permission
 * question answered by a role, so that it cannot drift into being
 * answered by whatever the row happens to look like.
 *
 * In practice everyone who reaches this screen is an owner (the roster
 * endpoint is owner-only). Writing the admin half anyway is what keeps
 * the button correct on the day the roster widens: the alternative is a
 * `me.is_owner` that looks deliberate and is really an accident of which
 * endpoint happened to be narrower.
 */
function mayRevoke(me: MeResponse): boolean {
  return me.is_owner || me.is_admin;
}

/**
 * The roles this row would actually lose, or `null` when the row must
 * not offer the button at all.
 *
 * Every `null` here is a request the server would refuse or no-op, and
 * each is named:
 *
 *   - no `bot_user_id` — nobody holds anything. `has_account` is defined
 *     as exactly this, and revoking by `master_id` would answer 200 with
 *     `changed: false`;
 *   - yourself — the view answers 403. An admin who revokes their own
 *     access cannot restore it;
 *   - the salon owner — the service raises `owner_revoke_refused` (409).
 *     A tenant has one active owner and only an owner may issue an owner
 *     code, so revoking it leaves a salon nobody can re-enter;
 *   - nothing revocable left — see `STATE_REVOCABLE`.
 */
function revokeTarget(
  person: StaffRosterPerson,
  me: MeResponse,
): { botUserId: string; roles: StaffRoleGrant[] } | null {
  const botUserId = person.bot_user_id;
  if (!botUserId) return null;
  if (botUserId === me.user.id) return null;
  const roles = person.roles.filter((g) => STATE_REVOCABLE[g.state]);
  if (roles.length === 0) return null;
  if (roles.some((g) => g.role === "owner")) return null;
  return { botUserId, roles };
}

/** «Мастер и Администратор» — what the confirmation says is going away. */
function roleNames(roles: StaffRoleGrant[]): string {
  const labels = roles.map((g) => ROLE_LABEL[g.role] ?? g.role);
  if (labels.length <= 1) return labels[0] ?? "";
  return `${labels.slice(0, -1).join(", ")} и ${labels[labels.length - 1]}`;
}

/** What the confirmation dialog is currently asking about. */
interface PendingRevoke {
  person: StaffRosterPerson;
  botUserId: string;
  roles: StaffRoleGrant[];
}

export function AdminPeopleScreen({ me }: Props) {
  const navigate = useNavigate();
  const [items, setItems] = useState<StaffRosterPerson[] | null>(null);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [truncated, setTruncated] = useState<boolean>(false);
  const [err, setErr] = useState<unknown>(null);
  // `null` = no dialog. Holding the whole target rather than an id keeps
  // the confirmation able to name the person and the roles without
  // looking anything up again while the list is being reloaded.
  const [pending, setPending] = useState<PendingRevoke | null>(null);
  const [revoking, setRevoking] = useState<boolean>(false);
  // Kept apart from `err`: the roster loaded fine, the revoke did not.
  // Folding the two would replace a working list with an error card and
  // lose which of the two things actually failed.
  const [revokeErr, setRevokeErr] = useState<unknown>(null);

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

  const confirmRevoke = useCallback(async () => {
    if (!pending) return;
    setRevoking(true);
    setRevokeErr(null);
    try {
      await revokeStaffAccess({
        bot_user_id: pending.botUserId,
        reason: "отозвано владельцем на экране «Люди салона»",
      });
      setPending(null);
      // The row must be re-read, not patched locally. What the revoke
      // actually did is the server's answer — which roles fell, whether
      // the master row was unlinked — and reloading is how the chip
      // becomes «доступ отозван» from the same source as every other
      // chip on this screen.
      await reload();
    } catch (e) {
      // Deliberately no silent swallow (DRF-1556 was exactly that: an
      // empty catch hid a contract mismatch). The dialog stays open,
      // the refusal is shown, and the owner can retry or back out.
      setRevokeErr(e);
    } finally {
      setRevoking(false);
    }
  }, [pending, reload]);

  const closeRevoke = useCallback(() => {
    setPending(null);
    setRevokeErr(null);
  }, []);

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
        <AdminTabBar me={me} />
      </div>
    );
  }

  if (err) {
    return (
      <div className="screen">
        <h1 className="screen__title">Люди салона</h1>
        <StateError err={err} onRetry={manualReload} />
        <AdminTabBar me={me} />
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
          {items.map((p) => {
            // Two questions, asked in this order and never merged: may
            // the VIEWER revoke (a role), and is there anything in THIS
            // ROW to take away (a state). `null` from either means no
            // button — and every `null` is a call the server would have
            // refused or no-opped. See `revokeTarget`.
            const target = mayRevoke(me) ? revokeTarget(p, me) : null;
            return (
              <li key={p.id}>
                {/*
                  A div, not a button: the CARD is not a tap target — there
                  is no person screen to open. A card that looks pressable
                  and does nothing is worse than a flat one. The revoke
                  button below is its own target and says so.
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

                    {target && (
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ marginTop: "var(--s-2)" }}
                        // Every row carries the same words, so the label
                        // alone would name nobody to a screen reader.
                        aria-label={`Отозвать доступ у ${p.name}`}
                        onClick={() => {
                          setRevokeErr(null);
                          setPending({
                            person: p,
                            botUserId: target.botUserId,
                            roles: target.roles,
                          });
                        }}
                      >
                        Отозвать доступ
                      </button>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/*
        Confirmation, not an undo. Nothing here is reversible from this
        screen: getting the person back in means issuing a fresh
        invitation on «Команда» and having them redeem it. The dialog
        therefore names WHO and WHAT — a bare «Вы уверены?» over a list
        of similar rows is how the wrong person gets revoked.
      */}
      {pending && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Отозвать доступ у ${pending.person.name}?`}
          onClick={() => !revoking && closeRevoke()}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              {`Отозвать доступ у ${pending.person.name}?`}
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`Снимаем роль: ${roleNames(pending.roles)}. ${pending.person.name} больше не сможет войти в приложение салона.`}
            </p>
            <p style={{ margin: "var(--s-2) 0" }}>
              Вернуть доступ отсюда нельзя — придётся выдать новое
              приглашение на экране «Команда» и дождаться, когда его примут.
            </p>

            {/*
              The server's refusal, shown as itself. `StateError` is the
              same component seven admin screens use for a failed load,
              and its retry re-sends the revoke rather than reloading the
              page. The dialog stays open: closing it on failure would
              leave the owner unsure whether the access is gone.
            */}
            {revokeErr != null && (
              <div style={{ margin: "var(--s-3) 0" }}>
                <p style={{ margin: "0 0 var(--s-2)" }}>Доступ не отозван.</p>
                <StateError
                  err={revokeErr}
                  onRetry={() => void confirmRevoke()}
                />
              </div>
            )}

            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={revoking}
                onClick={closeRevoke}
                style={{ flex: 1 }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={revoking}
                onClick={() => void confirmRevoke()}
                style={{ flex: 1 }}
              >
                {revoking ? "Отзываем…" : "Отозвать доступ"}
              </button>
            </div>
          </div>
        </div>
      )}

      <AdminTabBar me={me} />
    </div>
  );
}
