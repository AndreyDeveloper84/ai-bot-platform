"""No model-provider call escapes a replay run (DRF-2599).

### The defect

The live-path gate (``apps/replay/tests/test_live_path_gate.py``) claimed
«on this input the system answered without consulting a model», and proved it
by replacing ONE function — ``apps.orchestrator.concierge.generate_concierge_reply``.
Every other route to a model was open. ``apps/orchestrator/intent_resolution.py``
calls ``llm_client.chat.completions.create`` on an ``AsyncOpenAI`` client built
in ``apps/orchestrator/llm/openai_provider.py`` — so with a key in the
environment the gate made a real, paid call to OpenAI with the fixture text
(observed 28.09: it failed only because the local key was invalid, 401). CI has
no key, so the defect left no red trace anywhere.

### Where the block sits, and why there

Not on a name somebody may have bound at import. ``from x import call_model``
keeps its own reference, and patching ``x.call_model`` does not reach it — the
same trap another window hit today with a scheduler captured at import. The
block sits on the SDK **resource classes** (``Completions.create`` and friends):
every client instance, however its module imported the SDK, looks the method up
on the class at call time.

Census on dev ``b094bcc5`` (855 files): every path from our code to a model
goes through the ``openai`` or ``anthropic`` SDK — 5 import sites
(``apps/llm/providers/{openai,anthropic}_provider.py``,
``apps/orchestrator/llm/openai_provider.py``, ``apps/speech/providers/openai_stt.py``
×2); no raw HTTP to a provider host (the 12 files naming ``api.openai.com`` /
``api.anthropic.com`` do so in comments).

### Record, then raise

Callers of the model are written to never raise («resolver must never break
the turn» — ``except Exception``). A guard that only raised would be swallowed
and the run would stay green. So every attempt is **recorded** with the caller's
address first, and the gate asserts on the record, not on an exception.

### Limits — named, not closed

* **Same process only.** The patch is process-wide (every thread, every task in
  this interpreter, Celery in eager mode). A separate worker process, or a
  subprocess, is not covered.
* **Only these SDKs.** A new provider SDK, or raw HTTP to a model host, would
  pass. The census above is the reason it is complete today; the test
  ``test_census_every_sdk_import_is_covered`` re-counts on every run.
* An empty ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` is a second layer (it
  turns a paid call into a 401), never a replacement: the call still leaves.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import FrameType

#: (module, class) of every SDK resource whose ``create`` reaches a model.
BLOCKED: tuple[tuple[str, str], ...] = (
    ("openai.resources.chat.completions.completions", "Completions"),
    ("openai.resources.chat.completions.completions", "AsyncCompletions"),
    ("openai.resources.embeddings", "Embeddings"),
    ("openai.resources.embeddings", "AsyncEmbeddings"),
    ("openai.resources.audio.transcriptions", "Transcriptions"),
    ("openai.resources.audio.transcriptions", "AsyncTranscriptions"),
    ("anthropic.resources.messages.messages", "Messages"),
    ("anthropic.resources.messages.messages", "AsyncMessages"),
)


class ProviderCallForbidden(Exception):
    """A model provider was called inside a replay run."""


@dataclass(frozen=True)
class ProviderCall:
    sdk_method: str
    caller: str  # "path/relative/to/repo.py:line"


@dataclass
class ProviderGuard:
    calls: list[ProviderCall] = field(default_factory=list)

    @property
    def called(self) -> bool:
        return bool(self.calls)


def _short(filename: str) -> str:
    marker = filename.rfind("/apps/")
    if marker == -1:
        marker = filename.rfind("/tests/")
    return filename[marker + 1 :] if marker != -1 else filename


def _caller_address() -> str:
    """Our code that reached the SDK, as a short chain of our own frames.

    «apps/llm/providers/openai_provider.py:270» alone names the wrapper every
    call goes through and answers nothing; the callers behind it do.
    """
    ours: list[str] = []
    frame: FrameType | None = sys._getframe(2)
    while frame is not None:
        filename = frame.f_code.co_filename.replace("\\", "/")
        if (
            not filename.endswith("/provider_guard.py")
            and "/site-packages/" not in filename
            and "/openai/" not in filename
            and "/anthropic/" not in filename
            and "/asyncio/" not in filename
        ):
            ours.append(f"{_short(filename)}:{frame.f_lineno}")
        frame = frame.f_back
    if not ours:
        return "<unknown>"
    # The provider layer (apps/llm/: wrapper, retry) is shared by every call
    # and answers nothing about who asked. Name its entry frame, then the
    # first two frames OUTSIDE it: the adapter and the asker behind it (the
    # concierge's LLM-client adapter is itself called by intent resolution).
    outside = [a for a in ours if not a.startswith("apps/llm/")][:2]
    chain = ([ours[0]] if ours[0].startswith("apps/llm/") else []) + outside
    return " ← ".join(chain or ours[:1])


@contextlib.contextmanager
def forbid_provider_calls() -> Iterator[ProviderGuard]:
    """Block and record every model-provider call for the duration."""
    import importlib

    guard = ProviderGuard()
    originals: list[tuple[type, object]] = []

    def make_blocker(sdk_method: str):
        def blocked(*args: object, **kwargs: object) -> object:
            call = ProviderCall(sdk_method=sdk_method, caller=_caller_address())
            guard.calls.append(call)
            raise ProviderCallForbidden(
                f"model provider called inside a replay run: {sdk_method} from {call.caller}"
            )

        return blocked

    try:
        for module_name, class_name in BLOCKED:
            cls = getattr(importlib.import_module(module_name), class_name)
            originals.append((cls, cls.__dict__["create"]))
            cls.create = make_blocker(f"{class_name}.create")  # type: ignore[attr-defined]
        yield guard
    finally:
        for cls, original in originals:
            cls.create = original  # type: ignore[attr-defined]


__all__ = [
    "BLOCKED",
    "ProviderCall",
    "ProviderCallForbidden",
    "ProviderGuard",
    "forbid_provider_calls",
]
