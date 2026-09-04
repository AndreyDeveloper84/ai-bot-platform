/**
 * Admin MM3 — master detail / edit (Bundle 2/3 of TL admin chrome closure).
 *
 * Route: /admin/team/:masterId
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM3
 * (lines 492-585). Verbatim Russian copy throughout.
 *
 * Spec quote (§MM3, line 583, Mini App parity):
 *
 *   «Same content, single-column stack. Sticky bottom bar with [Сохранить]
 *    only when dirty. BackButton wired with closing-confirmation.»
 *
 * Spec quote (§MM3, line 531, bio counter):
 *
 *   «280 символов max»
 *
 * MM3 IN scope (this screen):
 *   - Profile: photo upload + name / bio / specialization inline-edit
 *   - View-only fields with «скоро» tooltips: role, contact MAX handle,
 *     services list, schedule summary, stats, notifications, photo delete
 *   - Audit feed (last 10 + cursor pagination)
 *   - Owner-only «Деактивировать» routes to existing MM5 flow
 *   - State matrix: 10 branches per §572-582
 *
 * OUT of scope (disabled tooltips on this screen — Bundle 3 / later PR):
 *   - Role change, contact change, services edit (MM4), schedule edit,
 *     stats analytics, notifications toggles, nudge button, resend /
 *     cancel invite, photo delete (no DELETE endpoint yet).
 *
 * Edit semantics (§MM3 lines 546-555):
 *   - Click field → inline editor
 *   - [Сохранить] PATCH single field optimistic
 *   - [Отмена] reverts
 *   - Success haptic + «✓ Сохранено» 2s toast
 *   - Error keeps value + inline error
 *   - Dirty → enableClosingConfirmation()
 *
 * Bridge API:
 *   - BackButton.show() → /admin/team
 *   - enableClosingConfirmation() while any inline editor is dirty
 *   - hapticSelection() on inline-editor open
 *   - hapticNotify('success') / hapticNotify('error') on save outcomes
 *   - Plain ``<input type=file>`` for photo upload (§MM3 line 551)
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Snackbar } from "../../components/Snackbar";
import { StateError } from "../../components/StateError";
import { ApiError } from "../../lib/api";
import {
  getMasterAudit,
  getMasterDetail,
  patchMaster,
  reactivateMaster,
  uploadMasterPhoto,
  type AuditEvent,
  type MasterDetail,
  type MasterPatchPayload,
  type MeResponse,
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

/** §MM3 line 531 — bio UI cap (server-side still allows up to 1000). */
const BIO_UI_MAX = 280;
const NAME_UI_MAX = 200;
const SPECIALIZATION_UI_MAX = 255;

/** §551 photo constraints — keep aligned with backend ``PHOTO_MAX_BYTES``. */
const PHOTO_MAX_BYTES = 10 * 1024 * 1024;
const PHOTO_MIME_ALLOWLIST = new Set<string>([
  "image/jpeg",
  "image/png",
  "image/webp",
]);

const AUDIT_PAGE_SIZE = 10;

type EditableField = "name" | "specialization" | "bio";

interface EditorState {
  field: EditableField;
  value: string;
  saving: boolean;
  err: string;
}

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("");
}

function firstName(fullName: string): string {
  const parts = (fullName || "").trim().split(/\s+/);
  return parts[0] || fullName;
}

function lastInitial(fullName: string): string {
  const parts = (fullName || "").trim().split(/\s+/);
  if (parts.length < 2) return "";
  const last = parts[parts.length - 1];
  return last ? `${last.charAt(0).toUpperCase()}.` : "";
}

/**
 * «16 мая 14:22» — drop year if same as current; otherwise include it.
 * Render in ru-RU locale. Fallback gracefully for invalid input.
 */
function formatOccurred(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameYear = d.getFullYear() === now.getFullYear();
    const day = d.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      ...(sameYear ? {} : { year: "numeric" }),
    });
    const time = d.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${day} ${time}`;
  } catch {
    return iso;
  }
}

/**
 * Map a backend audit slug + payload → human-readable action phrase.
 * Falls back to a neutral «совершил(а) действие» for unknown slugs so
 * the feed never goes blank.
 */
function actionToHuman(ev: AuditEvent): string {
  const slug = ev.action || "";
  const payload = ev.payload || {};
  const fieldsChanged = Array.isArray(payload["fields_changed"])
    ? (payload["fields_changed"] as string[])
    : [];

  switch (slug) {
    case "master.profile_updated_by_admin": {
      if (fieldsChanged.length === 0) return "обновил(а) профиль";
      if (fieldsChanged.length === 1) {
        switch (fieldsChanged[0]) {
          case "name":
            return "обновил(а) имя";
          case "bio":
            return "обновил(а) био";
          case "specialization":
            return "обновил(а) специализацию";
          case "experience":
            return "обновил(а) опыт";
          case "is_active":
            return "изменил(а) статус";
          default:
            return "обновил(а) профиль";
        }
      }
      return "обновил(а) профиль";
    }
    case "master.photo_updated_by_admin":
      return "обновил(а) фото";
    case "master.invited":
      return "пригласил(а) мастера";
    case "master.invite_dispatched":
      return "выслал(а) приглашение";
    case "master.onboarding_accepted":
      return "приняла приглашение";
    case "master.onboarding_rejected":
      return "отклонил(а) приглашение";
    case "master.services_changed":
      return "изменил(а) список услуг";
    case "master.deactivated":
      return "деактивировал(а) мастера";
    case "master.reactivated":
      return "восстановил(а) мастера";
    case "master.deactivation_started":
      return "начал(а) деактивацию";
    case "master.bookings_reassigned":
      return "перевёл записи";
    case "master.bookings_cancelled":
      return "отменил(а) записи";
    case "master.profile_initialized":
      return "заполнил(а) профиль";
    default:
      return "совершил(а) действие";
  }
}

/**
 * Pick the actor display string for an audit row. We don't have a
 * resolver for actor_id → name on the client (would need an extra
 * endpoint), so we fall back to the role label per spec §MM3 footnote
 * «Карина пригласила». Owner / Admin / Master / Receptionist all map
 * to a sensible Russian label.
 */
function actorLabel(ev: AuditEvent): string {
  const role = (ev.actor_role || "").toLowerCase();
  switch (role) {
    case "owner":
      return "Владелец";
    case "admin":
      return "Администратор";
    case "receptionist":
      return "Ресепшен";
    case "master":
      return "Мастер";
    default:
      return "Администратор";
  }
}

/**
 * Status chip — Active / Pending invite / Inactive. The spec calls for
 * a coloured dot prefix; we lean on existing ``.admin-chip`` /
 * ``.admin-chip--warn`` styles. Inactive uses a neutral chip with a
 * leading dot character.
 */
function statusChip(master: MasterDetail): { label: string; cls: string } {
  if (!master.is_active) {
    return { label: "● Неактивен", cls: "admin-chip" };
  }
  if (master.invite_status === "pending") {
    return { label: "● Приглашён", cls: "admin-chip admin-chip--warn" };
  }
  return { label: "● Активен", cls: "admin-chip" };
}

export function AdminMasterDetailScreen({ me }: Props) {
  const navigate = useNavigate();
  const { masterId = "" } = useParams<{ masterId: string }>();

  const [master, setMaster] = useState<MasterDetail | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Inline editor (single editor open at a time — simplest model that
  // matches the spec's «click field → inline» pattern).
  const [editor, setEditor] = useState<EditorState | null>(null);

  // Audit feed.
  const [auditItems, setAuditItems] = useState<AuditEvent[]>([]);
  const [auditCursor, setAuditCursor] = useState<string | null>(null);
  const [auditLoading, setAuditLoading] = useState<boolean>(false);
  const [auditErr, setAuditErr] = useState<unknown>(null);

  // Photo upload state.
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [photoUploading, setPhotoUploading] = useState<boolean>(false);
  const [photoErr, setPhotoErr] = useState<string>("");

  // Reactivation dialog (reuses MM1's pattern).
  const [reactivateOpen, setReactivateOpen] = useState<boolean>(false);
  const [reactivateNotify, setReactivateNotify] = useState<boolean>(true);
  const [reactivating, setReactivating] = useState<boolean>(false);

  // 409 concurrent-edit banner.
  const [conflictBanner, setConflictBanner] = useState<boolean>(false);
  // Network-offline banner.
  const [offlineBanner, setOfflineBanner] = useState<boolean>(false);

  // Toast.
  const [toast, setToast] = useState<string>("");

  // --- bridge: BackButton + closing-confirmation ---
  const isDirty = editor !== null && editor.value !== editorOriginalValue(master, editor);
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/admin/team");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setClosingConfirmation(isDirty || photoUploading);
  }, [isDirty, photoUploading]);

  // --- initial load ---
  const fetchDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!masterId) return;
      setLoading(true);
      setLoadErr(null);
      try {
        const m = await getMasterDetail(masterId, { signal });
        if (signal?.aborted) return;
        setMaster(m);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setLoadErr(e);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [masterId],
  );

  const fetchAuditPage = useCallback(
    async (cursor?: string, signal?: AbortSignal) => {
      if (!masterId) return;
      setAuditLoading(true);
      setAuditErr(null);
      try {
        const res = await getMasterAudit(masterId, cursor, { signal });
        if (signal?.aborted) return;
        setAuditItems((prev) => (cursor ? [...prev, ...res.items] : res.items));
        setAuditCursor(res.next_cursor);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setAuditErr(e);
      } finally {
        if (!signal?.aborted) setAuditLoading(false);
      }
    },
    [masterId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void fetchDetail(controller.signal);
    void fetchAuditPage(undefined, controller.signal);
    return () => controller.abort();
  }, [fetchDetail, fetchAuditPage]);

  // --- editor open / cancel ---
  const openEditor = useCallback(
    (field: EditableField) => {
      if (!master) return;
      hapticSelection();
      setEditor({
        field,
        value:
          field === "name"
            ? master.name
            : field === "specialization"
              ? master.specialization
              : master.bio,
        saving: false,
        err: "",
      });
    },
    [master],
  );

  const cancelEditor = useCallback(() => {
    setEditor(null);
  }, []);

  // --- save handler ---
  const saveEditor = useCallback(async () => {
    if (!editor || !master) return;
    const trimmed =
      editor.field === "name" ? editor.value.trim() : editor.value;
    // Client-side validation (mirrors backend; server is still authority).
    if (editor.field === "name" && !trimmed) {
      setEditor({ ...editor, err: "Имя не может быть пустым" });
      hapticNotify("error");
      return;
    }
    if (editor.field === "name" && trimmed.length > NAME_UI_MAX) {
      setEditor({ ...editor, err: `Имя не длиннее ${NAME_UI_MAX} символов` });
      hapticNotify("error");
      return;
    }
    if (
      editor.field === "specialization" &&
      trimmed.length > SPECIALIZATION_UI_MAX
    ) {
      setEditor({
        ...editor,
        err: `Не длиннее ${SPECIALIZATION_UI_MAX} символов`,
      });
      hapticNotify("error");
      return;
    }
    if (editor.field === "bio" && trimmed.length > BIO_UI_MAX) {
      setEditor({ ...editor, err: `Био не длиннее ${BIO_UI_MAX} символов` });
      hapticNotify("error");
      return;
    }

    setEditor({ ...editor, saving: true, err: "" });
    setOfflineBanner(false);
    try {
      const patch: Partial<MasterPatchPayload> = { [editor.field]: trimmed };
      const updated = await patchMaster(master.id, patch);
      hapticNotify("success");
      setMaster(updated);
      setEditor(null);
      setToast("✓ Сохранено");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 409) {
          setConflictBanner(true);
          setEditor({ ...editor, saving: false, err: "" });
        } else if (e.status >= 500) {
          setEditor({
            ...editor,
            saving: false,
            err: "Сервер сейчас не отвечает. Попробуйте ещё раз.",
          });
        } else {
          setEditor({
            ...editor,
            saving: false,
            err: e.detail || "Не получилось сохранить",
          });
        }
      } else {
        setOfflineBanner(true);
        setEditor({ ...editor, saving: false, err: "" });
      }
      hapticNotify("error");
    }
  }, [editor, master]);

  // --- photo upload ---
  const handlePhotoSelect = useCallback(
    async (file: File) => {
      if (!master) return;
      setPhotoErr("");
      if (file.size > PHOTO_MAX_BYTES) {
        setPhotoErr("Фото больше 10 МБ. Уменьшите размер.");
        hapticNotify("error");
        return;
      }
      const mime = (file.type || "").toLowerCase();
      if (!PHOTO_MIME_ALLOWLIST.has(mime)) {
        setPhotoErr(
          "Поддерживаются только JPEG, PNG и WebP.",
        );
        hapticNotify("error");
        return;
      }
      setPhotoUploading(true);
      try {
        const { photo_url } = await uploadMasterPhoto(master.id, file);
        hapticNotify("success");
        setMaster({ ...master, photo_url });
        setToast("✓ Фото обновлено");
      } catch (e) {
        if (e instanceof ApiError) {
          setPhotoErr(e.detail || "Не получилось загрузить фото");
        } else {
          setPhotoErr("Связь пропала. Проверьте интернет.");
        }
        hapticNotify("error");
      } finally {
        setPhotoUploading(false);
        // Allow re-selecting the same file (browsers de-dupe by name).
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [master],
  );

  // --- reactivation (reuse MM1 dialog pattern) ---
  const handleReactivateConfirm = useCallback(async () => {
    if (!master) return;
    setReactivating(true);
    try {
      await reactivateMaster(master.id, reactivateNotify);
      setReactivateOpen(false);
      setToast(`${firstName(master.name)} восстановлен(а)`);
      // Refresh detail so the status banner / chip update.
      await fetchDetail();
      await fetchAuditPage();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.detail || "Не получилось восстановить"
          : "Связь пропала — попробуйте ещё раз";
      setToast(msg);
    } finally {
      setReactivating(false);
    }
  }, [master, reactivateNotify, fetchDetail, fetchAuditPage]);

  // --- derived ---
  const canEditProfile = me.is_owner || me.is_admin;
  const isPending = master ? master.invite_status === "pending" : false;
  const isInactive = master ? !master.is_active : false;
  const isEmptyProfile = useMemo(() => {
    if (!master) return false;
    return (
      master.is_active &&
      master.invite_status === "accepted" &&
      !master.bio &&
      !master.photo_url
    );
  }, [master]);

  // --- render: top-level state branches ---

  if (!masterId) {
    return (
      <div className="screen admin-flow-screen">
        <h1 className="screen__title">Мастер</h1>
        <div className="callout callout--danger" role="alert">
          Не указан мастер.
        </div>
        <button
          type="button"
          className="btn-secondary"
          style={{ marginTop: "var(--s-3)" }}
          onClick={() => navigate("/admin/team")}
        >
          Вернуться к команде
        </button>
      </div>
    );
  }

  if (loadErr) {
    return (
      <div className="screen admin-flow-screen">
        <header style={{ marginBottom: "var(--s-3)" }}>
          <button
            type="button"
            className="admin-flow-back"
            onClick={() => navigate("/admin/team")}
            aria-label="К команде"
          >
            ← Мастера
          </button>
        </header>
        <StateError err={loadErr} onRetry={() => void fetchDetail()} />
      </div>
    );
  }

  if (loading && master === null) {
    return (
      <div className="screen admin-flow-screen">
        <header style={{ marginBottom: "var(--s-3)" }}>
          <button
            type="button"
            className="admin-flow-back"
            onClick={() => navigate("/admin/team")}
            aria-label="К команде"
          >
            ← Мастера
          </button>
        </header>
        <div className="callout" role="status">
          Загружаем мастера…
        </div>
      </div>
    );
  }

  if (!master) {
    // Defensive — neither loading nor erroring but null. Treat as 404.
    return (
      <div className="screen admin-flow-screen">
        <h1 className="screen__title">Мастер не найден</h1>
        <button
          type="button"
          className="btn-secondary"
          style={{ marginTop: "var(--s-3)" }}
          onClick={() => navigate("/admin/team")}
        >
          Вернуться к команде
        </button>
      </div>
    );
  }

  const chip = statusChip(master);

  // ----- main render -----
  return (
    <div className="screen admin-flow-screen">
      {/* Header — back link + name + status chip */}
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
          onClick={() => navigate("/admin/team")}
          aria-label="Назад к команде"
          style={{
            background: "transparent",
            border: 0,
            padding: 0,
            font: "inherit",
            color: "var(--c-text-primary)",
            cursor: "pointer",
            textAlign: "start",
            flex: 1,
            minWidth: 0,
          }}
        >
          <span style={{ color: "var(--c-text-secondary)" }}>← Мастера / </span>
          <span style={{ fontWeight: 600 }}>
            {firstName(master.name)} {lastInitial(master.name)}
          </span>
        </button>
        <span className={chip.cls} aria-label={chip.label}>
          {chip.label}
        </span>
      </header>

      {/* --- banner stack (state matrix) --- */}

      {offlineBanner && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>Связь пропала. Проверьте интернет.</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => {
              setOfflineBanner(false);
              void fetchDetail();
            }}
          >
            Повторить
          </button>
        </div>
      )}

      {conflictBanner && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>
            Кто-то только что обновил эту карточку. Перезагрузить?
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => {
              setConflictBanner(false);
              setEditor(null);
              void fetchDetail();
              void fetchAuditPage();
            }}
          >
            Перезагрузить
          </button>
        </div>
      )}

      {!canEditProfile && (
        <div className="callout" role="status" style={{ marginBottom: "var(--s-3)" }}>
          🔒 Только владелец или администратор могут редактировать карточку.
        </div>
      )}

      {isInactive && (
        <div className="callout" role="status" style={{ marginBottom: "var(--s-3)" }}>
          <p style={{ margin: 0 }}>
            Этот мастер временно неактивен. Можно вернуть в любой момент.
          </p>
          {me.is_owner && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "var(--s-2)" }}
              onClick={() => setReactivateOpen(true)}
            >
              Восстановить
            </button>
          )}
        </div>
      )}

      {isPending && (
        <div className="callout" role="status" style={{ marginBottom: "var(--s-3)" }}>
          <p style={{ margin: 0, fontWeight: 600 }}>Приглашение отправлено</p>
          <p style={{ margin: "var(--s-1) 0 0" }}>
            Ждём, когда {firstName(master.name)} примет приглашение в MAX.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-2)", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Скоро"
            >
              Отправить ещё раз
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Скоро"
            >
              Отменить приглашение
            </button>
          </div>
        </div>
      )}

      {isEmptyProfile && (
        <div className="callout" role="status" style={{ marginBottom: "var(--s-3)" }}>
          <p style={{ margin: 0 }}>
            {firstName(master.name)} ещё не заполнил(а) био и фото.
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            disabled
            title="Скоро — нужен бот-DM трекинг"
          >
            Напомнить →
          </button>
        </div>
      )}

      {/* --- Profile section --- */}
      <section style={{ marginBottom: "var(--s-4)" }}>
        <h2
          style={{
            fontSize: "var(--text-h3-size, 18px)",
            margin: "0 0 var(--s-2)",
          }}
        >
          Профиль
        </h2>

        {/* Photo */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-3)",
            marginBottom: "var(--s-3)",
          }}
        >
          <span
            className="master-card__avatar"
            style={{
              width: 96,
              height: 96,
              minWidth: 96,
              minHeight: 96,
              borderRadius: "50%",
              background: "var(--c-surface-2)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              overflow: "hidden",
              fontSize: 28,
              fontWeight: 600,
            }}
          >
            {master.photo_url ? (
              <img
                src={master.photo_url}
                alt={`Фото ${master.name}`}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
            ) : (
              <span aria-hidden="true">{initials(master.name)}</span>
            )}
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handlePhotoSelect(f);
              }}
              aria-label="Загрузить фото"
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={!canEditProfile || photoUploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {photoUploading ? "Загружаем…" : "Загрузить"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Скоро"
            >
              Удалить фото
            </button>
          </div>
        </div>

        {photoErr && (
          <div
            className="callout callout--danger"
            role="alert"
            style={{ marginBottom: "var(--s-3)" }}
          >
            {photoErr}
          </div>
        )}

        {/* Name */}
        <FieldRow
          label="Имя"
          value={master.name}
          field="name"
          editor={editor}
          setEditorValue={(v) => editor && setEditor({ ...editor, value: v, err: "" })}
          maxLength={NAME_UI_MAX}
          openEditor={openEditor}
          cancelEditor={cancelEditor}
          saveEditor={saveEditor}
          canEdit={canEditProfile}
        />

        {/* Specialization */}
        <FieldRow
          label="Специализация"
          value={master.specialization}
          field="specialization"
          editor={editor}
          setEditorValue={(v) => editor && setEditor({ ...editor, value: v, err: "" })}
          maxLength={SPECIALIZATION_UI_MAX}
          openEditor={openEditor}
          cancelEditor={cancelEditor}
          saveEditor={saveEditor}
          canEdit={canEditProfile}
          placeholder="Например, маникюр, гель-лак"
        />

        {/* Bio with 280-char counter */}
        <FieldRow
          label="О себе (видят клиенты)"
          value={master.bio}
          field="bio"
          editor={editor}
          setEditorValue={(v) => editor && setEditor({ ...editor, value: v, err: "" })}
          maxLength={BIO_UI_MAX}
          openEditor={openEditor}
          cancelEditor={cancelEditor}
          saveEditor={saveEditor}
          canEdit={canEditProfile}
          multiline
          placeholder="Расскажите немного о мастере"
          counter
        />

        {/* Role — view-only, disabled tooltip */}
        <div style={{ marginBottom: "var(--s-3)" }}>
          <div style={{ marginBottom: "var(--s-1)", color: "var(--c-text-secondary)" }}>
            Роль
          </div>
          <label
            title="Только владелец, скоро"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--s-2)",
              opacity: 0.55,
            }}
          >
            <input type="radio" name="master-role" checked readOnly disabled />
            <span>Мастер</span>
          </label>
        </div>

        {/* Contact MAX — view-only with disabled change */}
        <div style={{ marginBottom: "var(--s-3)" }}>
          <div style={{ marginBottom: "var(--s-1)", color: "var(--c-text-secondary)" }}>
            Контакт MAX
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
            <span style={{ fontWeight: 500 }}>
              {master.max_handle || master.linked_bot_user?.phone_masked || "—"}
              {master.linked_bot_user && (
                <span
                  style={{ color: "var(--c-text-secondary)", marginInlineStart: "var(--s-1)" }}
                  aria-label="привязан"
                >
                  ✓
                </span>
              )}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Скоро — высокий риск"
              style={{ padding: "var(--s-1) var(--s-2)" }}
            >
              Сменить →
            </button>
          </div>
        </div>

        {/* Status row + Owner-only deactivate link */}
        <div style={{ marginBottom: "var(--s-3)" }}>
          <div style={{ marginBottom: "var(--s-1)", color: "var(--c-text-secondary)" }}>
            Статус
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
            <span className={chip.cls}>{chip.label}</span>
            {master.is_active ? (
              <button
                type="button"
                className="btn-secondary"
                disabled={!me.is_owner}
                title={me.is_owner ? undefined : "Только владелец может деактивировать"}
                onClick={() => navigate(`/admin/team/${master.id}/deactivate`)}
                style={{ padding: "var(--s-1) var(--s-2)" }}
              >
                Деактивировать →
              </button>
            ) : (
              <button
                type="button"
                className="btn-secondary"
                disabled={!me.is_owner}
                title={me.is_owner ? undefined : "Только владелец может восстановить"}
                onClick={() => setReactivateOpen(true)}
                style={{ padding: "var(--s-1) var(--s-2)" }}
              >
                Восстановить →
              </button>
            )}
          </div>
        </div>
      </section>

      {/* --- Middle section --- */}
      <section style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
          Услуги
        </h2>
        {master.services.length === 0 ? (
          <p style={{ margin: "0 0 var(--s-2)", color: "var(--c-text-secondary)" }}>
            Услуги пока не назначены.
          </p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: "0 0 var(--s-2)" }}>
            {master.services.map((s) => (
              <li key={s.id} style={{ padding: "var(--s-1) 0" }}>
                ✓ {s.name}
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={() =>
            navigate(`/admin/services?master_id=${encodeURIComponent(master.id)}`)
          }
        >
          {`Редактировать (${master.services.length}) →`}
        </button>
      </section>

      <section style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
          График
        </h2>
        <p style={{ margin: "0 0 var(--s-2)" }}>
          {master.working_hours_summary || "Расписание уточнит салон"}
        </p>
        <button
          type="button"
          className="btn-secondary"
          disabled
          title="Скоро"
        >
          Редактировать график →
        </button>
      </section>

      <section style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
          Эффективность
        </h2>
        <p
          style={{
            margin: 0,
            color: "var(--c-text-secondary)",
            fontStyle: "italic",
          }}
        >
          Скоро — нужна аналитика-секция backend
        </p>
      </section>

      <section style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
          Уведомления
        </h2>
        <p
          style={{
            margin: 0,
            color: "var(--c-text-secondary)",
            fontStyle: "italic",
          }}
        >
          Мастер настраивает сам
        </p>
      </section>

      {/* --- Audit feed --- */}
      <section style={{ marginBottom: "var(--s-5)" }}>
        <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
          Журнал изменений
        </h2>
        {auditErr && !auditLoading ? (
          <StateError
            err={auditErr}
            onRetry={() => void fetchAuditPage(undefined)}
          />
        ) : auditItems.length === 0 && !auditLoading ? (
          <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>
            Пока нет событий.
          </p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {auditItems.map((ev) => (
              <li
                key={ev.id}
                style={{
                  padding: "var(--s-2) 0",
                  borderBottom: "1px solid var(--c-divider)",
                }}
              >
                <span style={{ color: "var(--c-text-secondary)" }}>
                  {formatOccurred(ev.occurred_at)}
                </span>
                {" — "}
                <span>{actorLabel(ev)}</span>
                {" "}
                <span>{actionToHuman(ev)}</span>
              </li>
            ))}
          </ul>
        )}
        {auditLoading && (
          <p style={{ margin: "var(--s-2) 0 0", color: "var(--c-text-secondary)" }}>
            Загружаем…
          </p>
        )}
        {auditCursor && !auditLoading && (
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => void fetchAuditPage(auditCursor)}
          >
            {`Показать ещё ${AUDIT_PAGE_SIZE} →`}
          </button>
        )}
      </section>

      {/* --- Reactivation confirm dialog --- */}
      {reactivateOpen && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Восстановить ${master.name}?`}
          onClick={() => !reactivating && setReactivateOpen(false)}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              {`Восстановить ${master.name}?`}
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`${firstName(master.name)} снова сможет войти в систему и получать клиентов. Её график восстановится в том виде, в котором был до деактивации.`}
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
                {`Отправить ${firstName(master.name)} сообщение «Вы снова активны»`}
              </span>
            </label>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={reactivating}
                onClick={() => setReactivateOpen(false)}
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
        durationMs={2000}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />
    </div>
  );
}

// --- helpers -------------------------------------------------------------

/**
 * Resolve the on-disk value of the field currently being edited so we
 * can compute «dirty» without holding two refs.
 */
function editorOriginalValue(
  master: MasterDetail | null,
  editor: EditorState,
): string {
  if (!master) return editor.value;
  switch (editor.field) {
    case "name":
      return master.name;
    case "specialization":
      return master.specialization;
    case "bio":
      return master.bio;
    default:
      return editor.value;
  }
}

interface FieldRowProps {
  label: string;
  value: string;
  field: EditableField;
  editor: EditorState | null;
  setEditorValue: (v: string) => void;
  maxLength: number;
  openEditor: (field: EditableField) => void;
  cancelEditor: () => void;
  saveEditor: () => void;
  canEdit: boolean;
  multiline?: boolean;
  placeholder?: string;
  counter?: boolean;
}

function FieldRow({
  label,
  value,
  field,
  editor,
  setEditorValue,
  maxLength,
  openEditor,
  cancelEditor,
  saveEditor,
  canEdit,
  multiline,
  placeholder,
  counter,
}: FieldRowProps) {
  const isEditing = editor?.field === field;
  const inputId = `master-field-${field}`;
  const errId = `${inputId}-err`;

  return (
    <div style={{ marginBottom: "var(--s-3)" }}>
      <label
        htmlFor={inputId}
        style={{
          display: "block",
          marginBottom: "var(--s-1)",
          color: "var(--c-text-secondary)",
        }}
      >
        {label}
      </label>

      {isEditing ? (
        <>
          {multiline ? (
            <textarea
              id={inputId}
              value={editor.value}
              onChange={(e) => setEditorValue(e.target.value)}
              maxLength={maxLength}
              rows={4}
              placeholder={placeholder}
              className="admin-textarea"
              aria-invalid={Boolean(editor.err)}
              aria-describedby={editor.err ? errId : undefined}
            />
          ) : (
            <input
              id={inputId}
              type="text"
              value={editor.value}
              onChange={(e) => setEditorValue(e.target.value)}
              maxLength={maxLength}
              placeholder={placeholder}
              className="admin-search-input"
              aria-invalid={Boolean(editor.err)}
              aria-describedby={editor.err ? errId : undefined}
            />
          )}
          {counter && (
            <div
              style={{
                fontSize: "var(--text-caption-size, 12px)",
                color: "var(--c-text-secondary)",
                marginTop: "var(--s-1)",
                textAlign: "end",
              }}
            >
              {`${editor.value.length} / ${maxLength}`}
            </div>
          )}
          {editor.err && (
            <div
              id={errId}
              className="callout callout--danger"
              role="alert"
              style={{ marginTop: "var(--s-1)" }}
            >
              {editor.err}
            </div>
          )}
          <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-2)" }}>
            <button
              type="button"
              className="btn-secondary"
              disabled={editor.saving}
              onClick={cancelEditor}
              style={{ flex: 1 }}
            >
              Отмена
            </button>
            <button
              type="button"
              className="cta-bar__button"
              disabled={editor.saving}
              onClick={saveEditor}
              style={{ flex: 1 }}
            >
              {editor.saving ? "Сохраняем…" : "Сохранить"}
            </button>
          </div>
        </>
      ) : (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--s-2)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0, whiteSpace: "pre-wrap" }}>
            {value || (
              <span style={{ color: "var(--c-text-secondary)" }}>
                {placeholder || "не указано"}
              </span>
            )}
          </div>
          {canEdit && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => openEditor(field)}
              style={{ padding: "var(--s-1) var(--s-2)" }}
              aria-label={`Редактировать ${label.toLowerCase()}`}
            >
              Редактировать →
            </button>
          )}
        </div>
      )}
    </div>
  );
}
