"""The registry of dialogue readers — the standing half of the DRF-1369 guarantee.

``OD_MEMORY.md`` §4 asks for a **guarantee** that an erased person's переписка
cannot reach a prompt. A guarantee is not proved by the absence of a call. The
contour had already demonstrated that: ``short_term.clear`` documented itself
as «used by the 152-ФЗ delete-my-data workflow», was written by someone who
meant it, and had no caller anywhere in ``apps/``. The intention was on disk;
the guarantee was not.

Nor is it proved by a test that names the three readers we happen to know
about. That test passes forever and says nothing about the fourth.

So the shape of the guard is: **discover the readers, then require each one to
have been classified.** The AST scan below finds every place in ``apps/`` that
can obtain the text of a dialogue turn. :data:`DIALOGUE_READERS` says, for each
of them, whether it feeds a prompt and why that is safe. The test in
``apps/conversations/tests/test_dialogue_reader_registry.py`` fails when the
two disagree in either direction:

    a reader exists and is not in the registry   →  red  (the new-reader case)
    the registry names a reader that is gone     →  red  (registry rot)
    a reader is marked prompt-bound with no probe→  red  (unproven claim)
    a probe returns the erased person's words    →  red  (the actual leak)

The fourth line is the one that matters. Every entry marked
``reaches_prompt=True`` carries a live probe that is run against a real
anonymised conversation, so «guarantee» is a passing assertion about behaviour
rather than a statement about call graphs. A reader added next month is red on
line one until its author classifies it, and red on line four unless the
classification is true.

# Why the mechanism underneath is not a filter

The registry is the *watchdog*, not the fix. The fix is that
``apps.conversations.erasure.anonymize_dialogue`` **moves** the text out of
``Message.content`` / ``Message.rendered_text`` into ``ArchivedMessage`` and
deletes the two Redis stores. So the default for an unclassified new reader is
already safe — it reads an empty column. The registry exists so that the day
someone points a prompt at ``ArchivedMessage``, or reintroduces a text store,
the build says so out loud instead of shipping quietly.

# Scope of the scan, stated honestly

Detected: any use of the ``Message`` / ``ArchivedMessage`` model managers that
can yield row text, any ``short_term.recall`` call, and any call of
``read_anonymized_dialogue``. Aggregations and writes (``count``, ``exists``,
``update``, ``create``, …) are not reads and are skipped; a ``values``/
``values_list`` projection counts only when it names a text column.

Import aliasing (``from apps.conversations.models import Message as M``) is
followed, for the same reason ``tools/lint/import_boundaries.py`` follows it:
a guard that a rename walks past is a guard that reports what it was told
rather than what is there.

NOT detected, and named rather than left implicit: a reader that reaches the
rows through a value the scanner cannot resolve (a queryset passed in as an
argument, ``apps.get_model("conversations", "Message")``, raw SQL). Those are
covered by the mechanism, not by this scan — the column is empty for them too.
The scan's job is the *authored* read, caught at authoring time.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: Model names whose managers yield dialogue text.
_DIALOGUE_MODELS = frozenset({"Message", "ArchivedMessage"})

#: Manager attributes on those models.
_MANAGERS = frozenset({"objects", "all_tenants"})

#: Queryset terminals that are not reads of row text — aggregations and
#: writes. A chain ending in one of these cannot hand anyone a message body.
_NON_READ_TERMINALS = frozenset(
    {
        "count",
        "acount",
        "exists",
        "aexists",
        "aggregate",
        "annotate",
        "create",
        "acreate",
        "bulk_create",
        "bulk_update",
        "update",
        "delete",
        "get_or_create",
        "update_or_create",
        "in_bulk",
    }
)

#: The columns that hold free dialogue text. A ``values``/``values_list``
#: projection is a read only when it names one of these.
DIALOGUE_TEXT_COLUMNS = frozenset(
    # `action_data` belongs here and it is not obvious: the clarification
    # block inside it holds the question the person was asked and the
    # options they were offered, and the MAX handler projects exactly
    # `values_list("content", "action_data")` to rebuild a pending
    # multi-select. Leaving it out let a prompt path read as metadata.
    {"content", "rendered_text", "body", "rendered_body", "action_data", "tool_call"}
)

#: Free functions whose call is itself a dialogue read, regardless of model.
_READ_FUNCTIONS: dict[tuple[str, str], str] = {
    ("short_term", "recall"): "redis_window",
    ("erasure", "read_anonymized_dialogue"): "archive",
}
_BARE_READ_FUNCTIONS: dict[str, str] = {
    "read_anonymized_dialogue": "archive",
}


@dataclass(frozen=True)
class DialogueReader:
    """One classified reader of dialogue text."""

    #: ``db_message`` | ``redis_window`` | ``archive``.
    storage: str
    #: Does the text this reader returns end up in an LLM prompt?
    reaches_prompt: bool
    #: Why this reader is safe after an erasure. One sentence, no hedging.
    why: str


@dataclass(frozen=True)
class ReadSite:
    """A discovered read, keyed the way the registry keys it."""

    key: str
    storage: str
    lineno: int


def _qualified(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    return ".".join(rel.parts)


def _chain_methods(node: ast.AST, parents: dict[int, ast.AST]) -> tuple[list[str], list[ast.Call]]:
    """Climb the ``Message.all_tenants.filter(...).order_by(...)[:n]`` chain.

    Returns the method names in call order plus the ``Call`` nodes, so the
    caller can inspect ``values_list`` arguments.
    """

    methods: list[str] = []
    calls: list[ast.Call] = []
    cur: ast.AST = node
    while True:
        parent = parents.get(id(cur))
        if isinstance(parent, ast.Attribute) and parent.value is cur:
            methods.append(parent.attr)
            cur = parent
        elif isinstance(parent, ast.Call) and parent.func is cur:
            calls.append(parent)
            cur = parent
        elif isinstance(parent, ast.Subscript) and parent.value is cur:
            cur = parent
        else:
            return methods, calls


def _projection_reads_text(methods: list[str], calls: list[ast.Call]) -> bool:
    """Does a ``values``/``values_list`` chain name a dialogue text column?"""

    for call in calls:
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in {"values", "values_list"}:
            for arg in call.args:
                if isinstance(arg, ast.Constant) and arg.value in DIALOGUE_TEXT_COLUMNS:
                    return True
            return False
    return False


def _model_aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> dialogue model, following ``import ... as``.

    ``from apps.conversations.models import Message as M`` is the cheapest way
    to walk past a scanner that matches on the literal class name, and the
    repo's other AST guard (``tools/lint/import_boundaries.py``) already treats
    aliasing as part of the adversarial set it must catch. Same standard here:
    a reader is a reader whatever it calls the class locally.
    """

    aliases: dict[str, str] = {name: name for name in _DIALOGUE_MODELS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "apps.conversations.models":
            continue
        for alias in node.names:
            if alias.name in _DIALOGUE_MODELS and alias.asname:
                aliases[alias.asname] = alias.name
    return aliases


def _scan_module(path: Path, root: Path) -> list[ReadSite]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    aliases = _model_aliases(tree)

    parents: dict[int, ast.AST] = {}
    scopes: dict[int, str] = {}
    stack: list[str] = []

    def walk(node: ast.AST) -> None:
        pushed = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack.append(node.name)
            pushed = True
        scopes[id(node)] = _qualified(stack)
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            walk(child)
        if pushed:
            stack.pop()

    walk(tree)

    module = _module_name(path, root)
    found: list[ReadSite] = []
    for node in ast.walk(tree):
        storage: str | None = None

        # 1. Model manager chains: Message.all_tenants.filter(...)…
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _MANAGERS
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            methods, calls = _chain_methods(node, parents)
            if any(m in _NON_READ_TERMINALS for m in methods):
                continue
            if any(m in {"values", "values_list"} for m in methods) and not _projection_reads_text(
                methods, calls
            ):
                continue
            model = aliases[node.value.id]
            storage = "archive" if model == "ArchivedMessage" else "db_message"

        # 2. Named read functions: short_term.recall(...), read_anonymized_dialogue(...)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                storage = _READ_FUNCTIONS.get((func.value.id, func.attr))
            elif isinstance(func, ast.Name):
                storage = _BARE_READ_FUNCTIONS.get(func.id)

        if storage is None:
            continue
        found.append(
            ReadSite(
                key=f"{module}:{scopes.get(id(node), '<module>')}",
                storage=storage,
                lineno=getattr(node, "lineno", 0),
            )
        )
    return found


def discover_read_sites(apps_root: Path) -> dict[str, ReadSite]:
    """Every authored dialogue read under ``apps_root``, keyed ``module:qualname``.

    Test modules are excluded: a test that reads a message body is not a
    production route to a prompt, and pinning them would make the registry
    churn on every new test.
    """

    sites: dict[str, ReadSite] = {}
    for path in sorted(apps_root.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "migrations" in parts:
            continue
        for site in _scan_module(path, apps_root):
            sites.setdefault(site.key, site)
    return sites


#: Every reader of dialogue text in ``apps/``, classified.
#:
#: Adding a reader without adding a row here fails
#: ``test_dialogue_reader_registry.py``. Marking one ``reaches_prompt=True``
#: without a probe in that module fails too, and a probe that hands back the
#: erased person's words fails loudest of all.
DIALOGUE_READERS: dict[str, DialogueReader] = {
    # ── Prompt-bound ────────────────────────────────────────────────────
    "apps.master_api.services.ai_drafts:_recent_history": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "The master's AI draft is assembled from these rows — the route "
            "the audit missed while looking at the concierge. Anonymisation "
            "empties content/rendered_text, and this reader also drops every "
            "row at or before Conversation.anonymized_through."
        ),
    ),
    "apps.master_api.services.ai_drafts:_latest_customer_message": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "The row the draft is answering. Its body is empty after "
            "anonymisation; the id it is mostly used for is not personal data."
        ),
    ),
    "apps.channels.max.handler:_last_user_content": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "Re-feeds the last customer turn to the model on a retry. Reads "
            "Message.content, which anonymisation empties; the caller already "
            "treats a blank as «no text to retry»."
        ),
    ),
    "apps.channels.max.handler:_last_clarification_offer": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "Rebuilds the pending multi-select from the ASSISTANT turn. Empty "
            "content after anonymisation degrades to «the question is gone», "
            "which is the documented safe direction of this reader."
        ),
    ),
    "apps.channels.max.handler:_handle_global_max_event_inner": DialogueReader(
        storage="redis_window",
        reaches_prompt=True,
        why=(
            "THE history of the MAX prompt (the audit thought it was the "
            "concierge). The window is deleted by short_term.clear at "
            "anonymisation — the production caller that docstring promised."
        ),
    ),
    "apps.orchestrator.memory.coordinator:load_snapshot": DialogueReader(
        storage="redis_window",
        reaches_prompt=True,
        why="Same Redis window as the MAX handler, deleted at anonymisation.",
    ),
    "apps.orchestrator.concierge:_conversation_text": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "Builds the «what was actually said» evidence string that gates a "
            "grounded reply. Reads Message.content, emptied by anonymisation; "
            "the function already degrades to «only this turn was said»."
        ),
    ),
    "apps.orchestrator.concierge:GlobalConversationStore.load_recent_history": DialogueReader(
        storage="db_message",
        reaches_prompt=True,
        why=(
            "A protocol method with NO production caller — the audit believed "
            "this was the MAX prompt's history and it is not. Deliberately not "
            "deleted (that is a separate decision), so it is classified and "
            "probed like a live reader: if it is ever wired up, it is already "
            "reading an emptied column."
        ),
    ),
    # ── Not prompt-bound ────────────────────────────────────────────────
    "apps.conversations.erasure:anonymize_dialogue": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="The anonymiser itself — it reads the bodies in order to move them.",
    ),
    "apps.conversations.erasure:read_anonymized_dialogue": DialogueReader(
        storage="archive",
        reaches_prompt=False,
        why=(
            "The sole sanctioned read of the archive: incident review and "
            "booking disputes, the purpose the owner kept the text for. "
            "Mandatory `purpose`, one audit row per read, never a prompt."
        ),
    ),
    "apps.conversations.admin:MessageAdmin.get_queryset": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Django admin list — an operator surface, and it reads what is left in the column.",
    ),
    "apps.handoff.services:package_transcript": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why=(
            "Packages the transcript for a human admin taking the "
            "conversation over. Not a prompt; and after anonymisation the "
            "bodies it copies are empty."
        ),
    ),
    "apps.master_api.services.conversations:list_master_conversations": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Master inbox list — last-message preview and SLA timestamps, rendered to a human.",
    ),
    "apps.master_api.services.conversation_detail:get_conversation_detail": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="The master's chat screen — a human surface, already PII-redacted for the master.",
    ),
    "apps.observability.delta:_load_shadow_rows": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Shadow-mode diffing for observability; compares bot outputs, never composes a prompt.",
    ),
    "apps.skills.privacy_consent.tools:data_export": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why=(
            "The subject's own 152-ФЗ export. Reading their data back to them "
            "is the right they exercised, not a route into a prompt."
        ),
    ),
    "apps.skills.welcome.skill:_flow_already_established": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Returning-customer detection — row counts split by conversation, not bodies.",
    ),
    "apps.master_api.services.conversation_detail:mark_conversation_read": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why=(
            "Counts unread rows to stamp last_read_by_master_at. The chain "
            "ends in .count() on a variable the scanner cannot follow, so it "
            "surfaces here rather than being skipped — no body is read."
        ),
    ),
    "apps.master_api.services.dashboard:_customer_intent_hint": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why=(
            "One-line preview of the customer's last turn on the master's "
            "dashboard. A human surface; after anonymisation it renders empty."
        ),
    ),
    "apps.master_api.services.dashboard:get_inbox_preview": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Inbox ordering and «still waiting» detection — timestamps and roles, not bodies.",
    ),
    "apps.master_api.services.dashboard:get_tab_badges": DialogueReader(
        storage="db_message",
        reaches_prompt=False,
        why="Unread badge counters — timestamps and roles, not bodies.",
    ),
}
