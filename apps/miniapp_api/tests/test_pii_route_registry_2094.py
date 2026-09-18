"""Personal-data route registry for the customer Mini App API (DRF-2094).

See :mod:`tests.support.pii_route_registry` for the record kinds and the
checks. This module holds the registry itself: one entry per named route
in :mod:`apps.miniapp_api.urls`, each citing the function that builds
the response body (``via``), so the classification is a reading of that
function and can be re-read next to it.

The subject on this surface is the calling customer (``request.bot_user``
under ``require_init_data``). ``own`` therefore means the customer's own
data; ``third_party`` is almost always the master — a natural person whose
public professional card the salon publishes.

Error bodies (``_error`` → ``{"error", "detail"}``) are not classified per
route: they carry a slug and a sentence, never a record.
"""

from __future__ import annotations

from apps.miniapp_api import urls as customer_urls
from tests.support.pii_route_registry import (
    Entry,
    check_citations_resolve,
    check_every_route_is_classified,
    check_fields_named_where_data_flows,
    check_no_stale_entries,
    check_no_unnamed_routes_and_floor,
    check_none_is_not_mixed,
    check_planted_route_goes_red,
    check_reasons_are_written_not_templated,
    check_third_party_names_whose_and_why,
    none,
    own,
    third_party,
)

V = "apps.miniapp_api.views:"

#: Named routes in ``apps/miniapp_api/urls.py`` on dev 94125e1f (18.09.2026).
#: Lower the floor deliberately when a route is removed.
ROUTE_FLOOR = 44

_BOOKING_FIELDS = (
    "id",
    "status",
    "service_name",
    "visit_at",
    "duration_min",
    "cancel_requested_at",
    "rating",
    "can_rate",
    "address",
)

_MASTER_ON_BOOKING = third_party(
    "master_name",
    via=V + "_booking_to_dict",
    whose="the master assigned to the customer's booking",
    why=(
        "the customer needs to know who receives them; the name is the one the salon "
        "publishes on the master card, and the proxy shape resolves it through the "
        "catalog mirror (_proxy_booking_to_dict) so both paths show the same name"
    ),
)

_OWN_BOOKING = own(
    *_BOOKING_FIELDS,
    via=V + "_booking_to_dict",
    note=(
        "the customer's own booking row; _get_booking_owned scopes the lookup to "
        "request.bot_user so no other customer's booking can be serialised here"
    ),
)

_CONSENTS_DOCUMENT = own(
    "consents.*.granted",
    "consents.*.granted_at",
    "consents.*.document_version",
    "proactive_hints.enabled",
    "data_storage.revocation",
    via="apps.consent.customer:read_consents",
    note=(
        "the customer's own consent registry re-read from the database after the write; "
        "facts of consent and their dates only — no medical content and no phone (DRF-1039)"
    ),
)

CUSTOMER_ROUTES: dict[str, Entry] = {
    # --- identity ---------------------------------------------------------
    "auth_verify": own(
        "user.id",
        "user.channel_user_id",
        "user.display_name",
        "user.client_name",
        "tenant.slug",
        "tenant.name",
        "identity.ayla_user_id",
        via=V + "auth_verify",
        note=(
            "the caller's own identity as resolved from the verified init data; channel_user_id "
            "is the messenger id of the very account that presented the signature"
        ),
    ),
    "me": (
        own(
            "bot_user_id",
            "display_name",
            "client_name",
            "phone_masked",
            "timezone",
            "joined_at",
            "preferences",
            "food_diary_processing (consent date)",
            "favorites.service_name",
            via=V + "_profile_to_dict",
            note=(
                "the customer's own profile snapshot; the phone is masked by the profile service "
                "and the consent key carries a date, not a document"
            ),
        ),
        third_party(
            "favorites.master_name",
            via=V + "_profile_to_dict",
            whose="the master the customer books most often",
            why=(
                "the profile shows the customer their own favourite; the value is the published "
                "master-card name derived from the customer's own booking history"
            ),
        ),
    ),
    "delete_me": none(
        "acknowledgement only — {'deleted': true} after soft_delete_user; the confirmation "
        "token from the body is not echoed",
        via=V + "delete_me",
    ),
    # --- privacy ----------------------------------------------------------
    "personal_data_export": own(
        "profile",
        "ayla section (personal context, display_name_preferred, language_preferred, summary)",
        "memory entries (kind, source, content, timestamps, status)",
        via="apps.identity.services.privacy:export_personal_data",
        note=(
            "the 152-FZ subject-access export of the caller's own data across both backends; "
            "served as an attachment and refused (502) rather than served half when the Ayla leg fails"
        ),
    ),
    "personal_data_delete": none(
        "status word only — 'deleted' / 'deletion_started' / an error slug; the erasure runs "
        "server-side and nothing about the person is echoed",
        via=V + "personal_data_delete",
    ),
    "deletion_request": own(
        "request.request_id",
        "request.status",
        "request.requested_at",
        "request.deadline_at",
        "request.completed_at",
        "request.is_open",
        via="apps.identity.services.deletion_request:DeletionRequestView.as_dict",
        note=(
            "the caller's own erasure request as Ayla records it; identifiers and dates of the "
            "request itself, no fields of the person"
        ),
    ),
    # --- consents ---------------------------------------------------------
    "health_consent": own(
        "granted",
        "granted_at",
        "document_version",
        via=V + "_health_consent_payload",
        note=(
            "the fact and date of the caller's own special-category consent (152-FZ art. 10); "
            "the health data itself never travels here"
        ),
    ),
    "food_scanner_consent": own(
        "granted",
        "granted_at",
        "document_version",
        via=V + "_food_scanner_consent_payload",
        note=(
            "the fact and date of the caller's own food-diary consent read from the registry "
            "under FOOD_DIARY_CONSENT_DOCUMENT_VERSION; diary content is not in this body"
        ),
    ),
    "customer_consents": _CONSENTS_DOCUMENT,
    "customer_proactive_hints": _CONSENTS_DOCUMENT,
    "customer_marketing_consent": _CONSENTS_DOCUMENT,
    "customer_data_storage_consent": own(
        "consents.*",
        "proactive_hints.enabled",
        "revocation.status",
        "revocation.failed_steps",
        via=V + "customer_data_storage_consent",
        note=(
            "the same consent document plus the outcome of the caller's own storage-consent "
            "revocation (DRF-1950 three outcomes); step names, not data, in failed_steps"
        ),
    ),
    # --- catalog (no person) -----------------------------------------------
    "slots": none(
        "bookable start times for one master/service/day window; rows are timestamps and "
        "durations from _slots_from_ayla or the local grid, no person field on either path",
        via=V + "slots",
    ),
    "services_list": none(
        "the tenant's service catalogue: id, slug, name, descriptions, price, duration, "
        "is_bookable — properties of a service, not of a person",
        via=V + "_service_to_dict",
    ),
    "service_detail": none(
        "one service card in the same _service_to_dict shape as the list; the route "
        "resolves by service id and reads nothing about the caller",
        via=V + "_service_to_dict",
    ),
    "booking_quote": none(
        "price, duration and their source for a service/master pair; a quote about an "
        "offer, computed before any booking row exists",
        via=V + "booking_quote",
    ),
    # --- masters (third party) ---------------------------------------------
    "masters_list": third_party(
        "name",
        "specialization",
        "bio",
        "experience",
        "rating",
        "photo_url",
        "review_count",
        via=V + "_master_to_dict",
        whose="the salon's masters (bookable natural persons)",
        why=(
            "the customer chooses whom to book; the card is the professional profile the salon "
            "publishes for that purpose (is_active + bookable rows only), not the master's account"
        ),
    ),
    "master_detail": third_party(
        "name",
        "specialization",
        "bio",
        "experience",
        "rating",
        "photo_url",
        "review_count",
        via=V + "_master_to_dict",
        whose="one of the salon's masters",
        why=(
            "the same published card as the list, opened by id; the customer reads it to decide "
            "on a booking and nothing beyond the published profile is added on detail"
        ),
    ),
    # --- bookings (own + master name) --------------------------------------
    "create_booking": (
        own(
            "booking.id",
            "booking.service_name",
            "booking.visit_at",
            "booking.duration_min",
            "booking.status",
            "booking.address",
            via=V + "create_booking",
            note=(
                "the booking the caller just made, echoed back once with the salon address "
                "(DRF-1952); the customer's own record on both the local and the Ayla path"
            ),
        ),
        third_party(
            "booking.master_name",
            via=V + "create_booking",
            whose="the master the caller booked",
            why=(
                "confirmation must name who will receive the customer; the value is the "
                "published master-card name copied onto the booking at creation"
            ),
        ),
    ),
    "bookings_list": (
        own(
            *_BOOKING_FIELDS,
            via=V + "_booking_to_dict",
            note=(
                "the caller's own bookings (local rows filtered by bot_user, or the Ayla proxy "
                "for this subject via _proxy_booking_to_dict); never another customer's rows"
            ),
        ),
        _MASTER_ON_BOOKING,
    ),
    "booking_detail": (_OWN_BOOKING, _MASTER_ON_BOOKING),
    "booking_cancel_request": (_OWN_BOOKING, _MASTER_ON_BOOKING),
    "booking_cancel_confirm": (_OWN_BOOKING, _MASTER_ON_BOOKING),
    "booking_cancel_undo": (_OWN_BOOKING, _MASTER_ON_BOOKING),
    "booking_reschedule_request": (_OWN_BOOKING, _MASTER_ON_BOOKING),
    "booking_reschedule_confirm": (
        own(
            "old_booking.*",
            "new_booking.*",
            via=V + "booking_reschedule_confirm",
            note=(
                "two rows of the caller's own booking — before and after the move — both in "
                "the _booking_to_dict shape and both owned by request.bot_user"
            ),
        ),
        _MASTER_ON_BOOKING,
    ),
    "submit_feedback": own(
        "booking_id",
        "rating",
        "comment",
        "feedback_at",
        "handoff_created",
        "task_id",
        via=V + "submit_feedback",
        note=(
            "the rating and free-text comment the caller just left on their own visit, echoed "
            "back with the moment it was recorded; the master is not named in this body"
        ),
    ),
    "customer_recent_activity": (
        own(
            "this_week_booking_count",
            "next_booking.date_human",
            "next_booking.service_name",
            "next_booking.duration_min",
            "next_booking.booking_id",
            "next_booking.address",
            via=V + "customer_recent_activity",
            note=(
                "the caller's own next visit and this week's count, from the mirror or local "
                "rows scoped to bot_user; the salon address travels verbatim (DRF-1652)"
            ),
        ),
        third_party(
            "next_booking.master_name",
            via=V + "customer_recent_activity",
            whose="the master of the caller's next visit",
            why=(
                "the home card says whom the customer is going to; same published name as on "
                "the booking row it was read from"
            ),
        ),
    ),
    # --- payments (Ayla passthrough, own) ----------------------------------
    "create_payment": own(
        "payment (Ayla create_payment response passed through: id, confirmation url, status)",
        via=V + "create_payment",
        note=(
            "the payment the caller opened for their own appointment; ownership is checked by "
            "_customer_owns_appointment before the upstream call and a miss answers 404"
        ),
    ),
    "cards_setup": own(
        "card setup (Ayla cards_setup response passed through: setup id, confirmation url)",
        via=V + "cards_setup",
        note=(
            "the caller's own card-binding session under their consent_version; the body is "
            "Ayla's document for this ayla_user_id and nothing else is merged into it"
        ),
    ),
    "cards_list": own(
        "cards[] (Ayla list_cards rows passed through: id, masked number, brand, expiry)",
        via=V + "cards_list",
        note=(
            "the caller's own saved cards as Ayla masks them; the list is fetched for the "
            "resolved binding of request.bot_user only"
        ),
    ),
    "card_delete": none(
        "204 with no body; the card is deleted upstream for the caller's own binding and "
        "an already-gone card is the same success",
        via=V + "card_delete",
    ),
    # --- goals / recommendations --------------------------------------------
    "customer_recommendations": (
        own(
            "ordered shelf keyed by the caller's goal (decision document from the resolver)",
            via=V + "customer_recommendations",
            note=(
                "a personalised decision for the caller's external_user_id; refused with 423 "
                "when the person has an open deletion request, so no shelf is built for them"
            ),
        ),
        third_party(
            "candidate master and service names after translate_provider_keys",
            via=V + "customer_recommendations",
            whose="the masters proposed as candidates",
            why=(
                "recommendations are of masters to book; their names are the published "
                "master-card names resolved through the catalog mirror"
            ),
        ),
    ),
    "customer_decision_context": own(
        "data.known",
        "data.missing",
        "data.suggestions",
        "data.intents",
        via=V + "customer_decision_context",
        note=(
            "Ayla's decision context for the caller's own external_user_id, passed through "
            "verbatim in the {data} envelope — the Mini App is a dumb renderer of it"
        ),
    ),
    "customer_goal_select": own(
        "data (the caller's goal document as Ayla returns it after the select)",
        via=V + "customer_goal_select",
        note=(
            "the goal the caller just chose, in the same {data} envelope as the read; a 400 "
            "echoes Ayla's error body, which is about the request, not the person"
        ),
    ),
    # --- wellness (own) ---------------------------------------------------
    "customer_wellness_today": own(
        "display_name",
        "calories_eaten",
        "calories_target",
        "pfc",
        "entries",
        "water_glasses_eaten",
        "water_glasses_target",
        "active_goals",
        "coach_observation",
        via=V + "customer_wellness_today",
        note=(
            "the caller's own diary day; without PERSONAL_DATA consent the body shrinks to "
            "display_name + consent_required (+ goals) and no diary key is present at all"
        ),
    ),
    "customer_wellness_water": own(
        "entry_id",
        "ml",
        "water_ml",
        "today_total_ml",
        "water_glasses_eaten",
        "today_norm_ml",
        "water_glasses_target",
        via=V + "customer_wellness_water",
        note=(
            "the water entry the caller just logged and their running total; the norm key is "
            "omitted when no target exists rather than sent as null"
        ),
    ),
    "customer_wellness_water_undo": none(
        "204 with no body when the caller's own water entry is undone; refusals carry an "
        "error slug and a sentence, not the entry",
        via=V + "customer_wellness_water_undo",
    ),
    "customer_food_estimate": none(
        "nutrition of a dish looked up for the text the caller typed (matched_dish, portion, "
        "kcal, macros); nothing is read from or written to the caller's diary",
        via=V + "customer_food_estimate",
    ),
    "customer_food_log": own(
        "log_id",
        "dish_name",
        "calories",
        "entry_origin",
        via=V + "customer_food_log",
        note=(
            "the diary entry the caller just wrote from text, behind _food_text_gate (diary "
            "consent required); the origin says whether the estimate was confirmed or corrected"
        ),
    ),
    "customer_wellness_food_entry": own(
        "entry_id",
        "restore_window_expires_at",
        "id / dish_name / calories / meal_type (PATCH, via _food_log_payload)",
        via=V + "customer_wellness_food_entry",
        note=(
            "DELETE answers with the caller's own deleted entry id and its restore window; "
            "PATCH answers with the edited entry in the _food_log_payload shape"
        ),
    ),
    "customer_wellness_food_entry_restore": own(
        "id",
        "dish_name",
        "calories",
        "meal_type",
        via=V + "_food_log_payload",
        note=(
            "the caller's own diary entry brought back from the restore window, in the same "
            "four-field shape the diary list uses"
        ),
    ),
    "customer_saved_meals": own(
        "id",
        "dish_name",
        "portion_g",
        "calories",
        "protein_g",
        "fat_g",
        "carbs_g",
        "source_food_log_id",
        "created_at",
        via="apps.miniapp_api.views_saved_meals:_row_payload",
        note=(
            "the caller's own favourite dishes proxied from the catalog under their subject "
            "(external_user_id); GET lists them, POST echoes the row just saved"
        ),
    ),
    "customer_saved_meal": own(
        "id",
        "deleted",
        via="apps.miniapp_api.views_saved_meals:customer_saved_meal",
        note=(
            "the id of the caller's own saved meal just deleted upstream, with the deleted "
            "flag; no dish fields come back on delete"
        ),
    ),
}


def test_every_customer_route_is_classified() -> None:
    check_every_route_is_classified(customer_urls, CUSTOMER_ROUTES)


def test_registry_has_no_stale_routes() -> None:
    check_no_stale_entries(customer_urls, CUSTOMER_ROUTES)


def test_no_unnamed_routes_and_the_census_is_not_empty() -> None:
    check_no_unnamed_routes_and_floor(customer_urls, ROUTE_FLOOR)


def test_reasons_are_written_not_templated() -> None:
    check_reasons_are_written_not_templated(CUSTOMER_ROUTES)


def test_own_and_third_party_name_their_fields() -> None:
    check_fields_named_where_data_flows(CUSTOMER_ROUTES)


def test_third_party_says_whose_and_why() -> None:
    check_third_party_names_whose_and_why(CUSTOMER_ROUTES)


def test_none_is_never_mixed_with_a_data_record() -> None:
    check_none_is_not_mixed(CUSTOMER_ROUTES)


def test_every_citation_resolves_to_a_payload_function() -> None:
    check_citations_resolve(CUSTOMER_ROUTES)


def test_planted_unregistered_route_turns_the_census_red() -> None:
    check_planted_route_goes_red(customer_urls, CUSTOMER_ROUTES)
