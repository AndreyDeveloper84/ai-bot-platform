"""Cleanup: rewrite link labels `single-assistant-identity*` → `ayla-identity-and-brand`
where the URL has already been fixed. Preserve meta-text and migration notes."""

import re
import glob

files = glob.glob("docs/design/policies/*.md") + glob.glob("docs/design/handoffs/*.md")

# These docs intentionally reference predecessor in deprecation notes — skip line-level
SKIP_FILES = {
    "docs/design/policies/ayla-identity-and-brand.md",
    "docs/design/policies/ayla-emergency-fallback-policy.md",
    "docs/design/policies/assistant-persona.md",
}

# Don't touch lines that explicitly call out the deprecation
SKIP_LINE_PATTERNS = [
    re.compile(r"Removed deprecated"),
    re.compile(
        r"`single-assistant-identity\.md` references? (preserved|removed|deprecated)", re.IGNORECASE
    ),
    re.compile(r"Deprecated `single-assistant-identity"),
]

# Match link labels: [`single-assistant-identity...`] — replace identifier part
# Patterns:
#   [`single-assistant-identity §2.2`]
#   [`single-assistant-identity.md §2.4`]
#   [`single-assistant-identity.md`]
LINK_LABEL_RE = re.compile(r"\[`single-assistant-identity(\.md)?(\s+§[\d.]+)?`\]")


def replace_label(m):
    sect = m.group(2) or ""
    return f"[`ayla-identity-and-brand{sect}`]"


updated = []
for f in files:
    f = f.replace("\\", "/")
    if f in SKIP_FILES:
        continue
    with open(f, encoding="utf-8") as fh:
        lines = fh.readlines()
    changed = False
    new_lines = []
    for ln in lines:
        if any(p.search(ln) for p in SKIP_LINE_PATTERNS):
            new_lines.append(ln)
            continue
        nl = LINK_LABEL_RE.sub(replace_label, ln)
        if nl != ln:
            changed = True
        new_lines.append(nl)
    if changed:
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(new_lines)
        updated.append(f)

print(f"Updated {len(updated)} files:")
for f in updated:
    print(f)
