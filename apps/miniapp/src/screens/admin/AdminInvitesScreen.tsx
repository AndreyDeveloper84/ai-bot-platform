/**
 * Admin — «Коды доступа»: every access code issued at this salon (DRF-2275).
 *
 * Route: `/admin/team/invites`. Backend: `GET /api/v1/admin/staff/invites/`,
 * `POST …/<id>/cancel/`, `POST …/<id>/resend/`.
 *
 * ### Why this screen exists
 *
 * Codes were issued on «Команда» (`/admin/team/access`) and then vanished:
 * which were still waiting, which were taken, and cancelling one before it
 * expired needed the operator's Django admin. The owner's decision CD §72
 * p.15–16 (21.09) makes the Mini App her only workplace.
 *
 * ### Who
 *
 * Owner AND admin — whoever issues codes manages them (main window, 22.09).
 * That is also why this is its own screen and not a block on «Люди
 * салона»: that screen is the owner's alone. The one line inside: only the
 * owner issues an owner code, so only she cancels or re-issues one — an
 * admin sees it in the list, with no buttons (the server answers 403).
 *
 * ### The code is never in the list
 *
 * Only its hash is stored. «Отправить заново» issues a NEW code for the same
 * role, note and master card and shows it ONCE — through the same
 * `IssuedAccessCode` block as a freshly issued one, warning included. A
 * pending old code stops working in the same step.
 *
 * Master invite LINKS (`CatalogMaster.invite_token`) are not here — a
 * separate sheet.
 */

import { useCallback, useEffect, useState } from "react";

import { ScreenLayout } from "../../components/ScreenLayout";
import { StateError } from "../../components/StateError";
import { StickyCta } from "../../components/StickyCta";
import { useClosingConfirmation } from "../../hooks/useClosingConfirmation";
import { ApiError } from "../../lib/api";
import {
  cancelStaffInvite,
  listStaffInvites,
  resendStaffInvite,
  type MeResponse,
  type StaffInviteResponse,
  type StaffInviteRow,
  type StaffInviteStatus,
} from "../../lib/admin-api";
import { formatDateLong } from "../../lib/masterDateFormat";
import { hapticNotify } from "../../lib/max-sdk";
import { backTo, screenRoot } from "../../lib/screen-back";

import { IssuedAccessCode } from "./IssuedAccessCode";

interface Props {
  me: MeResponse;
}

const ROLE_LABEL: Record<string, string> = {
  owner: "Владелец",
  admin: "Администратор",
  receptionist: "Ресепшен",
  master: "Мастер",
};

/** One word per status — four different next moves, four different words. */
const STATUS_LABEL: Record<StaffInviteStatus, string> = {
  pending: "ждёт",
  accepted: "принят",
  expired: "истёк",
  cancelled: "отменён",
};

const STATUS_CHIP: Record<StaffInviteStatus, string> = {
  // The only one that asks for a move — hand the code over, or cancel it.
  pending: "admin-chip admin-chip--warn",
  accepted: "admin-chip",
  expired: "admin-chip admin-chip--revoked",
  cancelled: "admin-chip admin-chip--revoked",
};

/**
 * May THIS VIEWER act on this code? Owner codes are the owner's; the rest
 * belong to whoever issues codes. Mirrors the server's 403.
 */
function mayManage(inv: StaffInviteRow, me: MeResponse): boolean {
  return inv.role !== "owner" || me.is_owner;
}

/** The date line under a code — the one fact that matters for its status. */
function dateLine(inv: StaffInviteRow): string {
  const issued = `выдан ${formatDateLong(inv.created_at)}`;
  switch (inv.status) {
    case "pending":
      return `${issued} · действует до ${formatDateLong(inv.expires_at)}`;
    case "accepted":
      return `${issued} · принят ${formatDateLong(inv.used_at ?? inv.created_at)}`;
    case "expired":
      return `${issued} · истёк ${formatDateLong(inv.expires_at)}`;
    case "cancelled":
      return `${issued} · отменён ${formatDateLong(inv.revoked_at ?? inv.created_at)}`;
  }
}

/** «Администратор», «Мастер · Вера Лис». */
function whatFor(inv: StaffInviteRow): string {
  const role = ROLE_LABEL[inv.role] ?? inv.role;
  return inv.master_name ? `${role} · ${inv.master_name}` : role;
}

function refusalText(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.slug === "invite_already_used") {
    return "Этим кодом уже воспользовались — доступ выдан. Обновите список.";
  }
  if (err.slug === "invite_master_missing") {
    return "Карточка мастера в архиве — код для неё выдать нельзя.";
  }
  if (err.status === 403) return "Код владельца может менять только владелец салона.";
  if (err.status === 404) return "Этого кода больше нет — обновите список.";
  return null;
}

type Pending = { kind: "cancel" | "resend"; invite: StaffInviteRow };
type Issued = { issued: StaffInviteResponse; invite: StaffInviteRow };

export function AdminInvitesScreen({ me }: Props) {
  const allowed = me.is_owner || me.is_admin;
  const [items, setItems] = useState<StaffInviteRow[] | null>(null);
  const [totalCount, setTotalCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<unknown>(null);
  // A resent code on screen: leaving loses it for good.
  const [issued, setIssued] = useState<Issued | null>(null);
  useClosingConfirmation(issued !== null);

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      // Hooks cannot sit behind the early return below, so the gate is here:
      // the front desk would only ever get a 403.
      if (!allowed) return;
      setErr(null);
      try {
        const res = await listStaffInvites({ signal });
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
    [allowed],
  );

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const confirm = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    setActionErr(null);
    try {
      if (pending.kind === "cancel") {
        await cancelStaffInvite(pending.invite.id);
        setPending(null);
        await reload();
      } else {
        const res = await resendStaffInvite(pending.invite.id);
        hapticNotify("success");
        setPending(null);
        setIssued({ issued: res, invite: pending.invite });
      }
    } catch (e) {
      hapticNotify("error");
      setActionErr(e);
    } finally {
      setBusy(false);
    }
  }, [pending, reload]);

  const closeSheet = useCallback(() => {
    setPending(null);
    setActionErr(null);
  }, []);

  if (issued) {
    return (
      <ScreenLayout
        back={screenRoot(
          "Новый код показывается один раз: уход назад уничтожает его. " +
            "Единственный выход — «Готово».",
        )}
        title="Новый код доступа"
        cta={
          <StickyCta
            onClick={() => {
              setIssued(null);
              void reload();
            }}
          >
            Готово
          </StickyCta>
        }
      >
        <p style={{ margin: "0 0 var(--s-3)" }}>
          Прежний код больше не работает. Передайте человеку этот.
        </p>
        <IssuedAccessCode
          issued={issued.issued}
          roleLabel={ROLE_LABEL[issued.issued.role] ?? issued.issued.role}
          masterName={issued.invite.master_name ?? undefined}
        />
      </ScreenLayout>
    );
  }

  if (!allowed) {
    return (
      <ScreenLayout back={backTo("/admin/team")} title="Коды доступа">
        <div className="callout" role="status">
          Коды доступа видят владелец и администраторы салона.
        </div>
      </ScreenLayout>
    );
  }

  const refusal = actionErr != null ? refusalText(actionErr) : null;

  return (
    <ScreenLayout back={backTo("/admin/team")} title="Коды доступа">
      <p
        className="master-card__spec"
        style={{ margin: "0 0 var(--s-3)", display: "block" }}
      >
        Все коды, выданные в салоне: кто ещё не пришёл, кто уже вошёл. Сам код
        здесь не показывается — только при выдаче.
      </p>

      {err != null && <StateError err={err} onRetry={() => void reload()} />}

      {err == null && items === null && (
        <div className="callout" role="status">
          Загружаем…
        </div>
      )}

      {items && items.length === 0 && (
        <div className="callout" role="status">
          Кодов пока не выдавали. Выдать код можно на экране «Команда» —
          «Добавить человека».
        </div>
      )}

      {truncated && (
        <div className="callout" role="status">
          Показаны последние {items?.length ?? 0} из {totalCount}.
        </div>
      )}

      {items && items.length > 0 && (
        <ul
          className="admin-master-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {items.map((inv) => {
            const manage = mayManage(inv, me);
            const canResend = manage && inv.status !== "accepted";
            const canCancel = manage && inv.status === "pending";
            return (
              <li key={inv.id}>
                <div className="master-card" style={{ cursor: "default" }}>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="master-card__name">{whatFor(inv)}</span>
                    <span
                      style={{
                        display: "flex",
                        gap: "var(--s-2)",
                        margin: "var(--s-1) 0",
                        flexWrap: "wrap",
                      }}
                    >
                      <span className={STATUS_CHIP[inv.status]}>
                        {STATUS_LABEL[inv.status]}
                      </span>
                    </span>
                    {inv.note && (
                      <span
                        className="master-card__spec"
                        style={{ display: "block" }}
                      >
                        {`Заметка: ${inv.note}`}
                      </span>
                    )}
                    <span
                      className="master-card__spec"
                      style={{ display: "block" }}
                    >
                      {dateLine(inv)}
                    </span>
                    {(canResend || canCancel) && (
                      <span
                        style={{
                          display: "flex",
                          gap: "var(--s-2)",
                          flexWrap: "wrap",
                          marginTop: "var(--s-2)",
                        }}
                      >
                        {canResend && (
                          <button
                            type="button"
                            className="btn-secondary"
                            aria-label={`Отправить заново: ${whatFor(inv)}`}
                            onClick={() => {
                              setActionErr(null);
                              setPending({ kind: "resend", invite: inv });
                            }}
                          >
                            Отправить заново
                          </button>
                        )}
                        {canCancel && (
                          <button
                            type="button"
                            className="btn-secondary"
                            aria-label={`Отменить код: ${whatFor(inv)}`}
                            onClick={() => {
                              setActionErr(null);
                              setPending({ kind: "cancel", invite: inv });
                            }}
                          >
                            Отменить
                          </button>
                        )}
                      </span>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {pending && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={
            pending.kind === "cancel" ? "Отменить код?" : "Выдать новый код?"
          }
          onClick={() => !busy && closeSheet()}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              {pending.kind === "cancel" ? "Отменить код?" : "Выдать новый код?"}
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`Код: ${whatFor(pending.invite)}${pending.invite.note ? ` · ${pending.invite.note}` : ""}.`}
            </p>
            <p style={{ margin: "var(--s-2) 0" }}>
              {pending.kind === "cancel"
                ? "Код перестанет работать: войти по нему будет нельзя."
                : pending.invite.status === "pending"
                  ? "Выдадим новый код на ту же роль, а прежний перестанет работать."
                  : "Выдадим новый код на ту же роль."}
            </p>

            {refusal != null && (
              <div className="callout callout--danger" role="alert">
                {refusal}
              </div>
            )}
            {actionErr != null && refusal == null && (
              <StateError err={actionErr} onRetry={() => void confirm()} />
            )}

            <div
              style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)" }}
            >
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={closeSheet}
                style={{ flex: 1 }}
              >
                Не сейчас
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={busy}
                onClick={() => void confirm()}
                style={{ flex: 1 }}
              >
                {busy
                  ? pending.kind === "cancel"
                    ? "Отменяем…"
                    : "Выдаём…"
                  : pending.kind === "cancel"
                    ? "Отменить код"
                    : "Выдать новый код"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ScreenLayout>
  );
}
