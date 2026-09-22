"""Personal-data route registry for the salon admin API (DRF-2094).

See :mod:`tests.support.pii_route_registry` for the record kinds and the
checks. One entry per named route in :mod:`apps.admin_api.urls`, each
citing the function that builds the response body (``via``).

The subject on this surface is a salon operator (owner / admin /
receptionist under ``require_admin_role``). Almost nothing here is the
caller's *own* data: the surface exists to read and act on other people
— the salon's masters, its staff and its customers — so most entries are
``third_party`` and must say whose data it is and why the operator sees
it. Customers reach the operator only as first name + last initial
(``DayVisit`` / ``build_schedule``) or as the picker's name; a phone
never does, and the one masked phone on the surface belongs to a master.
"""

from __future__ import annotations

from apps.admin_api import urls as admin_urls
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
    third_party,
)

A = "apps.admin_api."

#: Named routes in ``apps/admin_api/urls.py`` on dev 94125e1f (18.09.2026).
ROUTE_FLOOR = 32

_SETTLE_OUTCOME = none(
    "the §18 outcome envelope {outcome, detail, appointment_id, reason_code?}: a verdict "
    "about the action on one appointment id, never the customer or master behind it",
    via=A + "views_booking_complete:_outcome",
)

_AVAILABILITY_REQUEST = third_party(
    "master_id",
    "reason_class",
    "reason_text",
    "resolution_note",
    "requested_start / requested_end",
    via=A + "views_availability:_serialise_request",
    whose="the master who asked for a schedule change",
    why=(
        "the operator approves or rejects the master's own request and must read the reason "
        "the master wrote; the note is the operator's decision text on that request"
    ),
)

_MASTER_SCHEDULE = third_party(
    "days[] (working hours template)",
    "confirmation.confirmed_by.name",
    via=A + "views_master_schedule:_schedule_payload",
    whose="the master (working week) and the staff member who last confirmed it",
    why=(
        "the salon sets and confirms the master's working hours; the confirmer's display "
        "name is shown so the sheet says who signed the current template (_confirmation_payload)"
    ),
)

ADMIN_ROUTES: dict[str, Entry] = {
    # --- the day ------------------------------------------------------------
    "salon_day": third_party(
        "masters[].name",
        "visits[].client_first_name",
        "visits[].client_last_initial",
        "visits[].service_name",
        via=A + "views_day:_visit_payload",
        whose="the salon's masters and the customers with a visit that day",
        why=(
            "the day sheet must tell visits apart; the customer is reduced to first name + "
            "last initial by DayVisit, and the master row carries the published name (_day_payload)"
        ),
    ),
    "salon_day_frame": third_party(
        "masters[].display_name",
        "masters[].schedule_note",
        via=A + "views_salon_frame:_master_frame",
        whose="the salon's masters",
        why=(
            "the frame draws each master's working intervals for the date; the note is the "
            "master's own schedule remark as Ayla returns it, no customer row is in this body"
        ),
    ),
    # --- bookings on behalf of the salon ------------------------------------
    "create_booking": none(
        "the §18 outcome envelope {outcome, detail, appointment_id?, reason_code?, "
        "unsellable_reason?}; the customer chosen in the body is not echoed back",
        via=A + "views_booking_create:_outcome",
    ),
    "cancel_booking": none(
        "the outcome envelope {outcome, detail, appointment_id} for the cancellation; a "
        "verdict on one appointment id, no person field on any branch",
        via=A + "views_booking_cancel:_outcome",
    ),
    "booking_version": none(
        "{id, version, status, start_datetime} — the canonical facts of one appointment "
        "read from Ayla to seal a settle; no customer or master field is in the record",
        via=A + "views_booking_complete:booking_version",
    ),
    "complete_booking": _SETTLE_OUTCOME,
    "no_show_booking": _SETTLE_OUTCOME,
    "reschedule_booking": _SETTLE_OUTCOME,
    "booking_slots": none(
        "{time, start_at, duration_min} rows — bookable starts for one master/service/day "
        "from the availability client; timestamps, not people",
        via=A + "views_availability_slots:_slot_payload",
    ),
    "search_customers": third_party(
        "id",
        "name",
        "named",
        via=A + "views_customers:_public",
        whose="the salon's customers matching the search",
        why=(
            "the receptionist picks whom to book; the picker shows the name only — the "
            "channel handle is stripped and an empty name becomes a placeholder, phone is not returned"
        ),
    ),
    # --- masters --------------------------------------------------------------
    "masters_list": third_party(
        "name",
        "specialization",
        "photo_url",
        "is_active",
        "invite_status",
        "last_seen_at",
        via=A + "views:_row_to_list_item",
        whose="the salon's masters",
        why=(
            "the roster the owner manages: the published card fields plus the invite state "
            "and the linked account's last activity so the owner sees who is live"
        ),
    ),
    "masters_awaiting_verification": third_party(
        "name",
        "specialization",
        "photo_url",
        "invite_status",
        via=A + "views_master_verify:_row",
        whose="masters whose card is still hidden pending verification",
        why=(
            "the owner verifies the cards before they go public; POST answers with counts "
            "(verified/skipped/blocked) and no rows"
        ),
    ),
    "master_invite_create": none(
        "{master_id, invite_token, invite_expires_at, fallback_link, invite_link} — the "
        "credential handed to the invited master; the name and contact from the body are not echoed",
        via=A + "views_invite:_response_payload",
    ),
    "master_detail": third_party(
        "name",
        "specialization",
        "bio",
        "experience",
        "rating",
        "photo_url",
        "yclients_staff_id",
        "linked_bot_user.display_name",
        "linked_bot_user.phone_masked",
        "linked_bot_user.last_seen_at",
        "services[].name",
        via=A + "views:_detail_payload",
        whose="one master and the messenger account linked to them",
        why=(
            "the owner edits the master's card and needs to see which account claimed it; the "
            "phone is masked to the last two digits by _mask_phone and is the master's, never a customer's"
        ),
    ),
    "master_photo_upload": third_party(
        "photo_url",
        via=A + "views:master_photo_upload",
        whose="the master whose photo was just uploaded",
        why=(
            "the response is the public URL of the file the operator uploaded for that "
            "master's card; the file itself is stored under MEDIA_ROOT/master_photos"
        ),
    ),
    "master_schedule": _MASTER_SCHEDULE,
    "master_schedule_confirm": _MASTER_SCHEDULE,
    "master_day_schedule": third_party(
        "bookings[].client_first_name",
        "bookings[].client_last_initial",
        "bookings[].service_name",
        "working_hours / blocks / free_windows / conflicts",
        via="apps.master_api.services.schedule:build_schedule",
        whose="the master (working day) and the customers booked with them that day",
        why=(
            "the salon reads the master's day the way the master sees it; customers are the "
            "same first name + initial the master surface shows (_split_name), no phone"
        ),
    ),
    "master_exceptions": third_party(
        "exceptions[] (date, hours)",
        "time_off[].reason",
        "closures[].reason",
        via=A + "views_master_exceptions:master_exceptions",
        whose="the master whose off-days and time-off are listed",
        why=(
            "the operator reads why the master is unavailable in the window; the reason is "
            "the master's own free-text note as Ayla stores it (_time_off_row, _closure_row)"
        ),
    ),
    "master_schedule_impact": none(
        "appointment rows {appointment_id, start_local, end_local, service_name, status, "
        "payment_status, refund_percent_if_cancelled} — what a schedule change would hit; no person field",
        via=A + "views_schedule_impact:master_schedule_impact",
    ),
    "master_audit_feed": third_party(
        "actor_id",
        "actor_role",
        "payload (audit payload verbatim)",
        via=A + "views:master_audit_feed",
        whose="the staff who acted on this master, and the master the events are about",
        why=(
            "the owner reads who changed the master's card and when; the payload is the audit "
            "row's own dict and carries whatever the writing action recorded (ids, roles, sizes)"
        ),
    ),
    "master_deactivation_preview": third_party(
        "master.name",
        "future_bookings[].client_first_name",
        "future_bookings[].client_last_initial",
        "future_bookings[].fallback_masters[].name",
        via=A + "views_master_deactivation:_preview_to_payload",
        whose="the master being deactivated, their booked customers, and the fallback masters",
        why=(
            "the owner decides per booking whether to reassign or cancel and must see whom "
            "it affects; customers appear as first name + initial, fallbacks by published name"
        ),
    ),
    "master_deactivate": none(
        "{master_id, is_active, archived_at, summary counts} — the result of the cascade; "
        "how many were reassigned, cancelled or notified, not who",
        via=A + "views_master_deactivation:_deact_result_to_payload",
    ),
    "master_reactivate": none(
        "{master_id, is_active, archived_at} — the flipped state of the master row; no "
        "name or contact travels on reactivation",
        via=A + "views_master_deactivation:_reactivation_to_payload",
    ),
    # --- staff ----------------------------------------------------------------
    "staff_invite_create": none(
        "{invite_id, role, code, expires_at, code_is_shown_once, invite_link} — a one-time "
        "access code for a role; the person who will redeem it is not known yet",
        via=A + "views_staff_invite:staff_invite_create",
    ),
    "staff_revoke": none(
        "{changed, roles_revoked, master_unlinked} — what the revocation did to the target's "
        "roles; role names and flags, no field of the person",
        via=A + "views_staff_revoke:staff_revoke",
    ),
    "staff_role_change": none(
        "{role, previous_roles} — the role the target now holds and the roles it replaced; "
        "role names only, no field of the person",
        via=A + "views_staff_role:staff_role_change",
    ),
    "staff_restore": none(
        "{changed, role} — whether the revoked role came back and which role it was; "
        "a flag and a role name, no field of the person",
        via=A + "views_staff_role:staff_restore",
    ),
    "staff_roster": third_party(
        "items[].name",
        "items[].bot_user_id",
        "items[].master_id",
        "items[].has_account",
        "items[].roles[].since",
        via=A + "services.staff_roster:Person.to_payload",
        whose="the salon's staff — owners, admins, receptionists and masters",
        why=(
            "the owner sees who holds which role and since when; the name is display_name or "
            "client_name of the linked account (_name_from), no phone or handle"
        ),
    ),
    # --- services mapping -----------------------------------------------------
    "services_mapping_bulk": none(
        "{applied, conflicts[], new_snapshot_token} — how many service↔master edges were "
        "written and which collided; ids only",
        via=A + "views_services_mapping:services_mapping_bulk",
    ),
    "services_mapping_get": third_party(
        "masters[].name",
        "masters[].is_active",
        "masters[].invite_status",
        via=A + "views_services_mapping:services_mapping_get",
        whose="the salon's masters",
        why=(
            "the mapping grid needs a label per master column; the published name and the "
            "invite state, next to services that carry no person at all"
        ),
    ),
    # --- availability requests ------------------------------------------------
    "availability_requests_list": _AVAILABILITY_REQUEST,
    "availability_request_approve": _AVAILABILITY_REQUEST,
    "availability_request_reject": _AVAILABILITY_REQUEST,
    # --- DRF-2119 — ассистент администратора ------------------------------
    # Ответы — текст модели по данным инструментов; инструменты отдают
    # клиента ТОЛЬКО как «имя + инициал» из build_salon_day (у DayVisit нет
    # поля телефона), а на выходе стоит mask_phones. История — свои же
    # реплики администратора и ответы ассистента.
    "assistant_history": third_party(
        "messages[].content",
        via=A + "views_assistant:_message_dict",
        whose="the administrator's own questions and the assistant's answers",
        why=(
            "the admin re-opens their own thread; answers name customers by first name "
            "and initial from the salon day, never a phone (mask_phones on the way out)"
        ),
    ),
    "assistant_ask": third_party(
        "answer",
        "pending_action.summary",
        via=A + "services.assistant:AdminSubject.postprocess",
        whose="customers of the salon, by first name and initial",
        why=(
            "«найти запись» reads the same build_salon_day as «Сегодня»; the draft "
            "summary names the customer the admin themselves typed; phones are masked"
        ),
    ),
    "assistant_confirm": none(
        "the executed action's text names the master and the closed window; "
        "no customer is involved (only prepare_schedule_change has a token)",
        via=A + "services.assistant:execute_admin_action",
    ),
    # --- DRF-2117 — готовность салона поимённо ----------------------------
    "salon_readiness": third_party(
        "problems[].master.name",
        "problems[].text",
        via=A + "services.salon_readiness:Problem.as_dict",
        whose="the salon's masters (first name from the catalog / mirror row)",
        why=(
            "the owner or administrator reads which master blocks bookings and why "
            "(no schedule, no services, no catalog link, no free slots); the text names "
            "the master by first name so the sheet is actionable — no phone, no customer, "
            "no message text leaves this route"
        ),
    ),
    # --- DRF-2115 — очередь handoff для «Сегодня» --------------------------
    "handoff_queue": third_party(
        "addressee",
        via=A + "views_handoff_queue:_row",
        whose="the operator (or queue name) who claimed the handoff task",
        why=(
            "the salon operator sees how many customers wait for a human and for how long "
            "(task id, status, age, escalation flag) and which colleague has already taken "
            "the task; no customer name, channel id, reason or message text leaves this route "
            "— the transcript stays in the Django admin queue (DRF-1499)"
        ),
    ),
}


def test_every_admin_route_is_classified() -> None:
    check_every_route_is_classified(admin_urls, ADMIN_ROUTES)


def test_registry_has_no_stale_routes() -> None:
    check_no_stale_entries(admin_urls, ADMIN_ROUTES)


def test_no_unnamed_routes_and_the_census_is_not_empty() -> None:
    check_no_unnamed_routes_and_floor(admin_urls, ROUTE_FLOOR)


def test_reasons_are_written_not_templated() -> None:
    check_reasons_are_written_not_templated(ADMIN_ROUTES)


def test_own_and_third_party_name_their_fields() -> None:
    check_fields_named_where_data_flows(ADMIN_ROUTES)


def test_third_party_says_whose_and_why() -> None:
    check_third_party_names_whose_and_why(ADMIN_ROUTES)


def test_none_is_never_mixed_with_a_data_record() -> None:
    check_none_is_not_mixed(ADMIN_ROUTES)


def test_every_citation_resolves_to_a_payload_function() -> None:
    check_citations_resolve(ADMIN_ROUTES)


def test_planted_unregistered_route_turns_the_census_red() -> None:
    check_planted_route_goes_red(admin_urls, ADMIN_ROUTES)


def test_the_operator_surface_declares_no_own_record() -> None:
    """An ``own`` entry here would mean the admin API started answering about the
    operator themselves — a new class of payload that deserves its own reading."""

    from tests.support.pii_route_registry import Own, records_of

    owned = [n for n, e in ADMIN_ROUTES.items() if any(isinstance(r, Own) for r in records_of(e))]
    assert not owned, owned
