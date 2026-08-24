"""The registry of personal fields — who owns each one, and where it may go.

Read by ``tools/lint/personal_field_guard.py``, which discovers the slots
**from the code** and refuses any it cannot find a line for here. This
module is the declaration side only; it never lists what exists, so it
cannot quietly disagree with what exists.

# Why a registry at all

The memory audit of 2026-08-24 found six stores holding facts about the
same person, with — its own words — *no explicit conflict policy for
city, price, favorite master or goal*. None of those six was designed;
they accreted one field at a time, each addition locally sensible. The
missing step was never «design the memory». It was: at the moment a
personal slot is added, somebody says who owns it and whether it follows
the person out of the salon.

# Deliberately stdlib-only

The guard imports this file directly, without Django. Keep it that way:
no model imports, no settings, no ORM. It is a table.

# The columns

``origin`` — the audit's own classification, applied to the slot:

    USER_STATED     the person said it about themselves
    OBSERVED        a salon noticed it about them
    TRANSACTIONAL   it is a fact of what was booked/paid
    DERIVED         computed from the above
    INFERRED        a model guessed it from conversation
    SYSTEM          platform bookkeeping ABOUT the person, not about
                    what they want (a safety lock, an unset default)
    UNCLASSIFIED    the slot holds more than one of the above and
                    nobody has taken it apart — must be in POLICY_DEBT

``owner`` — which side is the source of truth. Per the owner's ruling of
2026-08-24 (decision 1) the backend's ``users.UserPersonalContext`` owns
**declared preferences**; the bot keeps the privacy machinery (zones,
tombstones, encryption, provenance) and the facts the backend has no
field for.

    BACKEND   users.UserPersonalContext in beautygo_backend
    BOT       this repository

``crosses_salons`` — whether the value read in salon B may include what
was learned in salon A. **Not a wish**: the guard derives the truth from
the store (a table with no ``tenant`` FK is cross-tenant; green
``MemoryEntry`` rows are read by ``user_id`` alone, see
``apps/identity/services/memory_reader.py:100``) and fails a declaration
that disagrees.

``why`` — the sentence a reviewer needs. A table of labels decays; a
table of reasons is reviewable. The guard enforces a floor on its length,
which is a crude proxy for «somebody thought about it».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

ORIGINS: frozenset[str] = frozenset(
    {
        "USER_STATED",
        "OBSERVED",
        "TRANSACTIONAL",
        "DERIVED",
        "INFERRED",
        "SYSTEM",
        "UNCLASSIFIED",
    }
)

OWNERS: frozenset[str] = frozenset({"BACKEND", "BOT"})


@dataclass(frozen=True)
class PersonalField:
    """One declared personal slot."""

    site: str
    origin: str
    owner: str
    crosses_salons: bool
    why: str


#: Slots the owner's ruling of 2026-08-24 (decision 3) names as a salon's
#: own observation, which therefore never follow the person out — even
#: when the person stated them out loud. The ruling's third item,
#: ``skin_sensitivities``, has no slot in this repository: it is a column
#: on the backend's ``users.UserPersonalContext`` and is invisible from
#: here (see the guard's KNOWN LIMITATIONS).
NEVER_CROSSES: frozenset[str] = frozenset(
    {
        "memory_key:favorite_masters",
    }
)


#: Slots that break a rule TODAY, named individually and never summarised.
#: The guard fails on a violation that is not listed here, and fails again
#: on a line that has stopped violating — so this can only shrink. Same
#: ratchet, and the same reason, as the 58 frozen class names in
#: ``tools/lint/miniapp_style_contract.py``: a one-line «known issues»
#: baseline is a way of not looking at them.
POLICY_DEBT: Mapping[str, str] = {
    "memory_key:favorite_masters": (
        "The person names a master out loud, so «сказал сам» would let it "
        "travel — but the ruling of 2026-08-24 overrides that for masters "
        "specifically: a favourite master is a relationship with one salon, "
        "and salon B learning it is salon A's commercial observation leaking. "
        "Today it travels: apps/persona/memory_extract.py:396 writes it as a "
        "green MemoryEntry and apps/identity/services/memory_reader.py:100 "
        "reads green rows by user_id with no tenant predicate. Fixing it is a "
        "read-path change (source_tenant_id stops being informational), which "
        "is not this change."
    ),
    "identity.UserPersonalContext.summary": (
        "Ayla's running prose summary of who the user is — INFERRED by "
        "definition, stored cross-tenant, deliberately unencrypted so the "
        "prompt builder can read it on every turn (see the model docstring). "
        "That is precisely «узнали о нём», crossing salons in free text where "
        "no key-level rule can reach it. It is also the one slot in this "
        "registry with no field-level shape at all, so a per-fact crossing "
        "policy cannot be applied to it without first giving it one."
    ),
    "identity.BotUser.context": (
        "A JSON scratch bag for «personalisation flags» with no schema. Two "
        'sub-keys are documented in prose — context["last_followup_sent_at"] '
        '(apps/bookings/followups.py:44) and context["nutrition_proactive"] '
        "(apps/nutrition_proactive/prefs.py:9) — and nothing stops a third "
        "arriving in a commit that touches no migration and no model. This is "
        "the exact growth pattern the guard was built for, in the one place "
        "the guard cannot see: a new personal field can appear inside this "
        "column without appearing in any scan. Taking it apart into named "
        "slots is a separate change."
    ),
}


PERSONAL_FIELDS: tuple[PersonalField, ...] = (
    # -----------------------------------------------------------------
    # identity.BotUser — the person themselves, per (tenant, channel).
    # -----------------------------------------------------------------
    PersonalField(
        site="identity.BotUser.avatar_url",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=False,
        why=(
            "Mirrored from the Ayla profile the person edits themselves; this "
            "row is a copy for rendering, not the place it is set."
        ),
    ),
    PersonalField(
        site="identity.BotUser.phone",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=False,
        why=(
            "The person's own contact number, E.164-normalised. The owner's "
            "standing ruling forbids handing it to a salon operator through "
            "the bot, so its presence here is for booking plumbing only."
        ),
    ),
    PersonalField(
        site="identity.BotUser.display_name",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Reported by the messaging channel, not typed by the person for "
            "us. Used to address them; never a preference they declared."
        ),
    ),
    PersonalField(
        site="identity.BotUser.client_name",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "The name the person typed when introducing themselves or "
            "booking. The backend has no counterpart wired from this repo, so "
            "the bot keeps it per decision 1's «facts the backend has no "
            "field for»."
        ),
    ),
    PersonalField(
        site="identity.BotUser.proactive_messages_opt_out",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "A stated wish not to be messaged first. Per-tenant on purpose: "
            "opting out of one salon's nudges is not opting out of every "
            "salon's."
        ),
    ),
    PersonalField(
        site="identity.BotUser.context",
        origin="UNCLASSIFIED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Unschematised JSON holding at least one stated preference "
            "(nutrition_proactive toggles) and at least one piece of "
            "bookkeeping (last_followup_sent_at) in the same column. See "
            "POLICY_DEBT."
        ),
    ),
    PersonalField(
        site="identity.BotUser.timezone",
        origin="SYSTEM",
        owner="BOT",
        crosses_salons=False,
        why=(
            "IANA zone for rendering times in messages. No runtime path "
            "writes it today (it is only read, at "
            "apps/identity/services/profile.py:96) — a personal slot standing "
            "at its default."
        ),
    ),
    # -----------------------------------------------------------------
    # identity.UserPreferences — the Mini App profile screen (F4).
    # -----------------------------------------------------------------
    PersonalField(
        site="identity.UserPreferences.notify_reminders",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Toggle the person set on their profile screen. Contractual: the "
            "proactive scheduler must honour it before sending reminders."
        ),
    ),
    PersonalField(
        site="identity.UserPreferences.notify_retention",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Toggle the person set on their profile screen, governing the "
            "«время обновить» nudge some weeks after the last visit."
        ),
    ),
    PersonalField(
        site="identity.UserPreferences.notify_promo",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Marketing opt-in, default OFF. A stated permission rather than a "
            "preference about beauty, but still the person's own statement."
        ),
    ),
    PersonalField(
        site="identity.UserPreferences.notify_birthday",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Toggle the person set on their profile screen; only fires when "
            "birthday_date is also filled in."
        ),
    ),
    PersonalField(
        site="identity.UserPreferences.birthday_date",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Optional date the person entered. The year is kept for "
            "age-conditional offers and never displayed back — that makes it "
            "a derived-age source as well as a greeting trigger."
        ),
    ),
    PersonalField(
        site="identity.UserPreferences.allergies",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Free-text contraindications, shown to the master before each "
            "booking. This is health text living in a plain column, outside "
            "MemoryEntry's zone/consent/TTL machinery entirely — it is not "
            "red-zoned, not consent-gated at write, and not reachable by "
            "forget-all. Flagged here rather than moved: relocating an "
            "existing field is not this change."
        ),
    ),
    # -----------------------------------------------------------------
    # identity.ClientProfile — the computed RFM/LTV/risk snapshot.
    # Every field here is derived from booking facts, which is why the
    # whole model must stay tenant-scoped: it is one salon's reading of
    # a customer, not the customer's account of themselves.
    # -----------------------------------------------------------------
    PersonalField(
        site="identity.ClientProfile.recency_days",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why="Days since last visit, computed from this salon's booking facts.",
    ),
    PersonalField(
        site="identity.ClientProfile.frequency_visits",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why="Visit count, computed from this salon's booking facts.",
    ),
    PersonalField(
        site="identity.ClientProfile.monetary_total",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Lifetime spend at this salon. What a person pays is the salon's "
            "commercial observation of them, never their own statement."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.rfm_segment",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "champion / loyal / at_risk / hibernating / new — a judgement "
            "about the person computed from the three fields above."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.ltv",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why="Realised lifetime value at this salon, derived from booking facts.",
    ),
    PersonalField(
        site="identity.ClientProfile.predicted_ltv_12m",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "A forecast about the person. Derived twice over — from a model "
            "run over derived aggregates."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.churn_risk",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Probability this salon loses the customer. Reading it in another "
            "salon would tell that salon what the first one fears."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.lifecycle_stage",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why="Lifecycle bucket computed from recency and frequency at this salon.",
    ),
    PersonalField(
        site="identity.ClientProfile.avg_visit_interval_days",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why="Mean gap between visits, computed from this salon's booking facts.",
    ),
    PersonalField(
        site="identity.ClientProfile.favorite_service_id",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "The service booked most often here. Looks like a preference and "
            "is not one: the person never said it, the salon counted it."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.favorite_category_id",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "The category booked most often here — same shape as "
            "favorite_service_id: counted, not stated."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.preferred_master_id",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "The master booked most often here. The counted twin of the "
            "stated memory key favorite_masters — the ruling of 2026-08-24 "
            "puts both on the never-crossing side, and this one is already "
            "there because the model is tenant-scoped."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.loyalty_tier",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Tier cached from the loyalty domain for O(1) prompt reads; the "
            "authority is loyalty.LoyaltyAccount.tier."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.last_review_rating",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why="The score the person gave this salon — the salon's record of being judged.",
    ),
    PersonalField(
        site="identity.ClientProfile.last_review_at",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "When that review landed. Bookkeeping about a personal act rather "
            "than about the row, so it is declared rather than waved through."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.low_rating_flag",
        origin="OBSERVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "«This customer rated us badly.» A salon's note about a person, "
            "and the clearest example of a field that must never travel."
        ),
    ),
    PersonalField(
        site="identity.ClientProfile.sentiment_score",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "A score for how the person feels, computed rather than asked. "
            "Derived affect is still an assertion about them."
        ),
    ),
    # -----------------------------------------------------------------
    # identity.UserPersonalContext (bot) — cross-tenant by design.
    # -----------------------------------------------------------------
    PersonalField(
        site="identity.UserPersonalContext.display_name_preferred",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=True,
        why=(
            "What the person wants to be called. They said it; making them "
            "repeat it in every salon is the disrespect decision 3 names."
        ),
    ),
    PersonalField(
        site="identity.UserPersonalContext.language_preferred",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=True,
        why=(
            "The language the person wants to be spoken to in. Stated, about "
            "themselves, and useless if it stopped at the salon door."
        ),
    ),
    PersonalField(
        site="identity.UserPersonalContext.summary",
        origin="INFERRED",
        owner="BOT",
        crosses_salons=True,
        why=(
            "Ayla's running prose account of who this person is, built from "
            "conversation and read into the prompt on every turn. See "
            "POLICY_DEBT — it crosses salons as free text."
        ),
    ),
    PersonalField(
        site="identity.UserPersonalContext.minor_lock",
        origin="SYSTEM",
        owner="BOT",
        crosses_salons=True,
        why=(
            "A protection, not a fact surfaced about the person: it blocks "
            "yellow/red writes once reconciliation finds the user is a minor. "
            "It must cross — a safety lock that stopped at the salon door "
            "would be no lock at all."
        ),
    ),
    # -----------------------------------------------------------------
    # loyalty.LoyaltyAccount — one row per (tenant, customer), forever.
    # -----------------------------------------------------------------
    PersonalField(
        site="loyalty.LoyaltyAccount.balance",
        origin="TRANSACTIONAL",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Redeemable points, cached from the LoyaltyEvent ledger. A fact "
            "of what was transacted with this salon, not about the person."
        ),
    ),
    PersonalField(
        site="loyalty.LoyaltyAccount.tier",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Standing computed from the ledger. It shapes what the person is "
            "offered, which is what makes it personal rather than accounting."
        ),
    ),
    PersonalField(
        site="loyalty.LoyaltyAccount.tier_changed_at",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "When the standing last moved — an input to tier decay, so it "
            "shapes future offers rather than merely recording the past."
        ),
    ),
    PersonalField(
        site="loyalty.LoyaltyAccount.tier_reset_at",
        origin="DERIVED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "When the standing is next re-evaluated; same reason as "
            "tier_changed_at — it is read forward, not only written."
        ),
    ),
    PersonalField(
        site="loyalty.LoyaltyAccount.enrolled",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "Whether the person joined this salon's programme. Their own "
            "decision, and per-salon by nature."
        ),
    ),
    PersonalField(
        site="loyalty.LoyaltyAccount.opted_out_at",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=False,
        why=(
            "When the person left the programme. A stated withdrawal, kept "
            "because re-enrolment must not silently resurrect the old balance."
        ),
    ),
    # -----------------------------------------------------------------
    # Memory keys — the personal fields INSIDE MemoryEntry.content.
    # All of them cross today: green rows are read by user_id alone.
    # -----------------------------------------------------------------
    PersonalField(
        site="memory_key:diet",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=True,
        why=(
            "«Я вегетарианка» — said about themselves. Decision 1 puts "
            "declared preferences under the backend's users.UserPersonalContext; "
            "the bot row is the capture-and-privacy layer bridged there."
        ),
    ),
    PersonalField(
        site="memory_key:preferred_time_slots",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=True,
        why=(
            "Durable «удобное время» anchors the person named. Declared "
            "preference, bridged to the backend field of the same name."
        ),
    ),
    PersonalField(
        site="memory_key:preferred_districts",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=True,
        why=(
            "Districts the person named as convenient. Declared preference, "
            "bridged to the backend field of the same name."
        ),
    ),
    PersonalField(
        site="memory_key:price_range",
        origin="USER_STATED",
        owner="BACKEND",
        crosses_salons=True,
        why=(
            "The budget the person named, bridged to price_range_min/max. "
            "Section 5 of the audit lists price as having no conflict policy "
            "between the two stores; decision 1 settles the direction, not yet "
            "the merge rule."
        ),
    ),
    PersonalField(
        site="memory_key:favorite_masters",
        origin="USER_STATED",
        owner="BOT",
        crosses_salons=True,
        why=(
            "A master the person named out loud. Bot-owned because the bridge "
            "cannot resolve a name to the SpecialistProfile UUID the backend "
            "contract wants. See POLICY_DEBT — it must not cross, and does."
        ),
    ),
)


#: Slots that are keyed to a person and permanent but are NOT personal
#: fields: they decide where a message goes, or record that something
#: happened to the row, and are never read back to shape what Ayla says.
#: Declared so the guard bites on a NEW field rather than shrugging at
#: every timestamp — and so a false positive here costs one line, not the
#: lint's credibility.
NOT_PERSONAL: Mapping[str, str] = {
    # identity.BotUser
    "identity.BotUser.id": "Row identity.",
    "identity.BotUser.tenant": "Scoping — which salon this shell of the person belongs to.",
    "identity.BotUser.ayla_user_id": "Identity bridge to the canonical Ayla user; an address, not a fact.",
    "identity.BotUser.channel": "Routing — which messenger this shell speaks over.",
    "identity.BotUser.channel_user_id": "Routing — the person's id inside that messenger.",
    "identity.BotUser.chat_id": "Routing — where outbound sends land. Decides where, never what.",
    "identity.BotUser.first_seen": "Row bookkeeping.",
    "identity.BotUser.last_seen": "Row bookkeeping; activity recency for the profile is a separate derived field.",
    "identity.BotUser.welcomed_at": "Onboarding bookkeeping — whether S1 welcome already ran.",
    "identity.BotUser.consent_at": (
        "A permission record, not a fact about the person. Note DRF-1314: it "
        "answers «ever consented», never «may we write to them», and is "
        "separately guarded by tools/lint/consent_column_guard.py."
    ),
    "identity.BotUser.food_scanner_consent_at": "Permission record for the food scanner surface.",
    "identity.BotUser.deleted_at": "Erasure bookkeeping — when deletion was requested.",
    # identity.UserPreferences
    "identity.UserPreferences.bot_user": "Row identity — the person this row is.",
    "identity.UserPreferences.tenant": "Scoping.",
    "identity.UserPreferences.created_at": "Row bookkeeping.",
    "identity.UserPreferences.updated_at": "Row bookkeeping.",
    # identity.ClientProfile
    "identity.ClientProfile.bot_user": "Row identity — the person this row is.",
    "identity.ClientProfile.tenant": "Scoping.",
    "identity.ClientProfile.last_recomputed_at": "Cache bookkeeping — when the aggregates were last rebuilt.",
    # identity.UserPersonalContext
    "identity.UserPersonalContext.user_id": "Row identity — the person this row is.",
    "identity.UserPersonalContext.created_at": "Row bookkeeping.",
    "identity.UserPersonalContext.updated_at": "Row bookkeeping.",
    "identity.UserPersonalContext.soft_deleted_at": "Erasure bookkeeping — the forget-all tombstone.",
    "identity.UserPersonalContext.forget_all_requested_at": "Erasure bookkeeping — when the person asked.",
    # loyalty.LoyaltyAccount
    "loyalty.LoyaltyAccount.id": "Row identity.",
    "loyalty.LoyaltyAccount.tenant": "Scoping.",
    "loyalty.LoyaltyAccount.customer": "Row identity — the person this account is.",
    "loyalty.LoyaltyAccount.created_at": "Row bookkeeping.",
    "loyalty.LoyaltyAccount.updated_at": "Row bookkeeping.",
    # experiments.Holdout — membership is a platform cohort assignment.
    # Nothing here is read back to shape what Ayla SAYS; it decides only
    # whether an experiment's treatment applies at all.
    "experiments.Holdout.id": "Row identity.",
    "experiments.Holdout.tenant": "Scoping — denormalised for the scanner.",
    "experiments.Holdout.bot_user": (
        "Row identity. Holdout membership is a platform cohort assignment: it "
        "gates whether experiment treatment applies, and is never surfaced to "
        "the person or used to choose what they are offered."
    ),
    "experiments.Holdout.since": "Row bookkeeping.",
}
