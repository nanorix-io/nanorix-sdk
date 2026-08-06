#!/usr/bin/env bash
# C2PA Art-50 provenance demo — full offline flow:
#   1. sign + verify a C2PA manifest binding LLM output to a real capsule run
#   2. independently verify that capsule run's AuditProof from public inputs
set -euo pipefail
cd "$(dirname "$0")"

echo "### stage 1/2 — C2PA manifest: sign, verify, cross-check binding"
python3 run_demo.py

echo
echo "### stage 2/2 — the referenced AuditProof, verified from public inputs only"
(cd ../g10-wedge-demo && python3 tools/verify_auditproof.py \
  --proof out/auditproof.json --pubkey out/capsule_pubkey.json) | tail -20
