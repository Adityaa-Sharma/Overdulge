"""NFR-1 read-only guard: fail if a mutating platform tool name appears in
first-party backend code (ADR-0009 §1). Replaces the old inline bash regex
in `.github/workflows/ci.yml` with a single documented, tested source.

Usage (from `backend/`): `uv run python scripts/check_nfr1_denylist.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.nfr1_denylist import MUTATING_TOOL_DENYLIST  # noqa: E402

# Word boundaries matter: without them, a short denylisted name would match
# as a substring of an unrelated, longer identifier in a vendored package.
_EXCLUDED_DIR_NAMES = {".venv", "venv", "site-packages", "node_modules", "__pycache__"}

# These files necessarily contain every denylisted name as a string literal
# — the definition module and the guard script that documents/tests it —
# that's the point of consolidating them here, not a violation.
# `tests/contracts/` stores real MCP tool schemas on purpose.
_EXCLUDED_PATHS = {
    _BACKEND_ROOT / "app" / "core" / "nfr1_denylist.py",
    _BACKEND_ROOT / "scripts" / "check_nfr1_denylist.py",
    _BACKEND_ROOT / "tests" / "unit" / "test_nfr1_denylist.py",
}
_EXCLUDED_PATH_PREFIX = _BACKEND_ROOT / "tests" / "contracts"


def build_denylist_pattern() -> re.Pattern[str]:
    names = "|".join(re.escape(name) for name in MUTATING_TOOL_DENYLIST)
    return re.compile(rf"\b(?:{names})\b")


def _is_scanned(path: Path) -> bool:
    if path in _EXCLUDED_PATHS:
        return False
    if _EXCLUDED_PATH_PREFIX in path.parents:
        return False
    if _EXCLUDED_DIR_NAMES & set(path.parts):
        return False
    return True


def find_violations(root: Path, pattern: re.Pattern[str]) -> list[str]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        if not _is_scanned(path):
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                relative = path.relative_to(root)
                violations.append(f"{relative}:{line_number}: {line.strip()}")
    return violations


def main() -> int:
    pattern = build_denylist_pattern()
    violations = find_violations(_BACKEND_ROOT, pattern)
    if violations:
        print("::error::Mutating platform tool reference found (NFR-1 violation)")
        for violation in violations:
            print(violation)
        return 1
    print("NFR-1 guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
