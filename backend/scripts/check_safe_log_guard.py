"""NFR-2 log-safety guard: fail if raw stdlib `logging` is used anywhere in
`backend/app` outside `core/safe_log.py` (ADR-0009 §2; gap identified in
issue #102 — #89 shipped `core/safe_log.py` but never this script).

`core/safe_log.py`'s `log_event` is the only path allowed to reach the
stdlib logger, because it enforces the field allowlist (BRD §6 NFR-2 AC-5).
A raw `import logging` or `logging.<method>(...)` call anywhere else in
`backend/app` bypasses that allowlist undetected — this makes that
structurally impossible instead of review-dependent, mirroring
`check_nfr1_denylist.py`'s "greppable enforcement" pattern.

Usage (from `backend/`): `uv run python scripts/check_safe_log_guard.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

# `safe_log.py` is the allowlisted wrapper itself — it necessarily contains
# `import logging` and a raw logger call, that's the point of consolidating
# them here, not a violation.
_EXCLUDED_PATHS = {_APP_ROOT / "core" / "safe_log.py"}

_RAW_LOGGING_PATTERN = re.compile(
    r"\bimport logging\b|\blogging\.(?:debug|info|warning|error|exception|critical)\("
)


def find_violations(root: Path, excluded: set[Path] = _EXCLUDED_PATHS) -> list[str]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        if path in excluded:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _RAW_LOGGING_PATTERN.search(line):
                relative = path.relative_to(root)
                violations.append(f"{relative}:{line_number}: {line.strip()}")
    return violations


def main() -> int:
    violations = find_violations(_APP_ROOT)
    if violations:
        print("::error::Raw stdlib logging usage found outside core/safe_log.py (NFR-2 violation)")
        for violation in violations:
            print(violation)
        return 1
    print("NFR-2 log-safety guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
