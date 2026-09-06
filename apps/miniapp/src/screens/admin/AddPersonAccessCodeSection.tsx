/**
 * «Добавить человека» — ветка «человек уже работает в салоне».
 *
 * Половина одного экрана (`AdminAddPersonScreen`), а не самостоятельный
 * экран. Прежде это был `AdminStaffAccessScreen` по адресу
 * `/admin/team/access`, и его собственный заголовок объяснял, почему он
 * отдельный: одна половина СОЗДАЁТ карточку мастера, вторая ДАЁТ ДОСТУП
 * тому, кто уже есть. Для бэкенда это по-прежнему два эндпоинта и два
 * разных жизненных цикла — там разделение остаётся.
 *
 * Владелец салона решает не это. Он решает одну задачу — подключить
 * человека — и до этой правки должен был знать заранее, заведён тот в
 * каталоге или нет, чтобы выбрать правильную из двух кнопок на экране
 * команды. Решение владельца §25 п.4 от 05.09.2026: экран один, вопрос
 * задаётся на нём.
 *
 * ### Код показывается один раз, и это форма экрана
 *
 * Хранится только отпечаток. Поэтому предупреждение стоит НАД кодом,
 * подтверждение выхода MAX включено, пока код на экране, и возврата из
 * этого состояния нет — выход только «Готово».
 *
 * ### Ссылка (DRF-1505)
 *
 * `?start=inv_<код>` — тот же код в виде, который можно вставить в
 * переписку. Механика погашения не меняется: бот читает эту форму с
 * DRF-1061. Меняется то, что четыре символа больше не нужно диктовать
 * голосом. **Ссылка и есть код**: кто её откроет, тот его и потратит,
 * поэтому она живёт под тем же предупреждением и показывается ровно
 * один раз.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ScreenLayout } from "../../components/ScreenLayout";
import { ShareableLink } from "../../components/ShareableLink";
import { StickyCta } from "../../components/StickyCta";
import { ApiError } from "../../lib/api";
import {
  issueStaffInvite,
  listMasters,
  type MasterListItem,
  type MeResponse,
  type StaffInviteResponse,
  type StaffInviteRole,
} from "../../lib/admin-api";
import { formatDateLong } from "../../lib/masterDateFormat";
import { hapticNotify, hapticSelection } from "../../lib/max-sdk";
import { backTo, screenRoot } from "../../lib/screen-back";
import { useClosingConfirmation } from "../../hooks/useClosingConfirmation";
import type { ReactNode } from "react";

/**
 * Ready-to-forward invitation text — NOT DECIDED.
 *
 * The owner has not ruled on whether the screen hands over a bare code /
 * link or a message the issuer can paste into a messenger, nor on how
 * such a message would be worded. Product voice is not a thing to guess
 * at, so the seam is left open and empty: when a decision lands, this
 * becomes a function of `{code, role, expiresAt, invite_link}` and the
 * block below starts rendering.
 *
 * A draft was written for DRF-1505 and sent to the owner **in the report,
 * not into this constant.** Until it comes back approved, this stays
 * `null` — a placeholder would ship, and shipped copy is approved copy
 * whether or not anybody approved it.
 *
 * Do not fill this with invented copy. `AdminAddPersonScreen.test.tsx`
 * pins that the block is absent while this is `null`.
 */
export const INVITE_MESSAGE_TEMPLATE: ((r: StaffInviteResponse) => string) | null =
  null;

interface RoleOption {
  readonly value: StaffInviteRole;
  readonly label: string;
  /** What the code actually does, in the reader's terms. */
  readonly effect: string;
  /** Owner-only per `views_staff_invite.py` — see `visibleRoles`. */
  readonly ownerOnly?: boolean;
}

/**
 * Order is increasing privilege, matching `TenantStaff.Role`'s own
 * docstring, so the most powerful thing is never the first thing a tired
 * person taps.
 */
export const ROLE_OPTIONS: readonly RoleOption[] = [
  {
    value: "master",
    label: "Мастер",
    effect: "Свяжет уже заведённого мастера с его аккаунтом MAX.",
  },
  {
    value: "receptionist",
    label: "Ресепшен",
    effect: "Даст доступ к дню салона и записям.",
  },
  {
    value: "admin",
    label: "Администратор",
    effect: "Даст доступ к дню салона, записям и команде.",
  },
  {
    value: "owner",
    label: "Владелец",
    effect: "Полный доступ. Выдать может только владелец.",
    ownerOnly: true,
  },
];

const MAX_NOTE_LEN = 200;

type Stage =
  | { kind: "form" }
  | { kind: "issued"; issued: StaffInviteResponse; masterName: string };

interface Props {
  readonly me: MeResponse;
  /** Переключатель веток — рисуется первым в теле, до формы. */
  readonly switcher: ReactNode;
}

export function AddPersonAccessCodeSection({ me, switcher }: Props) {
  const navigate = useNavigate();
  const [stage, setStage] = useState<Stage>({ kind: "form" });
  const [role, setRole] = useState<StaffInviteRole>("master");
  const [masterId, setMasterId] = useState("");
  const [note, setNote] = useState("");
  const [masters, setMasters] = useState<MasterListItem[] | null>(null);
  const [mastersFailed, setMastersFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const visibleRoles = useMemo(
    () => ROLE_OPTIONS.filter((o) => !o.ownerOnly || me.is_owner),
    [me.is_owner],
  );

  const goBackToTeam = useCallback(() => navigate("/admin/team"), [navigate]);

  // On the form the back goes to the roster. On the issued screen it is
  // deliberately absent: the only way out is «Готово», because leaving by
  // a back-swipe here destroys a credential that is shown once.
  //
  // DRF-1493 — объявлено видом экрана, а не пустым объектом: «здесь
  // возврата нет» говорится вслух и с причиной.
  const back =
    stage.kind === "form"
      ? backTo("/admin/team")
      : screenRoot(
          "Код и ссылка показываются один раз: уход назад со свежевыданным " +
            "кодом уничтожает его. Единственный выход — «Готово».",
        );

  const dirty = stage.kind === "form" && (masterId !== "" || note !== "");
  // While the code is on screen, closing the Mini App loses it for good —
  // that is a stronger reason for the confirmation than an unsaved form.
  useClosingConfirmation(dirty || stage.kind === "issued");

  // The master list is only needed for `role=master`, but it is fetched
  // once for the screen rather than on every toggle.
  const wantsMasters = role === "master";
  const fetchedRef = useRef(false);
  useEffect(() => {
    if (!wantsMasters || fetchedRef.current) return;
    fetchedRef.current = true;
    const ctrl = new AbortController();
    listMasters({ is_active: true, limit: 100 }, { signal: ctrl.signal })
      .then((res) => setMasters(res.items))
      .catch(() => setMastersFailed(true));
    return () => ctrl.abort();
  }, [wantsMasters]);

  async function onIssue() {
    if (submitting) return;
    if (role === "master" && !masterId) {
      setErr("Выберите мастера, которого нужно связать.");
      hapticNotify("error");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      const issued = await issueStaffInvite({
        role,
        ...(role === "master" ? { master_id: masterId } : {}),
        ...(note.trim() ? { note: note.trim().slice(0, MAX_NOTE_LEN) } : {}),
      });
      hapticNotify("success");
      const masterName = masters?.find((m) => m.id === masterId)?.name ?? "";
      setStage({ kind: "issued", issued, masterName });
    } catch (e: unknown) {
      hapticNotify("error");
      setErr(errorText(e));
    } finally {
      setSubmitting(false);
    }
  }

  function onIssueAnother() {
    hapticSelection();
    setCopied(false);
    setErr(null);
    setNote("");
    setStage({ kind: "form" });
  }

  async function onCopy(code: string) {
    // Clipboard is a convenience, never the only way out: the code stays
    // selectable on screen, so a refusal (older webview, denied
    // permission) costs nothing but the button's feedback.
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      hapticSelection();
    } catch {
      setCopied(false);
    }
  }

  if (stage.kind === "issued") {
    const { issued, masterName } = stage;
    const roleLabel =
      ROLE_OPTIONS.find((o) => o.value === issued.role)?.label ?? issued.role;
    return (
      <ScreenLayout
        back={back}
        title="Код доступа"
        cta={<StickyCta onClick={goBackToTeam}>Готово</StickyCta>}
      >
        {/* Above the code on purpose: a warning under it is read after the
            reader has already decided what to do. */}
        <div className="callout callout--danger" role="alert">
          <p className="staff-access__once">
            Код и ссылка показываются один раз.
          </p>
          <p className="staff-access__once-detail">
            Мы храним только отпечаток кода — восстановить его нельзя ни здесь,
            ни в поддержке. Передайте до того, как закроете экран.
          </p>
        </div>

        <p className="staff-access__code" aria-label={`Код доступа ${issued.code}`}>
          {issued.code}
        </p>

        <button
          type="button"
          className="btn-secondary"
          onClick={() => void onCopy(issued.code)}
        >
          {copied ? "Скопировано" : "Скопировать код"}
        </button>
        {/* Слышимый итог: смена подписи кнопки скринридеру не событие. */}
        {copied && (
          <p className="shareable__copied" role="status">
            Код скопирован.
          </p>
        )}

        {issued.invite_link ? (
          <ShareableLink
            url={issued.invite_link}
            label="Ссылка вместо кода"
            hint={
              "Открывшему её доступ откроется сразу — код вводить не нужно. " +
              "Это тот же самый код: кто перейдёт по ссылке, тот его и " +
              "потратит, поэтому отправляйте её только тому человеку."
            }
          />
        ) : (
          <p className="admin-hint">
            Ссылки нет: в этом контуре не настроен салонный бот. Код по-прежнему
            работает, если ввести его в диалоге с ботом.
          </p>
        )}

        <dl className="staff-access__meta">
          <dt>Роль</dt>
          <dd>{roleLabel}</dd>
          {masterName && (
            <>
              <dt>Мастер</dt>
              <dd>{masterName}</dd>
            </>
          )}
          <dt>Действует до</dt>
          <dd>{formatDateLong(issued.expires_at)}</dd>
        </dl>

        <p className="staff-access__how">
          Человек отправляет этот код салонному боту в диалоге — доступ
          откроется сразу.
        </p>

        {/* The seam described at the top of this file. Absent until the
            owner rules on the wording; never a placeholder. */}
        {INVITE_MESSAGE_TEMPLATE && (
          <p className="staff-access__message">{INVITE_MESSAGE_TEMPLATE(issued)}</p>
        )}

        <button type="button" className="admin-flow-back" onClick={onIssueAnother}>
          Выдать ещё один код
        </button>
      </ScreenLayout>
    );
  }

  return (
    <ScreenLayout
      back={back}
      title="Добавить человека"
      cta={
        <StickyCta onClick={() => void onIssue()} disabled={submitting}>
          {submitting ? "Выдаём…" : "Выдать код"}
        </StickyCta>
      }
    >
      {switcher}

      <p className="staff-access__lead">
        Код открывает доступ человеку, который уже есть в салоне. Чтобы завести
        нового мастера в каталог, переключитесь наверху.
      </p>

      {err && (
        <div className="callout callout--danger" role="alert">
          {err}
        </div>
      )}

      <fieldset className="staff-access__roles">
        <legend className="staff-access__legend">Что даёт код</legend>
        {visibleRoles.map((opt) => (
          <label key={opt.value} className="staff-access__role">
            {/*
              `aria-label` + `aria-describedby` rather than letting the
              <label> name the control: without them the accessible name
              becomes «Ресепшен Даст доступ к дню салона и записям.» —
              the role and its consequence read as one run-on phrase, and
              a screen-reader user hears the explanation before they can
              tell the options apart.
            */}
            <input
              type="radio"
              name="staff-access-role"
              aria-label={opt.label}
              aria-describedby={`staff-access-effect-${opt.value}`}
              checked={role === opt.value}
              onChange={() => {
                hapticSelection();
                setErr(null);
                setRole(opt.value);
              }}
            />
            <span className="staff-access__role-text">
              <span className="staff-access__role-label">{opt.label}</span>
              <span
                className="staff-access__role-effect"
                id={`staff-access-effect-${opt.value}`}
              >
                {opt.effect}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      {role === "master" && (
        <label className="staff-access__field">
          <span className="staff-access__field-title">Кого связать</span>
          {mastersFailed ? (
            <span className="staff-access__field-note">
              Не получилось загрузить список мастеров. Обновите экран.
            </span>
          ) : (
            <select
              className="admin-select"
              value={masterId}
              onChange={(e) => {
                setErr(null);
                setMasterId(e.target.value);
              }}
              disabled={masters === null}
            >
              <option value="">
                {masters === null ? "Загружаем…" : "Выберите мастера"}
              </option>
              {(masters ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          )}
          <span className="staff-access__field-note">
            Новая карточка не создаётся — код связывает уже заведённого мастера
            с его аккаунтом MAX.
          </span>
        </label>
      )}

      <label className="staff-access__field">
        <span className="staff-access__field-title">Заметка для себя</span>
        <textarea
          className="admin-textarea"
          value={note}
          maxLength={MAX_NOTE_LEN}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Например: Аня, ресепшен, вышла 25.08"
        />
        <span className="staff-access__field-note">
          Видите только вы. Человеку не показывается.
        </span>
      </label>
    </ScreenLayout>
  );
}

/**
 * Backend slug → what the person can do about it.
 *
 * The server's `detail` strings are English and written for a developer
 * («only the salon owner can issue an owner invite»), so they are mapped
 * rather than shown. An unmapped failure falls through to a neutral line —
 * never to the raw slug, which reads as breakage.
 */
function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) {
    return "Не получилось выдать код. Проверьте связь и попробуйте ещё раз.";
  }
  switch (e.slug) {
    case "forbidden":
      return "Код владельца может выдать только владелец салона.";
    case "not_found":
      return "Такого мастера нет среди активных. Обновите экран и выберите заново.";
    case "bad_request":
      return "Не хватает данных для кода. Проверьте роль и выбранного мастера.";
    default:
      return "Не получилось выдать код. Попробуйте ещё раз.";
  }
}
