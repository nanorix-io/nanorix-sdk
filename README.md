# Nanorix SDKs

Client libraries and examples for [Nanorix](https://nanorix.io) — sealed ephemeral compute
that returns a verifiable **AuditProof** for every run.

You send a workload. It executes inside an isolated Linux namespace with a declared-everything
environment and default-deny egress. When it finishes, the environment is destroyed and you get
back a hash-chained, tamper-evident record of the full lifecycle — ingress, execution, egress,
destruction — signed with a non-extractable KMS-held key.

The point is the last part. Logs describe what happened; an AuditProof lets someone else
*check* what happened, without taking your word for it and without seeing your data.

## Install

```bash
pip install nanorix          # Python
npm install @nanorix/sdk     # TypeScript / JavaScript
```

A Go SDK exists and ships alongside the reference verifier release, which it depends on.

## Quickstart

```python
import nanorix

client = nanorix.Client(api_key="nrx_live_...")

with client.workspace() as ws:
    ws.upload("patient_records.csv")
    result = ws.run("python extract.py")

# The workspace is gone. The proof remains.
proof = ws.audit_proof()
print(proof.verification())   # safe to share — no filenames, no commands
```

The shareable half of the proof carries structural facts only: how many files entered, their
hashes, how long data existed, that destruction completed. The private half — the full activity
trail — stays yours.

## Verifying a proof

Anyone holding an AuditProof can check it independently. Verification recomputes the
eight-step destruction chain and validates the Ed25519 signature:

```python
from nanorix.verifier import verify

result = verify("audit_proof.json")   # also accepts a dict, bytes, or Path
print(result)
```

Verification is deliberately not a black box. The algorithm is composition of open standards —
RFC 8785 JCS canonicalization, RFC 6962 Merkle, Ed25519, SHA-512 — because a proof checked by
code nobody can read is not evidence, it is a claim. The reference verifier is being prepared
for release under Apache 2.0 as a separate repository.

## What's here

| Path | |
|---|---|
| `python/` | Python SDK — sync and async clients, batching, framework integrations |
| `typescript/` | TypeScript SDK |
| `examples/` | Runnable end-to-end examples |

Examples cover the shapes people actually build: healthcare extraction, claims processing,
legal e-discovery, federated learning, long-running and tool-using agents, LangChain
integration, high-volume per-record processing, and C2PA provenance under EU AI Act Article 50.

## Docs and support

Reference documentation lives at [nanorix.io](https://nanorix.io). Questions, bug reports, and
integration problems are welcome as issues on this repository.

## Security

Please do not open a public issue for a security vulnerability. See [SECURITY.md](SECURITY.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).

The client libraries and examples in this repository are open source. The Nanorix runtime, the
trust-chain manifest, and per-capsule signing keys are not: a verifier follows the chain to
Nanorix's published manifest, and forking a client cannot mint proofs. That boundary is an
identity boundary, not an algorithm one — the same shape as X.509 root stores or C2PA manifest
policy.
