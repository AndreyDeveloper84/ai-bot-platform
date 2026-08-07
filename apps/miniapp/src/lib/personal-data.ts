/**
 * C5 personal-data client (152-ФЗ, pilot 2026-08-15).
 *
 * Frozen contract: `PILOT_CONTRACTS_2026-08-15` §6 — endpoints owned by
 * W3 (`apps/miniapp_api`), aggregation/cascade by W2/W3 services. This
 * module is the ONLY frontend caller; screens must not hand-roll fetches.
 *
 *   GET    /api/v1/customer/me/personal-data/export/
 *     → 200 JSON attachment (Ayla export + bot memory + consents, one
 *       file). 502 `upstream_unavailable` when the Ayla leg fails — the
 *       backend refuses to ship a silently-incomplete export; the sheet
 *       surfaces this as a retryable error.
 *   DELETE /api/v1/customer/me/personal-data/
 *     → 200 `{"status": "deleted"}`. Idempotent per contract: repeats
 *       return the same 200, so the UI never special-cases "already
 *       deleted". 502 `{"status": "partial", "failed_steps": [...]}` when
 *       a cascade step failed — surfaced as
 *       {@link PersonalDataPartialDeleteError}; retry is safe (finished
 *       steps no-op server-side).
 *
 * Pilot scope note (contract §6): the cascade covers personal context +
 * memory + consents. Transactional records (bookings, payments) are
 * retained per legal retention and anonymised post-pilot — the delete
 * sheet copy says exactly that, nothing more.
 *
 * Auth mirrors `api.ts` (`MaxInitData` header + dev bypass) — duplicated
 * here rather than exported from `api.ts` because the export endpoint
 * answers a Blob, not JSON, and `api.ts::request` is JSON-typed.
 */

import { ApiError } from "./api";
import { applyDevBypassHeaders } from "./dev-bypass";
import { getInitData } from "./max-sdk";

const API_BASE = "/api/v1/customer";
const EXPORT_PATH = "/me/personal-data/export/";
const DELETE_PATH = "/me/personal-data/";

/** Contract filename — mirrors the backend Content-Disposition value. */
const EXPORT_FILENAME_FALLBACK = "personal-data-export.json";

export interface PersonalDataExportFile {
  blob: Blob;
  filename: string;
}

/**
 * Thrown on the honest-partial 502: some cascade steps finished, the
 * listed ones did not. `failedSteps` are backend slugs
 * ("ayla_delete" | "memory_delete" | "consent_withdraw") — screens map
 * them to human copy, never render raw.
 */
export class PersonalDataPartialDeleteError extends Error {
  constructor(readonly failedSteps: string[]) {
    super("personal-data delete finished partially");
    this.name = "PersonalDataPartialDeleteError";
  }
}

interface ErrorBody {
  error: string;
  detail: string;
}

function buildAuthHeaders(): Headers {
  const headers = new Headers();
  const initData = getInitData();
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  return headers;
}

async function throwApiError(res: Response): Promise<never> {
  let body: ErrorBody = { error: "http_error", detail: res.statusText };
  try {
    body = (await res.json()) as ErrorBody;
  } catch {
    /* non-JSON 5xx */
  }
  throw new ApiError(res.status, body.error, body.detail);
}

/** C5.1 — fetch the aggregated personal-data export as a Blob. */
export async function exportPersonalData(): Promise<PersonalDataExportFile> {
  const res = await fetch(`${API_BASE}${EXPORT_PATH}`, {
    headers: buildAuthHeaders(),
  });
  if (!res.ok) await throwApiError(res);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob, filename: match?.[1] ?? EXPORT_FILENAME_FALLBACK };
}

/**
 * The exact string the customer must type to confirm erasure. Mirrors
 * `DELETE_CONFIRMATION_TOKEN` in `apps/identity/services/profile.py` — the
 * server rejects anything else with 400 `confirmation_mismatch`.
 */
export const DELETE_CONFIRMATION_TOKEN = "УДАЛИТЬ";

/**
 * C5.2 — run the delete cascade. Resolves only on full success; a
 * partial cascade raises {@link PersonalDataPartialDeleteError} so the
 * sheet can offer an honest retry.
 *
 * `confirmation` must equal {@link DELETE_CONFIRMATION_TOKEN}; it is
 * verified server-side (DRF-956 / T-05), so this is not a formality the
 * client can skip.
 */
export async function deletePersonalData(
  confirmation: string,
): Promise<{ status: "deleted" }> {
  const headers = buildAuthHeaders();
  headers.set("Content-Type", "application/json");
  const res = await fetch(`${API_BASE}${DELETE_PATH}`, {
    method: "DELETE",
    headers,
    body: JSON.stringify({ confirmation }),
  });
  if (res.ok) {
    return (await res.json()) as { status: "deleted" };
  }
  if (res.status === 502) {
    try {
      const body = (await res.json()) as {
        status?: string;
        failed_steps?: string[];
      };
      if (body.status === "partial" && Array.isArray(body.failed_steps)) {
        throw new PersonalDataPartialDeleteError(body.failed_steps);
      }
    } catch (err) {
      if (err instanceof PersonalDataPartialDeleteError) throw err;
      /* unparseable 502 — fall through to generic ApiError */
    }
  }
  return throwApiError(res);
}

/**
 * Best-effort Blob download in the MAX webview (Chromium): temporary
 * anchor with a `download` attribute. The object URL is revoked on the
 * next tick so the click handler has time to start the download.
 */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
