# C2PA provenance for LLM output — with destruction evidence attached

One runnable flow, fully offline:

1. An AI-generated text (`data/llm_output.txt`) is bound to a **real capsule
   run** — the sibling wedge demo's capsule, whose memory and key material were
   destroyed at the end of that run — via a signed
   [C2PA](https://c2pa.org/specifications/)-style manifest.
2. The manifest is verified offline: Ed25519 signature, claims hash, and the
   three-way binding (capsule id, AuditProof reference, verification key id).
3. The referenced **AuditProof** — the destruction evidence itself — is then
   verified from public inputs only, by an independent offline verifier that
   re-implements the hashing, Merkle, and signature checks and runs
   adversarial tamper controls against the proof.

```
./run_demo.sh
```

Expected: five ✓ binding checks in stage 1, `VERDICT: PASS` in stage 2,
exit code 0. Outputs land in `out/` (signed manifest + the demo signing key).

## Why this pairing matters

EU AI Act **Article 50** (transparency obligations, applicable from
**2026-08-02**) requires providers of generative AI systems to ensure outputs
are marked in a machine-readable format and detectable as artificially
generated or manipulated. C2PA is the de-facto interchange format for such
markings.

A C2PA manifest answers *"where did this content come from?"* An AuditProof
answers *"what happened to the data and the workspace that produced it?"* The
two are sibling primitives:

| Artifact | Attests | Verified by |
|---|---|---|
| C2PA manifest | model id, prompt-intent hash, output hash, generation time, capsule binding | `verify_c2pa_manifest` (SDK, offline) |
| AuditProof | the capsule's 8-step destruction chain, signed | any independent verifier, offline |

Everything signed here is **structural** — what happened, cryptographically.
Whether that evidence answers Article 50 (or any regulatory regime) for a
specific deployment is the determination of the auditor or authority with
jurisdiction over it; the manifest's own disclaimer says exactly that. The
mapping is a citation (`related_to`), never a conformance claim.

## Customer-sole-declarer

The SDK refuses to infer any provenance field. Model id, prompt intent,
generation timestamp, and the output bytes are all declared by the caller
(this demo declares them in `run_demo.py`). Prompts are never embedded —
only the hash of a declared *intent description*.

## The live-API version of this flow

This demo binds to a recorded capsule run so it works with no account and no
network. The production flow — create capsule, run inference, destroy, then
bind `cdp.cdp_id` from `capsule.destroy()` — is documented with code in
[`docs/integrations/c2pa-provenance.md`](../../docs/integrations/c2pa-provenance.md).

## Known limits (stated, not hidden)

Inherited from the recorded run it references: the capsule signing key is
self-published (the trust-chain manifest that binds signing keys to a named
party is a separate, in-progress surface), and the dataset is synthetic. The
demo signing key generated in `out/` represents the *customer's* key; Nanorix
never holds it.
