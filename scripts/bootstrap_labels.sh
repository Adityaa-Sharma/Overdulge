#!/usr/bin/env bash
# Overdulge label taxonomy. Run once: bash scripts/bootstrap_labels.sh <owner/repo>
set -euo pipefail
REPO="${1:?usage: bootstrap_labels.sh <owner/repo>}"
mk() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }

# Types
mk "type:feature"        "1D9E75" "Product feature (BA-owned)"
mk "type:task"           "378ADD" "PR-sized implementation unit (SA-owned)"
mk "type:bug"            "E24B4A" "Defect (QA-owned)"
# States (each owned by exactly one agent)
mk "needs-architecture"  "534AB7" "Awaiting SA design + breakdown"
mk "ready-for-dev"       "639922" "Unblocked, awaiting Developer"
mk "ready-for-fix"       "BA7517" "Bug awaiting Developer"
mk "in-progress"         "F0997B" "Claimed by Developer (concurrency lock)"
mk "blocked"             "888780" "Waiting on blocked-by:#N"
mk "needs-review"        "AFA9EC" "Sweep re-fire marker for SA review"
mk "qa-passed"           "085041" "Verified by QA against acceptance criteria"
mk "qa-failed"           "993C1D" "Failed QA; bug(s) filed"
mk "signed-off"          "04342C" "BA final sign-off"
mk "escalation:human"    "000000" "Agents must not touch; human decision needed"
# Counters
for n in 1 2 3; do
  mk "attempt:$n"    "D3D1C7" "PR review attempt counter"
  mk "qa-attempt:$n" "D3D1C7" "QA verification attempt counter"
done
# Priority
mk "P0" "501313" "Critical - blocks sign-off"
mk "P1" "A32D2D" "Major"
mk "P2" "F09595" "Minor"
echo "Labels created on $REPO"
