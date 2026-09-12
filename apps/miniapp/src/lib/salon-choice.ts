/**
 * Выбор салона при нескольких ролях у одной личности (DRF-1766, срез 5 DRF-1705).
 *
 * Сервер, встретив личность с рабочей ролью в двух и более салонах, отвечает
 * `409 salon_choice_required` со списком `details.tenants` (slug, name) —
 * решение владельца: «выбор салона, не страж». Человек выбирает на экране,
 * выбор живёт в `sessionStorage` (на сессию Mini App, не дольше) и уходит
 * заголовком `X-Salon-Choice` на КАЖДЫЙ запрос — `/api/v1/me`, master_api,
 * admin_api читают его одним местом на сервере (`bot_user_resolver`).
 *
 * Заголовок ничего не даёт сам по себе: сервер принимает его только среди
 * тенантов, где у личности есть роль. Чужой слаг → снова 409, снова экран.
 */

const STORAGE_KEY = "ayla:salon-choice";
export const SALON_CHOICE_HEADER = "X-Salon-Choice";
export const SALON_CHOICE_REQUIRED = "salon_choice_required";

let inMemory: string | null = null;

export interface SalonChoiceTenant {
  slug: string;
  name: string;
}

export function getSalonChoice(): string | null {
  if (inMemory) return inMemory;
  try {
    inMemory = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    inMemory = null;
  }
  return inMemory;
}

export function setSalonChoice(slug: string | null): void {
  inMemory = slug;
  try {
    if (slug) sessionStorage.setItem(STORAGE_KEY, slug);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* private mode / blocked storage — the in-memory value still serves this session */
  }
}

/** Mutates `headers` in place: adds `X-Salon-Choice` when a choice is stored. */
export function applySalonChoiceHeader(headers: Headers): void {
  const slug = getSalonChoice();
  if (slug) headers.set(SALON_CHOICE_HEADER, slug);
}

/**
 * The tenants a `409 salon_choice_required` names, or `null` for any other
 * error. Reads the body the server sent — never invents a list.
 */
export function salonChoiceTenantsFrom(err: unknown): SalonChoiceTenant[] | null {
  const e = err as { status?: number; slug?: string; details?: Record<string, unknown> } | null;
  if (!e || e.status !== 409 || e.slug !== SALON_CHOICE_REQUIRED) return null;
  const raw = e.details?.tenants;
  if (!Array.isArray(raw)) return null;
  const tenants = raw
    .map((t) => (t && typeof t === "object" ? (t as Record<string, unknown>) : null))
    .filter((t): t is Record<string, unknown> => t !== null)
    .map((t) => ({ slug: String(t.slug ?? ""), name: String(t.name ?? t.slug ?? "") }))
    .filter((t) => t.slug.length > 0);
  return tenants.length > 0 ? tenants : null;
}
