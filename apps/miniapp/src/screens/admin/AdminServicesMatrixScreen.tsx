/**
 * Admin MM4 — services ↔ masters matrix UI (Bundle 3/3 of TL admin chrome).
 *
 * Route (Mini App): /admin/services  — replaces the MM4 placeholder.
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM4
 * (lines 589-676). Verbatim Russian copy throughout.
 *
 * Spec quote (§MM4 line 597):
 *
 *   «Drive bot's `suggest_master` and `show_slots` logic: when customer
 *    asks for «маникюр», bot needs to know which masters perform it.»
 *
 * Spec quote (§MM4 line 652 — Mini App layout):
 *
 *   «Matrix becomes per-master cards (master picker top, then service
 *    list with checkboxes). Save at sticky bottom. Less efficient but
 *    mobile-functional.»
 *
 * Layout (single column, per-master collapsed list):
 *   - Header «← Услуги мастеров» with optional dirty dot
 *   - Search bar (debounced 300ms) + filter chips «Все» / «С пробелами»
 *   - Master rows: row tap toggles inline expand; expanded panel shows
 *     service checkboxes with «Имя · NN мин» (price omitted to keep
 *     line height tight on small screens)
 *   - Orphan warnings (§648 — always rendered when present)
 *   - Sticky bottom CTA «N изменений в очереди» + [Применить всё]
 *
 * Conflict semantics (§671-672 + PR #411 backend):
 *   - Backend's snapshot_token = HMAC of MAX(updated_at). On 409, the
 *     backend returns ``{applied: 0, conflicts: [...]}``. Per spec the
 *     caller MAY merge or retry — all-or-nothing.
 *   - This UI never silently overwrites: 409 surfaces a banner with
 *     [Применить поверх] (re-send with the fresh token) and
 *     [Перезагрузить] (drop edits + refetch). Pending edits stay in
 *     state until the user picks.
 *
 * Bridge API:
 *   - BackButton.show() → /admin/team; tap with dirty → confirm modal
 *   - enableClosingConfirmation() while any pending edit exists
 *   - hapticSelection() on checkbox toggle
 *   - hapticNotify('success'/'error') on save outcomes
 *
 * Receptionist read-only:
 *   - Spec §642 doesn't explicitly call out Receptionist for this
 *     screen but the broader §MM0 chrome treats Receptionist as
 *     read-only for editing flows. We render the matrix with
 *     ``disabled`` checkboxes + a padlock banner for that role; the
 *     backend ``@require_admin_role`` decorator also gates the API.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { Snackbar } from "../../components/Snackbar";
import { StateError } from "../../components/StateError";
import { ApiError } from "../../lib/api";
import {
  getServicesMapping,
  patchServicesMapping,
  type MeResponse,
  type ServicesMappingChange,
  type ServicesMappingConflictEntry,
  type ServicesMappingMaster,
  type ServicesMappingPayload,
  type ServicesMappingService,
} from "../../lib/admin-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../../lib/max-sdk";

interface Props {
  me: MeResponse;
}

type FilterChip = "all" | "orphans";

/**
 * Per-master pending edits are tracked as ``Map<master_id, Set<service_id>>``
 * holding the INTENDED selection. Toggling twice cleanly reverts. Bulk
 * save flattens to a flat ``changes[]`` array via :func:`computeChanges`.
 */

/** Build initial selection map from server payload. */
function buildBaseline(p: ServicesMappingPayload): Map<string, Set<string>> {
  const m = new Map<string, Set<string>>();
  for (const master of p.masters) m.set(master.id, new Set());
  for (const pair of p.mapping) {
    const s = m.get(pair.master_id);
    if (s) s.add(pair.service_id);
  }
  return m;
}

/** Deep-clone a master→Set map. */
function cloneBaseline(
  baseline: Map<string, Set<string>>,
): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const [k, v] of baseline) out.set(k, new Set(v));
  return out;
}

/** Compute diff (add / remove) between baseline and edits, per master. */
function computeChanges(
  baseline: Map<string, Set<string>>,
  edits: Map<string, Set<string>>,
): ServicesMappingChange[] {
  const changes: ServicesMappingChange[] = [];
  for (const [masterId, intended] of edits) {
    const base = baseline.get(masterId) ?? new Set<string>();
    for (const sid of intended) {
      if (!base.has(sid)) {
        changes.push({ master_id: masterId, service_id: sid, enabled: true });
      }
    }
    for (const sid of base) {
      if (!intended.has(sid)) {
        changes.push({ master_id: masterId, service_id: sid, enabled: false });
      }
    }
  }
  return changes;
}

/** Count dirty cells for the "N изменений" footer. */
function countDirty(
  baseline: Map<string, Set<string>>,
  edits: Map<string, Set<string>>,
): number {
  return computeChanges(baseline, edits).length;
}

/** True iff a single master has any dirty cell vs baseline. */
function isMasterDirty(
  baseline: Map<string, Set<string>>,
  edits: Map<string, Set<string>>,
  masterId: string,
): boolean {
  const base = baseline.get(masterId) ?? new Set<string>();
  const cur = edits.get(masterId) ?? new Set<string>();
  if (base.size !== cur.size) return true;
  for (const s of cur) if (!base.has(s)) return true;
  return false;
}

function firstName(fullName: string): string {
  const parts = (fullName || "").trim().split(/\s+/);
  return parts[0] || fullName;
}

function fold(s: string): string {
  return s.toLocaleLowerCase("ru-RU");
}

export function AdminServicesMatrixScreen({ me }: Props) {
  const navigate = useNavigate();
  const location = useLocation();

  // Deeplink ?master_id=<uuid> — auto-expand that master on mount
  // (called from MM3 «Редактировать (N) →» button).
  const initialExpandedMaster = useMemo(() => {
    const q = new URLSearchParams(location.search);
    return q.get("master_id");
  }, [location.search]);

  const [payload, setPayload] = useState<ServicesMappingPayload | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Baseline = server truth. ``edits`` mutates as the user toggles.
  // Both are refs-as-state so React re-renders on change.
  const [baseline, setBaseline] = useState<Map<string, Set<string>>>(new Map());
  const [edits, setEdits] = useState<Map<string, Set<string>>>(new Map());

  const [snapshotToken, setSnapshotToken] = useState<string>("");

  // UI state.
  const [expandedMaster, setExpandedMaster] = useState<string | null>(
    initialExpandedMaster,
  );
  const [search, setSearch] = useState<string>("");
  const [debouncedSearch, setDebouncedSearch] = useState<string>("");
  const [filter, setFilter] = useState<FilterChip>("all");
  const [saving, setSaving] = useState<boolean>(false);
  const [confirmApply, setConfirmApply] = useState<boolean>(false);
  const [confirmDiscard, setConfirmDiscard] = useState<boolean>(false);
  const [confirmLeave, setConfirmLeave] = useState<boolean>(false);

  // Conflict state — preserves user edits per §MM4.
  const [conflict, setConflict] = useState<
    ServicesMappingConflictEntry[] | null
  >(null);

  // Offline / save-error banner.
  const [errBanner, setErrBanner] = useState<string>("");

  // Toast queue.
  const [toast, setToast] = useState<string>("");

  // Reload guard: in-flight AbortController to cancel on unmount /
  // re-fetch trigger.
  const reloadCtrl = useRef<AbortController | null>(null);

  const canEdit = me.is_owner || me.is_admin;
  const isReadOnly = !canEdit;

  // --- bridge: BackButton + closing confirmation ---
  const dirtyCount = useMemo(
    () => countDirty(baseline, edits),
    [baseline, edits],
  );

  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      if (dirtyCount > 0) {
        setConfirmLeave(true);
      } else {
        navigate("/admin/team");
      }
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirtyCount]);

  useEffect(() => {
    setClosingConfirmation(dirtyCount > 0 || saving);
  }, [dirtyCount, saving]);

  // --- search debounce (300ms) ---
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // --- initial load ---
  const reload = useCallback(async (preserveEdits: boolean) => {
    reloadCtrl.current?.abort();
    const ctrl = new AbortController();
    reloadCtrl.current = ctrl;
    setLoading(true);
    setLoadErr(null);
    try {
      const data = await getServicesMapping({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setPayload(data);
      const fresh = buildBaseline(data);
      setBaseline(fresh);
      if (!preserveEdits) {
        setEdits(cloneBaseline(fresh));
      } else {
        // Keep user's intended selections, but for masters that no
        // longer exist in the response drop them silently.
        setEdits((prev) => {
          const next = new Map<string, Set<string>>();
          for (const m of data.masters) {
            next.set(m.id, prev.get(m.id) ?? new Set(fresh.get(m.id) ?? []));
          }
          return next;
        });
      }
      setSnapshotToken(data.snapshot_token);
    } catch (e) {
      if ((e as DOMException | undefined)?.name === "AbortError") return;
      setLoadErr(e);
    } finally {
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload(false);
    return () => reloadCtrl.current?.abort();
  }, [reload]);

  // --- derived: filtered master rows ---
  const filteredMasters = useMemo<ServicesMappingMaster[]>(() => {
    if (!payload) return [];
    const q = fold(debouncedSearch);
    return payload.masters.filter((m) => {
      if (filter === "orphans") {
        const cur = edits.get(m.id) ?? new Set();
        if (cur.size > 0) return false;
      }
      if (!q) return true;
      // Match master name OR any service name they perform / could perform.
      if (fold(m.name).includes(q)) return true;
      const intended = edits.get(m.id) ?? new Set();
      for (const sid of intended) {
        const svc = payload.services.find((s) => s.id === sid);
        if (svc && fold(svc.name).includes(q)) return true;
      }
      // Also surface masters whose row stays visible if the search hit
      // ANY catalog service — the user might be trying to assign that
      // service to this master.
      for (const svc of payload.services) {
        if (fold(svc.name).includes(q)) return true;
      }
      return false;
    });
  }, [payload, debouncedSearch, filter, edits]);

  // --- helpers ---
  const toggleCell = useCallback(
    (masterId: string, serviceId: string) => {
      if (isReadOnly) return;
      hapticSelection();
      setEdits((prev) => {
        const next = new Map(prev);
        const set = new Set(next.get(masterId) ?? new Set<string>());
        if (set.has(serviceId)) set.delete(serviceId);
        else set.add(serviceId);
        next.set(masterId, set);
        return next;
      });
    },
    [isReadOnly],
  );

  const toggleExpand = useCallback((masterId: string) => {
    setExpandedMaster((cur) => (cur === masterId ? null : masterId));
  }, []);

  const resetMasterEdits = useCallback(
    (masterId: string) => {
      setEdits((prev) => {
        const next = new Map(prev);
        const base = baseline.get(masterId) ?? new Set();
        next.set(masterId, new Set(base));
        return next;
      });
    },
    [baseline],
  );

  const resetAllEdits = useCallback(() => {
    setEdits(cloneBaseline(baseline));
    setConfirmDiscard(false);
  }, [baseline]);

  // --- save ---
  const applyChanges = useCallback(
    async (tokenOverride?: string) => {
      if (saving) return;
      const changes = computeChanges(baseline, edits);
      if (changes.length === 0) {
        setToast("Нет изменений");
        setConfirmApply(false);
        return;
      }
      setSaving(true);
      setErrBanner("");
      try {
        const result = await patchServicesMapping({
          changes,
          snapshot_token: tokenOverride ?? snapshotToken,
        });
        if ("__conflict" in result) {
          // 409 — preserve user edits, surface banner.
          setConflict(result.conflicts);
          hapticNotify("error");
          // Refresh snapshot token via a silent re-fetch (preserve
          // edits) so [Применить поверх] uses the latest token.
          await reload(true);
          setConfirmApply(false);
          return;
        }
        hapticNotify("success");
        // Success: server is now authoritative. Sync baseline = new
        // edits and clear conflict banner.
        const newBaseline = cloneBaseline(edits);
        setBaseline(newBaseline);
        setSnapshotToken(result.new_snapshot_token);
        setConflict(null);
        setConfirmApply(false);
        setToast(
          result.applied === 1
            ? "✓ Сохранено"
            : `✓ Сохранено: ${result.applied} ${pluralChange(result.applied)}`,
        );
        // Refresh payload to pick up new orphans + the canonical
        // mapping rows. Preserve edits in case the user kept editing.
        await reload(false);
      } catch (e) {
        hapticNotify("error");
        if (e instanceof ApiError) {
          setErrBanner(e.detail || "Не получилось сохранить");
        } else {
          setErrBanner("Связь пропала. Проверьте интернет.");
        }
      } finally {
        setSaving(false);
      }
    },
    [baseline, edits, snapshotToken, saving, reload],
  );

  // --- load / error gates ---
  if (loadErr) {
    return (
      <div className="screen">
        <h1 className="screen__title">Услуги мастеров</h1>
        <StateError err={loadErr} onRetry={() => void reload(false)} />
        <AdminTabBar />
      </div>
    );
  }

  if (loading && payload === null) {
    return (
      <div className="screen">
        <h1 className="screen__title">Услуги мастеров</h1>
        <div className="callout" role="status">
          Загружаем матрицу…
        </div>
        <AdminTabBar />
      </div>
    );
  }

  if (!payload) {
    return (
      <div className="screen">
        <h1 className="screen__title">Услуги мастеров</h1>
        <div className="callout callout--danger" role="alert">
          Не удалось загрузить данные.
        </div>
        <AdminTabBar />
      </div>
    );
  }

  const hasServices = payload.services.length > 0;
  const hasMasters = payload.masters.length > 0;

  // --- empty branches ---
  if (!hasServices) {
    return (
      <div className="screen">
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-2)",
            marginBottom: "var(--s-3)",
          }}
        >
          <button
            type="button"
            className="admin-flow-back"
            onClick={() => navigate("/admin/team")}
            aria-label="К команде"
            style={backLinkStyle}
          >
            ← Услуги мастеров
          </button>
        </header>
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>
            Сначала добавьте услуги в каталог.
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            disabled
            title="Скоро — Services Management screen"
          >
            Добавить услугу →
          </button>
        </div>
        <AdminTabBar />
      </div>
    );
  }

  if (!hasMasters) {
    return (
      <div className="screen">
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-2)",
            marginBottom: "var(--s-3)",
          }}
        >
          <button
            type="button"
            className="admin-flow-back"
            onClick={() => navigate("/admin/team")}
            aria-label="К команде"
            style={backLinkStyle}
          >
            ← Услуги мастеров
          </button>
        </header>
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>Сначала добавьте мастеров в команду.</p>
          <button
            type="button"
            className="cta-bar__button"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => navigate("/admin/team/invite")}
          >
            Пригласить мастера →
          </button>
        </div>
        <AdminTabBar />
      </div>
    );
  }

  // --- main render ---
  return (
    <div
      className="screen"
      style={{ paddingBottom: dirtyCount > 0 ? "96px" : undefined }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
          marginBottom: "var(--s-3)",
        }}
      >
        <button
          type="button"
          className="admin-flow-back"
          onClick={() => {
            if (dirtyCount > 0) setConfirmLeave(true);
            else navigate("/admin/team");
          }}
          aria-label="Назад к команде"
          style={backLinkStyle}
        >
          <span style={{ color: "var(--c-text-secondary)" }}>← </span>
          <span style={{ fontWeight: 600 }}>Услуги мастеров</span>
        </button>
        {dirtyCount > 0 && (
          <span
            aria-label={`несохранённые изменения: ${dirtyCount}`}
            title="Несохранённые изменения"
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: "var(--c-accent, #ff8a00)",
              flexShrink: 0,
            }}
          />
        )}
      </header>

      {isReadOnly && (
        <div
          className="callout"
          role="status"
          style={{ marginBottom: "var(--s-3)" }}
        >
          🔒 Только владелец или администратор могут менять привязки.
        </div>
      )}

      {/* Search + filter chips */}
      <div style={{ margin: "var(--s-2) 0 var(--s-3)" }}>
        <input
          type="search"
          inputMode="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="🔍 Найти мастера или услугу"
          aria-label="Поиск по мастеру или услуге"
          className="admin-search-input"
        />
      </div>
      <div
        role="tablist"
        aria-label="Фильтр"
        className="admin-filter-tabs"
        style={{ marginBottom: "var(--s-3)" }}
      >
        <button
          type="button"
          role="tab"
          aria-selected={filter === "all"}
          className={`admin-filter-tabs__tab${filter === "all" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => {
            hapticSelection();
            setFilter("all");
          }}
        >
          Все
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filter === "orphans"}
          className={`admin-filter-tabs__tab${filter === "orphans" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => {
            hapticSelection();
            setFilter("orphans");
          }}
        >
          С пробелами
        </button>
      </div>

      {/* Orphan warnings — service orphans (services nobody performs). */}
      {payload.orphans.services_without_masters.length > 0 && filter === "all" && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
          <p style={{ margin: 0, fontWeight: 600 }}>
            {`Услуги без мастеров (${payload.orphans.services_without_masters.length}):`}
          </p>
          <ul style={{ margin: "var(--s-1) 0 0", paddingInlineStart: "var(--s-3)" }}>
            {payload.orphans.services_without_masters.map((sid) => {
              const svc = payload.services.find((s) => s.id === sid);
              return (
                <li key={sid}>
                  {svc ? svc.name : sid} — никто не выполняет.
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Conflict banner — preserve user edits */}
      {conflict && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
          <p style={{ margin: 0, fontWeight: 600 }}>
            Кто-то изменил привязки.
          </p>
          <p style={{ margin: "var(--s-1) 0" }}>
            {`Ваши ${dirtyCount} ${pluralChange(dirtyCount)} сохранены — выберите, что делать.`}
          </p>
          <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
            <button
              type="button"
              className="cta-bar__button"
              onClick={() => void applyChanges(snapshotToken)}
              disabled={saving}
            >
              Применить поверх
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setConflict(null);
                void reload(false);
              }}
              disabled={saving}
            >
              Перезагрузить
            </button>
          </div>
        </div>
      )}

      {errBanner && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>{errBanner}</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => {
              setErrBanner("");
              void applyChanges();
            }}
          >
            Повторить
          </button>
        </div>
      )}

      {/* Master list */}
      {filteredMasters.length === 0 ? (
        <div className="callout" role="status">
          {debouncedSearch
            ? "Никого не нашли по запросу."
            : filter === "orphans"
              ? "Все мастера выполняют хотя бы одну услугу."
              : "В команде пока нет активных мастеров."}
        </div>
      ) : (
        <ul
          className="admin-master-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {filteredMasters.map((m) => {
            const intended = edits.get(m.id) ?? new Set<string>();
            const count = intended.size;
            const dirty = isMasterDirty(baseline, edits, m.id);
            const isExpanded = expandedMaster === m.id;
            return (
              <li
                key={m.id}
                style={{
                  borderBottom: "1px solid var(--c-border, rgba(0,0,0,0.08))",
                }}
              >
                <button
                  type="button"
                  className="master-card"
                  onClick={() => toggleExpand(m.id)}
                  aria-expanded={isExpanded}
                  aria-controls={`master-services-${m.id}`}
                  style={{ width: "100%" }}
                >
                  <span
                    className="master-card__avatar"
                    style={{ background: "var(--c-surface-2)" }}
                    aria-hidden="true"
                  >
                    {firstName(m.name).charAt(0).toUpperCase()}
                  </span>
                  <span style={{ flex: 1, minWidth: 0, textAlign: "start" }}>
                    <span className="master-card__name">{m.name}</span>
                    <span
                      style={{
                        display: "flex",
                        gap: "var(--s-2)",
                        marginTop: "var(--s-1)",
                        flexWrap: "wrap",
                      }}
                    >
                      {m.invite_status === "pending" && (
                        <span className="admin-chip admin-chip--warn">
                          приглашён
                        </span>
                      )}
                      <span className="admin-chip">
                        {`${count} ${pluralService(count)} ${isExpanded ? "▾" : "▸"}`}
                      </span>
                      {dirty && (
                        <span
                          className="admin-chip admin-chip--warn"
                          aria-label="есть несохранённые изменения"
                        >
                          ● изменения
                        </span>
                      )}
                    </span>
                  </span>
                </button>

                {isExpanded && (
                  <div
                    id={`master-services-${m.id}`}
                    style={{
                      padding: "var(--s-2) var(--s-3) var(--s-3)",
                      background: "var(--c-surface-1)",
                    }}
                  >
                    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                      {payload.services.map((svc) => (
                        <ServiceCheckboxRow
                          key={svc.id}
                          service={svc}
                          checked={intended.has(svc.id)}
                          disabled={isReadOnly || saving}
                          onToggle={() => toggleCell(m.id, svc.id)}
                          highlight={debouncedSearch}
                        />
                      ))}
                    </ul>
                    {dirty && !isReadOnly && (
                      <div
                        style={{
                          display: "flex",
                          gap: "var(--s-2)",
                          marginTop: "var(--s-3)",
                        }}
                      >
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={saving}
                          onClick={() => resetMasterEdits(m.id)}
                          style={{ flex: 1 }}
                        >
                          Отмена
                        </button>
                        <button
                          type="button"
                          className="cta-bar__button"
                          disabled={saving}
                          onClick={() => setConfirmApply(true)}
                          style={{ flex: 1 }}
                        >
                          {`Сохранить (${perMasterDiffCount(baseline, edits, m.id)})`}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {/* Master orphan list — at bottom (less prominent than service orphans). */}
      {payload.orphans.masters_without_services.length > 0 && filter === "all" && (
        <div
          className="callout"
          role="status"
          style={{ marginTop: "var(--s-3)" }}
        >
          <p style={{ margin: 0, fontWeight: 600 }}>
            {`Мастера без услуг (${payload.orphans.masters_without_services.length}):`}
          </p>
          <ul style={{ margin: "var(--s-1) 0 0", paddingInlineStart: "var(--s-3)" }}>
            {payload.orphans.masters_without_services.map((mid) => {
              const master = payload.masters.find((m) => m.id === mid);
              return (
                <li key={mid}>
                  {master ? master.name : mid} — не выполняет ни одной услуги.
                </li>
              );
            })}
          </ul>
          <p
            style={{
              margin: "var(--s-1) 0 0",
              color: "var(--c-text-secondary)",
            }}
          >
            Пока не выберете услуги, помощник не сможет их предлагать.
          </p>
        </div>
      )}

      {/* Sticky bottom action bar */}
      {dirtyCount > 0 && !isReadOnly && (
        <div
          role="region"
          aria-label="Несохранённые изменения"
          style={{
            position: "fixed",
            insetInline: 0,
            bottom: 0,
            padding: "var(--s-2) var(--s-3) calc(var(--s-3) + env(safe-area-inset-bottom, 0px))",
            background: "var(--c-surface-0, #fff)",
            borderTop: "1px solid var(--c-border, rgba(0,0,0,0.08))",
            display: "flex",
            alignItems: "center",
            gap: "var(--s-2)",
            zIndex: 5,
          }}
        >
          <span style={{ flex: 1, minWidth: 0 }}>
            {`${dirtyCount} ${pluralChange(dirtyCount)} в очереди`}
          </span>
          <button
            type="button"
            className="btn-secondary"
            disabled={saving}
            onClick={() => setConfirmDiscard(true)}
          >
            Сбросить
          </button>
          <button
            type="button"
            className="cta-bar__button"
            disabled={saving}
            onClick={() => setConfirmApply(true)}
          >
            {saving ? "Сохраняем…" : "Применить всё"}
          </button>
        </div>
      )}

      {/* Confirm "Применить всё" */}
      {confirmApply && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Применить все изменения?"
          onClick={() => !saving && setConfirmApply(false)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              Применить все изменения?
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`Будет применено ${dirtyCount} ${pluralChange(dirtyCount)}. Помощник сразу учтёт новые привязки.`}
            </p>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={saving}
                onClick={() => setConfirmApply(false)}
                style={{ flex: 1 }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={saving}
                onClick={() => void applyChanges()}
                style={{ flex: 1 }}
              >
                {saving ? "Сохраняем…" : "Применить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm "Сбросить" */}
      {confirmDiscard && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Сбросить изменения?"
          onClick={() => setConfirmDiscard(false)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              Сбросить изменения?
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`Все ${dirtyCount} ${pluralChange(dirtyCount)} будут отменены.`}
            </p>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmDiscard(false)}
                style={{ flex: 1 }}
              >
                Нет
              </button>
              <button
                type="button"
                className="cta-bar__button"
                onClick={resetAllEdits}
                style={{ flex: 1 }}
              >
                Сбросить
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm leave (back tap with dirty) */}
      {confirmLeave && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Выйти без сохранения?"
          onClick={() => setConfirmLeave(false)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              Выйти без сохранения?
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`У вас ${dirtyCount} ${pluralChange(dirtyCount)} без сохранения.`}
            </p>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmLeave(false)}
                style={{ flex: 1 }}
              >
                Остаться
              </button>
              <button
                type="button"
                className="cta-bar__button admin-sheet__action--danger"
                onClick={() => {
                  setConfirmLeave(false);
                  navigate("/admin/team");
                }}
                style={{ flex: 1 }}
              >
                Выйти
              </button>
            </div>
          </div>
        </div>
      )}

      <Snackbar
        visible={!!toast}
        message={toast}
        durationMs={2000}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />

      <AdminTabBar />
    </div>
  );
}

// --- helpers (module-scope) ---------------------------------------------

const backLinkStyle = {
  background: "transparent",
  border: 0,
  padding: 0,
  font: "inherit",
  color: "var(--c-text-primary)",
  cursor: "pointer",
  textAlign: "start" as const,
  flex: 1,
  minWidth: 0,
};

function pluralChange(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "изменений";
  if (mod10 === 1) return "изменение";
  if (mod10 >= 2 && mod10 <= 4) return "изменения";
  return "изменений";
}

function pluralService(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "услуг";
  if (mod10 === 1) return "услуга";
  if (mod10 >= 2 && mod10 <= 4) return "услуги";
  return "услуг";
}

function perMasterDiffCount(
  baseline: Map<string, Set<string>>,
  edits: Map<string, Set<string>>,
  masterId: string,
): number {
  const base = baseline.get(masterId) ?? new Set<string>();
  const cur = edits.get(masterId) ?? new Set<string>();
  let n = 0;
  for (const s of cur) if (!base.has(s)) n++;
  for (const s of base) if (!cur.has(s)) n++;
  return n;
}

// --- service checkbox row -----------------------------------------------

interface ServiceRowProps {
  service: ServicesMappingService;
  checked: boolean;
  disabled: boolean;
  onToggle: () => void;
  highlight: string;
}

function ServiceCheckboxRow({
  service,
  checked,
  disabled,
  onToggle,
  highlight,
}: ServiceRowProps) {
  const lowered = highlight ? fold(highlight) : "";
  const isMatch = lowered ? fold(service.name).includes(lowered) : false;
  return (
    <li style={{ padding: "var(--s-1) 0" }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.55 : 1,
        }}
      >
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={onToggle}
        />
        <span
          style={{
            fontWeight: isMatch ? 600 : 400,
            flex: 1,
            minWidth: 0,
          }}
        >
          {service.name}
        </span>
        <span style={{ color: "var(--c-text-secondary)" }}>
          {service.duration_min ? `${service.duration_min} мин` : "—"}
        </span>
      </label>
    </li>
  );
}
