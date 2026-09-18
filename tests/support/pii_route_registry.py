"""PII route-classification registry — the shared machinery (DRF-2094).

A registry maps every **named** route of a URL module to a record that says
what personal data the response carries and whose it is. The record kinds:

* :func:`own` — fields about the calling subject themselves;
* :func:`third_party` — fields about another natural person (a master, a
  customer, a staff member), with ``whose`` and ``why`` spelled out;
* :func:`none` — the response carries no personal data, with a reason that
  names what the payload IS instead.

Every record cites the function that builds the payload (``via``), as
``"dotted.module:qualname"``. The citation is resolved by import at test
time, so a registry entry cannot point at a function that does not exist —
the entry is a reading of the code, not an opinion about it.

A route may map to a tuple of records when one payload mixes kinds (a
customer's own booking that also carries the master's name). ``none``
cannot be mixed with anything: «no personal data» and «these fields» are
contradictory claims about one body.

The ``check_*`` functions are the same for every surface; the
per-surface test module supplies the URL module, the registry and the
floor. They are shaped as plain functions so the surface test can expose
each as its own pytest node (one red per defect, not one red per file).
"""

from __future__ import annotations

import importlib
from collections import Counter
from dataclasses import dataclass
from types import ModuleType
from typing import Any

#: A reason shorter than this is a label, not a reason («proxy», «no PII»).
MIN_REASON_CHARS = 40
#: The same reason text on more than this many routes is a template.
MAX_REASON_REPEATS = 3


@dataclass(frozen=True)
class Own:
    fields: tuple[str, ...]
    via: str
    note: str


@dataclass(frozen=True)
class ThirdParty:
    fields: tuple[str, ...]
    via: str
    whose: str
    why: str


@dataclass(frozen=True)
class NoPersonalData:
    via: str
    reason: str


Record = Own | ThirdParty | NoPersonalData
Entry = Record | tuple[Record, ...]


def own(*fields: str, via: str, note: str) -> Own:
    return Own(fields=fields, via=via, note=note)


def third_party(*fields: str, via: str, whose: str, why: str) -> ThirdParty:
    return ThirdParty(fields=fields, via=via, whose=whose, why=why)


def none(reason: str, *, via: str) -> NoPersonalData:
    return NoPersonalData(via=via, reason=reason)


def records_of(entry: Entry) -> tuple[Record, ...]:
    return entry if isinstance(entry, tuple) else (entry,)


def reason_of(record: Record) -> str:
    if isinstance(record, Own):
        return record.note
    if isinstance(record, ThirdParty):
        return record.why
    return record.reason


def resolve_citation(via: str) -> Any:
    """``"pkg.module:Outer.inner"`` → the object, or raise with the path."""

    module_name, _, qualname = via.partition(":")
    if not module_name or not qualname:
        raise LookupError(f"citation {via!r} must be 'dotted.module:qualname'")
    obj: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise LookupError(f"citation {via!r}: {part!r} not found") from exc
    return obj


def declared_route_names(urls: ModuleType) -> tuple[set[str], int]:
    """Named routes of the module and the number of unnamed ones."""

    patterns = list(urls.urlpatterns)
    named = {p.name for p in patterns if getattr(p, "name", None)}
    return named, len(patterns) - len(named)


# --- the checks -------------------------------------------------------------


def check_every_route_is_classified(urls: ModuleType, registry: dict[str, Entry]) -> None:
    declared, _ = declared_route_names(urls)
    unclassified = declared - set(registry)
    assert not unclassified, (
        f"{len(unclassified)} route(s) in {urls.__name__} are not classified for "
        f"personal-data exposure: {sorted(unclassified)}. Add each to the registry "
        "as own(...) / third_party(...) / none(...), citing the payload function."
    )


def check_no_stale_entries(urls: ModuleType, registry: dict[str, Entry]) -> None:
    declared, _ = declared_route_names(urls)
    stale = set(registry) - declared
    assert not stale, (
        f"registry names route(s) that no longer exist in {urls.__name__}: {sorted(stale)}"
    )


def check_no_unnamed_routes_and_floor(urls: ModuleType, floor: int) -> None:
    declared, unnamed = declared_route_names(urls)
    assert unnamed == 0, (
        f"{unnamed} unnamed route(s) in {urls.__name__} — a route without a name "
        "cannot be classified; name it so the registry can hold it."
    )
    assert len(declared) >= floor, (
        f"{urls.__name__} declares {len(declared)} named routes, below the floor of "
        f"{floor}: either routes were removed (lower the floor deliberately) or the "
        "enumeration is looking at the wrong module."
    )


def check_reasons_are_written_not_templated(registry: dict[str, Entry]) -> None:
    short: list[str] = []
    texts: Counter[str] = Counter()
    seen: set[int] = set()
    for name, entry in registry.items():
        for record in records_of(entry):
            text = reason_of(record).strip()
            if len(text) < MIN_REASON_CHARS:
                short.append(f"{name}: {text!r}")
            # One record object shared by several routes is a declared shared
            # serializer (one reading of one payload function); the same text
            # typed into separate records is the template this check is after.
            if id(record) in seen:
                continue
            seen.add(id(record))
            texts[text] += 1
    assert not short, (
        f"reason shorter than {MIN_REASON_CHARS} chars (a label, not a reason): {short}"
    )
    repeated = {t: c for t, c in texts.items() if c > MAX_REASON_REPEATS}
    assert not repeated, (
        f"reason text used on more than {MAX_REASON_REPEATS} routes reads as a template, "
        f"not a reading of each payload: {repeated}"
    )


def check_fields_named_where_data_flows(registry: dict[str, Entry]) -> None:
    empty = [
        name
        for name, entry in registry.items()
        for record in records_of(entry)
        if not isinstance(record, NoPersonalData) and not record.fields
    ]
    assert not empty, f"own/third_party without a single named field: {empty}"


def check_third_party_names_whose_and_why(registry: dict[str, Entry]) -> None:
    bad = [
        name
        for name, entry in registry.items()
        for record in records_of(entry)
        if isinstance(record, ThirdParty)
        and (not record.whose.strip() or len(record.why.strip()) < MIN_REASON_CHARS)
    ]
    assert not bad, f"third_party must say whose data it is and why the caller sees it: {bad}"


def check_none_is_not_mixed(registry: dict[str, Entry]) -> None:
    mixed = [
        name
        for name, entry in registry.items()
        if len(records_of(entry)) > 1
        and any(isinstance(r, NoPersonalData) for r in records_of(entry))
    ]
    assert not mixed, f"none(...) combined with a data record contradicts itself: {mixed}"


def check_citations_resolve(registry: dict[str, Entry]) -> None:
    broken: list[str] = []
    for name, entry in registry.items():
        for record in records_of(entry):
            try:
                target = resolve_citation(record.via)
            except (ImportError, LookupError) as exc:
                broken.append(f"{name}: {exc}")
                continue
            if not callable(target):
                broken.append(f"{name}: {record.via!r} is not a function")
    assert not broken, f"citation does not resolve to a payload function: {broken}"


def check_planted_route_goes_red(urls: ModuleType, registry: dict[str, Entry]) -> None:
    """The guard on the guard: an unregistered route must turn the census red."""

    from django.urls import path

    import pytest

    def probe_unclassified(_request: Any) -> Any:  # pragma: no cover — never called
        raise AssertionError("probe route must never be dispatched")

    planted = ModuleType(urls.__name__)
    setattr(
        planted,
        "urlpatterns",
        [*urls.urlpatterns, path("probe/", probe_unclassified, name="probe_unclassified")],
    )
    with pytest.raises(AssertionError, match="probe_unclassified"):
        check_every_route_is_classified(planted, registry)


__all__ = [
    "Entry",
    "MAX_REASON_REPEATS",
    "MIN_REASON_CHARS",
    "NoPersonalData",
    "Own",
    "Record",
    "ThirdParty",
    "check_citations_resolve",
    "check_every_route_is_classified",
    "check_fields_named_where_data_flows",
    "check_no_stale_entries",
    "check_no_unnamed_routes_and_floor",
    "check_none_is_not_mixed",
    "check_planted_route_goes_red",
    "check_reasons_are_written_not_templated",
    "check_third_party_names_whose_and_why",
    "declared_route_names",
    "none",
    "own",
    "records_of",
    "resolve_citation",
    "third_party",
]
