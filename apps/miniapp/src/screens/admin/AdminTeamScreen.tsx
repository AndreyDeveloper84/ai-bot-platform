/**
 * Admin team screen — MM1 minimal roster list.
 *
 * Default landing for admin / owner / receptionist roles. Per
 * master-management-handoff §MM1, this is a simplified version:
 * roster, search, active/archive filter, tap → action sheet.
 *
 * MM3 master-detail editing is OUT of scope this PR; the action
 * sheet's «Открыть подробно» surfaces a placeholder toast.
 *
 * Owner-only actions («Деактивировать» / «Восстановить») are
 * disabled with a tooltip for Admin / Receptionist callers — the
 * backend re-checks at /deactivate + /reactivate (PR #467).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { Snackbar } from "../../components/Snackbar";
import { StateError } from "../../components/StateError";
import { ApiError } from "../../lib/api";
import {
  listMasters,
  reactivateMaster,
  type MasterListItem,
  type MeResponse,
} from "../../lib/admin-api";
import { hapticImpact, hapticSelection, setBackButton } from "../../lib/max-sdk";

interface Props {
  me: MeResponse;
}

type FilterTab = "active" | "archive";

const PLACEHOLDER_AVATAR_BG = "var(--c-surface-2)";

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).slice(0, 2);
  return parts
    .map((p) => p.charAt(0).toUpperCase())
    .join("");
}

export function AdminTeamScreen({ me }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  // Read filter from URL query so /admin/team?is_active=false (Step 4
  // «Открыть архив») lands on the archive tab directly.
  const initialFilter: FilterTab = useMemo(() => {
    const q = new URLSearchParams(location.search);
    return q.get("is_active") === "false" ? "archive" : "active";
  }, [location.search]);

  const [filter, setFilter] = useState<FilterTab>(initialFilter);
  const [search, setSearch] = useState<string>("");
  const [items, setItems] = useState<MasterListItem[] | null>(null);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Action sheet state — selected master + open flag.
  const [sheetMaster, setSheetMaster] = useState<MasterListItem | null>(null);
  // Reactivation confirm dialog.
  const [reactivateMasterItem, setReactivateMasterItem] =
    useState<MasterListItem | null>(null);
  const [reactivateNotify, setReactivateNotify] = useState<boolean>(true);
  const [reactivating, setReactivating] = useState<boolean>(false);
  // Snackbar toast queue.
  const [toast, setToast] = useState<string>("");

  useEffect(() => {
    // Tab bar is at the root — hide MAX BackButton on the team screen.
    setBackButton(false);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await listMasters({
        is_active: filter === "active",
        search: search.trim() || undefined,
        limit: 50,
      });
      setItems(res.items);
      setTotalCount(res.total_count);
    } catch (e) {
      setErr(e);
      setItems(null);
    } finally {
      setLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Sync filter back into the URL so the «Открыть архив» deep-link
  // round-trips when the user navigates back from MM5 Step 4.
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const want = filter === "archive" ? "false" : null;
    const have = q.get("is_active");
    if (want !== have) {
      if (want === null) q.delete("is_active");
      else q.set("is_active", want);
      const qs = q.toString();
      navigate(`/admin/team${qs ? `?${qs}` : ""}`, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const ownerOnlyDisabledLabel = "Только владелец может деактивировать мастера";

  const handleDeactivate = useCallback(
    (master: MasterListItem) => {
      if (!me.is_owner) {
        setToast(ownerOnlyDisabledLabel);
        return;
      }
      hapticImpact("medium");
      setSheetMaster(null);
      navigate(`/admin/team/${master.id}/deactivate`);
    },
    [me.is_owner, navigate],
  );

  const handleReactivateOpen = useCallback(
    (master: MasterListItem) => {
      if (!me.is_owner) {
        setToast(ownerOnlyDisabledLabel);
        return;
      }
      setSheetMaster(null);
      setReactivateNotify(true);
      setReactivateMasterItem(master);
    },
    [me.is_owner],
  );

  const handleReactivateConfirm = useCallback(async () => {
    if (!reactivateMasterItem) return;
    setReactivating(true);
    try {
      const masterName = reactivateMasterItem.name.split(/\s+/)[0] ?? reactivateMasterItem.name;
      await reactivateMaster(reactivateMasterItem.id, reactivateNotify);
      setReactivateMasterItem(null);
      setToast(`${masterName} восстановлена`);
      // Refresh — the archived row should disappear from the archive tab.
      void reload();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.detail || "Не получилось восстановить"
          : "Сеть недоступна — попробуйте ещё раз";
      setToast(msg);
    } finally {
      setReactivating(false);
    }
  }, [reactivateMasterItem, reactivateNotify, reload]);

  const handleOpenDetail = useCallback(() => {
    setSheetMaster(null);
    setToast("Карточка мастера — скоро");
  }, []);

  if (err) {
    return (
      <div className="screen">
        <h1 className="screen__title">Команда</h1>
        <StateError err={err} onRetry={() => void reload()} />
        <AdminTabBar />
      </div>
    );
  }

  return (
    <div className="screen">
      <header className="screen__header" style={{ marginBottom: "var(--s-3)" }}>
        <h1 className="screen__title" style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          Команда
          <span
            className="admin-count-chip"
            aria-label={`всего: ${totalCount}`}
          >
            {totalCount}
          </span>
        </h1>
      </header>

      <div
        role="tablist"
        aria-label="Фильтр команды"
        className="admin-filter-tabs"
      >
        <button
          type="button"
          role="tab"
          aria-selected={filter === "active"}
          className={`admin-filter-tabs__tab${filter === "active" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => {
            hapticSelection();
            setFilter("active");
          }}
        >
          Активные
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filter === "archive"}
          className={`admin-filter-tabs__tab${filter === "archive" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => {
            hapticSelection();
            setFilter("archive");
          }}
        >
          Архив
        </button>
      </div>

      <div style={{ margin: "var(--s-3) 0" }}>
        <input
          type="search"
          inputMode="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по имени"
          aria-label="Поиск по имени"
          className="admin-search-input"
        />
      </div>

      {loading && items === null && (
        <div className="callout" role="status">
          Загружаем команду…
        </div>
      )}

      {items && items.length === 0 && (
        <div className="callout" role="status">
          {filter === "active"
            ? "В команде пока нет активных мастеров."
            : "В архиве никого нет."}
        </div>
      )}

      {items && items.length > 0 && (
        <ul className="admin-master-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="master-card"
                onClick={() => {
                  hapticSelection();
                  setSheetMaster(m);
                }}
                aria-label={`Открыть действия мастера ${m.name}`}
              >
                <span
                  className="master-card__avatar"
                  style={{ background: PLACEHOLDER_AVATAR_BG }}
                >
                  {m.photo_url ? (
                    <img src={m.photo_url} alt="" />
                  ) : (
                    <span aria-hidden="true">{initials(m.name)}</span>
                  )}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="master-card__name">{m.name}</span>
                  {m.specialization && (
                    <span className="master-card__spec" style={{ display: "block" }}>
                      {m.specialization}
                    </span>
                  )}
                  <span
                    style={{
                      display: "flex",
                      gap: "var(--s-2)",
                      marginTop: "var(--s-1)",
                      flexWrap: "wrap",
                    }}
                  >
                    {m.invite_status === "pending" && (
                      <span className="admin-chip admin-chip--warn">приглашён</span>
                    )}
                    <span className="admin-chip">{`${m.services_count} услуг`}</span>
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* --- Action sheet --- */}
      {sheetMaster && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Действия — ${sheetMaster.name}`}
          onClick={() => setSheetMaster(null)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="admin-sheet__title">{sheetMaster.name}</div>
            <button
              type="button"
              className="admin-sheet__action"
              onClick={handleOpenDetail}
            >
              Открыть подробно
            </button>
            {sheetMaster.is_active ? (
              <button
                type="button"
                className="admin-sheet__action admin-sheet__action--danger"
                disabled={!me.is_owner}
                title={me.is_owner ? undefined : ownerOnlyDisabledLabel}
                onClick={() => handleDeactivate(sheetMaster)}
              >
                Деактивировать
              </button>
            ) : (
              <button
                type="button"
                className="admin-sheet__action"
                disabled={!me.is_owner}
                title={me.is_owner ? undefined : ownerOnlyDisabledLabel}
                onClick={() => handleReactivateOpen(sheetMaster)}
              >
                Восстановить
              </button>
            )}
            <button
              type="button"
              className="admin-sheet__action admin-sheet__action--cancel"
              onClick={() => setSheetMaster(null)}
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      {/* --- Reactivation confirm dialog --- */}
      {reactivateMasterItem && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Восстановить ${reactivateMasterItem.name}?`}
          onClick={() => !reactivating && setReactivateMasterItem(null)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              {`Восстановить ${reactivateMasterItem.name}?`}
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`${reactivateMasterItem.name.split(/\s+/)[0] ?? reactivateMasterItem.name} снова сможет войти в систему и получать клиентов. Её график восстановится в том виде, в котором был до деактивации.`}
            </p>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                margin: "var(--s-3) 0",
              }}
            >
              <input
                type="checkbox"
                checked={reactivateNotify}
                onChange={(e) => setReactivateNotify(e.target.checked)}
              />
              <span>
                {`Отправить ${reactivateMasterItem.name.split(/\s+/)[0] ?? reactivateMasterItem.name} сообщение «Вы снова активны»`}
              </span>
            </label>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={reactivating}
                onClick={() => setReactivateMasterItem(null)}
                style={{ flex: 1 }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={reactivating}
                onClick={() => void handleReactivateConfirm()}
                style={{ flex: 1 }}
              >
                {reactivating ? "Восстанавливаем…" : "Восстановить"}
              </button>
            </div>
          </div>
        </div>
      )}

      <Snackbar
        visible={!!toast}
        message={toast}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />

      <AdminTabBar />
    </div>
  );
}
