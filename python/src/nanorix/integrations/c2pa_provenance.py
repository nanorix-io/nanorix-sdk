"""
C2PA LLM-Output Provenance — sibling primitive next to AuditProof.

Under the 2026-05-28. Customers running LLM/AI inference inside
Nanorix capsules emit a C2PA-conformant signed manifest binding the
generated output content to the capsule run that produced it. The
manifest references the AuditProof by three opaque identifiers
(capsule_id, auditproof_id, verification_key_id) without modifying
the AuditProof byte-form — strict Forever-Standard
byte-equivalence preservation.

## C2PA standard

Coalition for Content Provenance and Authenticity. Open standard
(ISO 22144 / TR 24337-2). Already shipping in Microsoft Designer,
OpenAI DALL-E, Sony cameras, Adobe Photoshop, BBC editorial. Backed
by Microsoft, Adobe, BBC, OpenAI.

Reference specification: https://c2pa.org/specifications/

## EU AI Act Article 50

Effective August 2026. Mandates that providers of generative AI
systems ensure outputs are "marked in a machine-readable format"
and "detectable as artificially generated or manipulated" via
cryptographic guarantees. C2PA is the de-facto-standard interchange
format for satisfying these transparency obligations.

## Three-role discipline

Nanorix signs STRUCTURAL FACTS only:

- "Model M was used" — structural attribution.
- "Capsule Y produced this output" — structural lineage.
- "Output bytes have this SHA-512 hash" — structural integrity.
- "Generation occurred at time T" — structural timestamp.

Nanorix DOES NOT certify EU AI Act Article 50 conformance — that's
the auditor's lane. The presence of a valid manifest is RELATED_TO
Article 50 transparency obligations; it is NOT a determination that
the output (or the system that produced it) is in regulatory
conformance for any specific use case.

## Narrow signed claim discipline

Per feedback_narrow_signed_claim_auditor_certifies.md PERMANENT:
manifest assertions are structural ("what happened"), NEVER
regulatory ("X satisfies Y"). Forbidden tokens: COMPLIANT,
SATISFIED, PASSED, MEETS. Required: applies_to / references /
related_to.

## Narrowness invariant (sibling primitive, NOT receipt enrichment)

Per feedback_narrowness_is_the_moat_resist_receipt_enrichment.md
PERMANENT: AuditProof / RecordReceipt / ParentProofLink fields are
UNCHANGED by this module. The C2PA manifest is a fully external
sibling primitive with its own lifecycle. AuditProof byte-form is
explicitly affirmed unchanged.

## Customer-sole-declarer

Customer declares ALL FIVE required fields:

- model_id (string identifier; not validated against any registry)
- model_weights_hash (optional; customer provides if available)
- prompt_intent (free-text description; SHA-512 hashed before recording)
- generation_timestamp (RFC 3339; customer's clock)
- output_content_bytes (Nanorix computes SHA-512; bytes never stored)

Nanorix REFUSES to infer any of these from the capsule or the
output content. Missing required field at .sign() time -> raises.

## Usage

    from nanorix.integrations.c2pa_provenance import (
        C2PAManifestBuilder,
        verify_c2pa_manifest,
    )
    from nanorix.helpers.signer import Ed25519FileKey

    signer = Ed25519FileKey("/path/to/customer_key.pem")

    manifest = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="cap_abc123",
            auditproof_id="cdp_def456",
            verification_key_id="nrx-verify-2026-05-28T12:34:50Z-cap_abc1",
        )
        .with_model(model_id="openai/gpt-4o-2024-08-06")
        .with_prompt_intent("clinical_pa_decision_narrative_v3")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(output_bytes)
        .sign(signer)
    )

    manifest_bytes = manifest.to_canonical_bytes()

    # Third-party offline verification
    result = verify_c2pa_manifest(manifest_bytes, public_key_bytes)
    assert result.signature_valid
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nanorix._jcs import canonicalize
from nanorix.helpers.signer import EventSigner


# ── Closed enums (Forever-Standard discipline) ──

CLAIM_GENERATOR_PREFIX = "Nanorix/1.0"
"""Wire-format claim_generator prefix; customer may append their own tooling."""

MANIFEST_FORMAT = "application/json"
"""JSON manifest format (C2PA standard allows multiple format identifiers;
this SDK helper emits JSON canonical bytes; media-format-specific JUMBF
embedding is out of scope)."""

SIGNATURE_ALGORITHM = "Ed25519"
"""Closed enum; matches existing per-event signing primitive in
nanorix.helpers.signer (sdk/python/src/nanorix/helpers/signer.py).
RSA-PSS-SHA256 reserved post-V1."""

DIGITAL_SOURCE_TYPE_AI = "trainedAlgorithmicMedia"
"""C2PA standard digitalSourceType for AI-generated content.
Reference: C2PA v1.x assertion specification."""

C2PA_ACTION_CREATED = "c2pa.created"
"""C2PA standard action for newly-generated content."""


class AssertionType(str, Enum):
    """Closed enum of supported assertion labels.

    Append-only Forever-Standard; new variants arrive
    via ADR amendment. C2PA standard assertion labels + Nanorix-namespaced
    extensions for capsule + AuditProof binding.
    """

    C2PA_ACTIONS = "c2pa.actions"
    C2PA_INGREDIENT = "c2pa.ingredient"
    C2PA_TRAINING_MINING = "c2pa.training-mining"
    NANORIX_AI_GENERATED = "nanorix.ai_generated"
    NANORIX_CAPSULE_BINDING = "nanorix.capsule_binding"


VALID_ASSERTION_TYPES = frozenset(a.value for a in AssertionType)


# ── Length caps + validation ──

MAX_MODEL_ID_LEN = 512
MAX_PROMPT_INTENT_LEN = 4096
MAX_CAPSULE_ID_LEN = 64
MAX_AUDITPROOF_ID_LEN = 64
MAX_VERIFICATION_KEY_ID_LEN = 128
MAX_TIMESTAMP_LEN = 64

# Forbidden tokens — these violate brand discipline (internal
# engine/struct names must never leak) or narrow-signed-claim discipline
# (regulatory adequacy is not Nanorix's claim).
FORBIDDEN_BRAND_LEAK_TOKENS = (
    "VerificationCdp",
    "FullCdp",
    "AVE",
)

FORBIDDEN_REGULATORY_TOKENS = (
    "COMPLIANT",
    "SATISFIED",
    "PASSED",
    "MEETS",
)

# Disclaimer carried in every verification result + every manifest's
# `nanorix.ai_generated` assertion data, per
# feedback_narrow_signed_claim_auditor_certifies.md PERMANENT.
NARROW_CLAIM_DISCLAIMER = (
    "Structural attestation only. This manifest records that the "
    "named model was used in the named capsule at the named time "
    "with the named output content hash. It is RELATED_TO EU AI Act "
    "Article 50 transparency obligations and other content-provenance "
    "regulatory regimes. It is NOT a determination that the output "
    "(or the system that produced it) is in regulatory conformance "
    "for any specific use case. That determination is the "
    "responsibility of the auditor / certifying authority / regulator "
    "with jurisdiction over the specific deployment."
)


def _check_forbidden_tokens(value: str, field_name: str) -> None:
    """Raise ValueError if value contains brand-leak or regulatory tokens."""
    for token in FORBIDDEN_BRAND_LEAK_TOKENS:
        if token in value:
            raise ValueError(
                f"Field {field_name!r}: value contains forbidden brand-leak "
                f"token {token!r}. Internal struct/engine names must not "
                "appear in customer-facing C2PA manifests."
            )
    for token in FORBIDDEN_REGULATORY_TOKENS:
        if token in value:
            raise ValueError(
                f"Field {field_name!r}: value contains forbidden regulatory "
                f"token {token!r}. C2PA manifest assertions are STRUCTURAL "
                "facts only; regulatory adequacy is the auditor's "
                "determination ("
                "feedback_narrow_signed_claim_auditor_certifies.md)."
            )


def _sha512_hex(data: bytes) -> str:
    """Compute SHA-512 with sha512: prefix (wire-format convention)."""
    return "sha512:" + hashlib.sha512(data).hexdigest()


def _sha512_hex_raw(data: bytes) -> bytes:
    """Compute raw SHA-512 digest (signed by Ed25519, no prefix)."""
    return hashlib.sha512(data).digest()


def _b64(data: bytes) -> str:
    """Base64-encode with base64: prefix (wire-format convention)."""
    return "base64:" + base64.b64encode(data).decode("ascii")


def _b64_decode(value: str) -> bytes:
    """Decode a base64 wire value (strips base64: prefix if present)."""
    stripped = value[len("base64:") :] if value.startswith("base64:") else value
    return base64.b64decode(stripped)


# ── Dataclasses ──


@dataclass(frozen=True)
class C2PAClaimGeneratorInfo:
    """One entry in claim_generator_info array per C2PA spec."""

    name: str
    version: str
    icon: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "version": self.version}
        if self.icon is not None:
            out["icon"] = self.icon
        return out


@dataclass(frozen=True)
class C2PASignatureInfo:
    """Ed25519 signature material attached to a signed manifest."""

    algorithm: str  # closed: "Ed25519"
    public_key: str  # "base64:..." wire form
    signature: str  # "base64:..." wire form

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "public_key": self.public_key,
            "signature": self.signature,
        }


@dataclass
class C2PAManifest:
    """A signed C2PA manifest. Sibling primitive next to AuditProof.

    Wire form is a JSON object whose canonical bytes (RFC 8785 JCS) are
    SHA-512 hashed; that hash is signed via Ed25519. The signature is
    independently verifiable offline given the public key bytes.

    Under the 2026-05-28, this manifest references the AuditProof
    by three opaque identifiers (capsule_id, auditproof_id,
    verification_key_id) without modifying the AuditProof byte-form.
    Strict Forever-Standard byte-equivalence preservation.
    """

    claim_generator: str
    claim_generator_info: List[C2PAClaimGeneratorInfo]
    format: str
    assertions: List[Dict[str, Any]]
    claims_hash: str  # "sha512:..." wire form
    signature_info: C2PASignatureInfo

    def to_dict(self) -> Dict[str, Any]:
        """Render to wire-format dict suitable for JSON encoding."""
        return {
            "claim_generator": self.claim_generator,
            "claim_generator_info": [
                cg.to_dict() for cg in self.claim_generator_info
            ],
            "format": self.format,
            "assertions": self.assertions,
            "claims_hash": self.claims_hash,
            "signature": self.signature_info.to_dict(),
        }

    def to_canonical_bytes(self) -> bytes:
        """JCS-canonicalized bytes (RFC 8785). Stable across languages."""
        return canonicalize(self.to_dict())

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Pretty-print JSON (non-canonical; for human inspection only)."""
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class C2PAVerificationResult:
    """Structural verification result — NEVER a regulatory verdict.

    Three-role split: this result is the
    Nanorix-side STRUCTURAL determination. Whether the manifest's
    presence + content satisfies EU AI Act Article 50 (or any other
    regulatory regime) for a specific deployment is the auditor's
    determination, downstream of this result.
    """

    signature_valid: bool
    claims_hash_valid: bool
    claim_generator: str
    assertions: List[Dict[str, Any]]
    output_content_hash: Optional[str]
    model_id: Optional[str]
    model_weights_hash: Optional[str]
    capsule_id: Optional[str]
    auditproof_id: Optional[str]
    verification_key_id: Optional[str]
    prompt_intent_hash: Optional[str]
    generation_timestamp: Optional[str]
    disclaimer: str = field(default=NARROW_CLAIM_DISCLAIMER)


# ── Builder ──


class C2PAManifestBuilder:
    """Fluent builder for assembling + signing a C2PA manifest.

    Customer chains method calls; each method returns self for fluency.
    Final `.sign(signer)` validates required fields, computes the
    claims hash, signs it, and returns an immutable C2PAManifest.

    All inference is REFUSED customer-sole-declarer. The
    builder will not infer model_id, prompt_intent, generation
    timestamp, output bytes — customer must declare each explicitly.
    Missing required field at .sign() time -> raises ValueError.
    """

    def __init__(self) -> None:
        self._capsule_id: Optional[str] = None
        self._auditproof_id: Optional[str] = None
        self._verification_key_id: Optional[str] = None
        self._model_id: Optional[str] = None
        self._model_weights_hash: Optional[str] = None
        self._prompt_intent_hash: Optional[str] = None
        self._generation_timestamp: Optional[str] = None
        self._output_content_hash: Optional[str] = None
        self._extra_assertions: List[Dict[str, Any]] = []
        self._claim_generator: str = CLAIM_GENERATOR_PREFIX
        self._claim_generator_info: List[C2PAClaimGeneratorInfo] = [
            C2PAClaimGeneratorInfo(name="Nanorix", version="1.0")
        ]

    # ── Required setters ──

    def with_capsule(
        self,
        *,
        capsule_id: str,
        auditproof_id: str,
        verification_key_id: str,
    ) -> "C2PAManifestBuilder":
        """Bind manifest to a Nanorix AuditProof via three opaque ids.

        The three identifiers together are sufficient for any downstream
        verifier to fetch the AuditProof (public-no-auth) and
        independently verify both the manifest and the AuditProof.
        """
        if not capsule_id or len(capsule_id) > MAX_CAPSULE_ID_LEN:
            raise ValueError(
                f"capsule_id must be 1..{MAX_CAPSULE_ID_LEN} chars; got "
                f"{len(capsule_id)}"
            )
        if not auditproof_id or len(auditproof_id) > MAX_AUDITPROOF_ID_LEN:
            raise ValueError(
                f"auditproof_id must be 1..{MAX_AUDITPROOF_ID_LEN} chars; got "
                f"{len(auditproof_id)}"
            )
        if (
            not verification_key_id
            or len(verification_key_id) > MAX_VERIFICATION_KEY_ID_LEN
        ):
            raise ValueError(
                f"verification_key_id must be 1..{MAX_VERIFICATION_KEY_ID_LEN} "
                f"chars; got {len(verification_key_id)}"
            )
        _check_forbidden_tokens(capsule_id, "capsule_id")
        _check_forbidden_tokens(auditproof_id, "auditproof_id")
        _check_forbidden_tokens(verification_key_id, "verification_key_id")
        self._capsule_id = capsule_id
        self._auditproof_id = auditproof_id
        self._verification_key_id = verification_key_id
        return self

    def with_model(
        self,
        *,
        model_id: str,
        model_weights_hash: Optional[str] = None,
    ) -> "C2PAManifestBuilder":
        """Customer-declared model attribution.

        model_id is opaque to Nanorix (not validated against any
        registry). model_weights_hash is optional; customer may supply
        if known. Both customer-sole-declarer.
        """
        if not model_id or len(model_id) > MAX_MODEL_ID_LEN:
            raise ValueError(
                f"model_id must be 1..{MAX_MODEL_ID_LEN} chars; got "
                f"{len(model_id)}"
            )
        _check_forbidden_tokens(model_id, "model_id")
        if model_weights_hash is not None:
            if not model_weights_hash.startswith("sha512:"):
                raise ValueError(
                    "model_weights_hash must use sha512: prefix; got "
                    f"{model_weights_hash[:20]}..."
                )
            _check_forbidden_tokens(model_weights_hash, "model_weights_hash")
        self._model_id = model_id
        self._model_weights_hash = model_weights_hash
        return self

    def with_prompt_intent(self, intent_text: str) -> "C2PAManifestBuilder":
        """Record SHA-512 of customer-supplied intent text.

        Only the hash reaches the manifest; raw text never recorded
        (privacy preserve — prompts may contain PHI/PII). Customer
        decides what 'intent' means; SDK does not interpret it.
        """
        if not intent_text or len(intent_text) > MAX_PROMPT_INTENT_LEN:
            raise ValueError(
                f"prompt intent must be 1..{MAX_PROMPT_INTENT_LEN} chars; got "
                f"{len(intent_text)}"
            )
        _check_forbidden_tokens(intent_text, "prompt_intent")
        self._prompt_intent_hash = _sha512_hex(intent_text.encode("utf-8"))
        return self

    def with_generation_timestamp(
        self, timestamp: str
    ) -> "C2PAManifestBuilder":
        """RFC 3339 timestamp of generation completion (customer's clock)."""
        if not timestamp or len(timestamp) > MAX_TIMESTAMP_LEN:
            raise ValueError(
                f"generation_timestamp must be 1..{MAX_TIMESTAMP_LEN} chars; "
                f"got {len(timestamp)}"
            )
        # Loose RFC 3339 sanity (full strict parser not required at SDK layer)
        if not re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$",
            timestamp,
        ):
            raise ValueError(
                f"generation_timestamp must be RFC 3339 format; got "
                f"{timestamp!r}"
            )
        self._generation_timestamp = timestamp
        return self

    def with_output_content(
        self, content_bytes: Union[bytes, bytearray]
    ) -> "C2PAManifestBuilder":
        """Hash output bytes; bytes never stored.

        Customer captures the bytes at the point of attaching
        provenance. SDK computes SHA-512; raw bytes never enter the
        manifest. Zero-retention preserved.
        """
        if not isinstance(content_bytes, (bytes, bytearray)):
            raise TypeError(
                f"output_content must be bytes; got {type(content_bytes).__name__}"
            )
        if len(content_bytes) == 0:
            raise ValueError("output_content must be non-empty")
        self._output_content_hash = _sha512_hex(bytes(content_bytes))
        return self

    # ── Optional setters / extensibility ──

    def with_claim_generator(
        self, claim_generator: str
    ) -> "C2PAManifestBuilder":
        """Override claim_generator string (default: "Nanorix/1.0")."""
        if not claim_generator or len(claim_generator) > 256:
            raise ValueError("claim_generator must be 1..256 chars")
        _check_forbidden_tokens(claim_generator, "claim_generator")
        self._claim_generator = claim_generator
        return self

    def with_claim_generator_info(
        self, info: C2PAClaimGeneratorInfo
    ) -> "C2PAManifestBuilder":
        """Append a claim_generator_info entry (e.g., customer's tool)."""
        _check_forbidden_tokens(info.name, "claim_generator_info.name")
        _check_forbidden_tokens(info.version, "claim_generator_info.version")
        self._claim_generator_info.append(info)
        return self

    def with_assertion(
        self, assertion: Dict[str, Any]
    ) -> "C2PAManifestBuilder":
        """Append an arbitrary assertion (closed-enum label required).

        Assertion labels come from a closed enum
        (AssertionType). Open `data` payload accommodates forward
        evolution of the C2PA standard.
        """
        if not isinstance(assertion, dict):
            raise TypeError("assertion must be a dict")
        label = assertion.get("label")
        if not isinstance(label, str):
            raise ValueError("assertion missing string 'label' field")
        if label not in VALID_ASSERTION_TYPES:
            raise ValueError(
                f"assertion label {label!r} is not a recognized AssertionType; "
                f"valid values: {sorted(VALID_ASSERTION_TYPES)}"
            )
        # Forbidden-token scan on assertion data (recursive shallow)
        def _scan(node: Any, path: str) -> None:
            if isinstance(node, str):
                _check_forbidden_tokens(node, path)
            elif isinstance(node, dict):
                for k, v in node.items():
                    _scan(k, f"{path}.{k}")
                    _scan(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    _scan(v, f"{path}[{i}]")

        _scan(assertion, "assertion")
        self._extra_assertions.append(assertion)
        return self

    # ── Sign + emit ──

    def sign(self, signer: EventSigner) -> C2PAManifest:
        """Assemble + sign the manifest. Required-field check enforced here."""
        # Required-field check
        missing = []
        if self._capsule_id is None:
            missing.append("capsule_id (via with_capsule)")
        if self._auditproof_id is None:
            missing.append("auditproof_id (via with_capsule)")
        if self._verification_key_id is None:
            missing.append("verification_key_id (via with_capsule)")
        if self._model_id is None:
            missing.append("model_id (via with_model)")
        if self._prompt_intent_hash is None:
            missing.append("prompt_intent (via with_prompt_intent)")
        if self._generation_timestamp is None:
            missing.append(
                "generation_timestamp (via with_generation_timestamp)"
            )
        if self._output_content_hash is None:
            missing.append("output_content (via with_output_content)")
        if missing:
            raise ValueError(
                "Cannot sign manifest: missing required customer-declared "
                f"fields: {', '.join(missing)}. Customer-sole-"
                "declarer, all required fields must be set explicitly; "
                "Nanorix refuses to infer any of them."
            )

        if signer.algorithm_name() != SIGNATURE_ALGORITHM:
            raise ValueError(
                f"Signer algorithm must be {SIGNATURE_ALGORITHM!r}; got "
                f"{signer.algorithm_name()!r}"
            )

        # Build assertions list
        assertions: List[Dict[str, Any]] = []

        # Mandatory C2PA actions assertion (created by AI)
        assertions.append(
            {
                "label": AssertionType.C2PA_ACTIONS.value,
                "data": {
                    "actions": [
                        {
                            "action": C2PA_ACTION_CREATED,
                            "softwareAgent": CLAIM_GENERATOR_PREFIX,
                            "digitalSourceType": DIGITAL_SOURCE_TYPE_AI,
                            "when": self._generation_timestamp,
                        }
                    ]
                },
            }
        )

        # Nanorix-namespaced binding assertion
        nanorix_data: Dict[str, Any] = {
            "model_id": self._model_id,
            "capsule_id": self._capsule_id,
            "auditproof_id": self._auditproof_id,
            "verification_key_id": self._verification_key_id,
            "prompt_intent_hash": self._prompt_intent_hash,
            "generation_timestamp": self._generation_timestamp,
            "output_content_hash": self._output_content_hash,
            "disclaimer": NARROW_CLAIM_DISCLAIMER,
        }
        if self._model_weights_hash is not None:
            nanorix_data["model_weights_hash"] = self._model_weights_hash
        assertions.append(
            {
                "label": AssertionType.NANORIX_AI_GENERATED.value,
                "data": nanorix_data,
            }
        )

        # Extension assertions (customer-supplied)
        assertions.extend(self._extra_assertions)

        # Canonical claims object (excludes claims_hash + signature)
        claims_object: Dict[str, Any] = {
            "claim_generator": self._claim_generator,
            "claim_generator_info": [
                cg.to_dict() for cg in self._claim_generator_info
            ],
            "format": MANIFEST_FORMAT,
            "assertions": assertions,
        }

        canonical_bytes = canonicalize(claims_object)
        claims_hash_raw = _sha512_hex_raw(canonical_bytes)
        claims_hash_prefixed = "sha512:" + claims_hash_raw.hex()

        # Sign the raw 64-byte SHA-512 digest (NOT the ASCII-hex form)
        signature_bytes = signer.sign(claims_hash_raw)
        public_key_bytes = signer.public_key_bytes()

        signature_info = C2PASignatureInfo(
            algorithm=SIGNATURE_ALGORITHM,
            public_key=_b64(public_key_bytes),
            signature=_b64(signature_bytes),
        )

        return C2PAManifest(
            claim_generator=self._claim_generator,
            claim_generator_info=list(self._claim_generator_info),
            format=MANIFEST_FORMAT,
            assertions=assertions,
            claims_hash=claims_hash_prefixed,
            signature_info=signature_info,
        )


# ── Verification ──


def _extract_nanorix_assertion(
    assertions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find the nanorix.ai_generated assertion's data block, if present."""
    for a in assertions:
        if a.get("label") == AssertionType.NANORIX_AI_GENERATED.value:
            data = a.get("data")
            if isinstance(data, dict):
                return data
    return None


def verify_c2pa_manifest(
    manifest_bytes: Union[bytes, bytearray, str],
    public_key_bytes: bytes,
) -> C2PAVerificationResult:
    """Verify a C2PA manifest offline.

    Returns a STRUCTURAL verification result (signature_valid +
    claims_hash_valid + extracted fields). Whether the structural
    pass satisfies any regulatory regime (e.g., EU AI Act Article 50)
    is the auditor's determination — downstream of this function.

    Args:
        manifest_bytes: JSON bytes/string of the wire-format manifest.
        public_key_bytes: Raw 32-byte Ed25519 public key of the signer.

    Returns:
        C2PAVerificationResult with structural fields populated.
        signature_valid + claims_hash_valid indicate cryptographic
        verification result.
    """
    if isinstance(manifest_bytes, (bytes, bytearray)):
        manifest_str = bytes(manifest_bytes).decode("utf-8")
    else:
        manifest_str = manifest_bytes
    manifest = json.loads(manifest_str)

    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")

    claim_generator = manifest.get("claim_generator", "")
    assertions = manifest.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError("Manifest assertions must be a list")

    signature_block = manifest.get("signature", {})
    if not isinstance(signature_block, dict):
        raise ValueError("Manifest signature must be an object")

    algorithm = signature_block.get("algorithm", "")
    if algorithm != SIGNATURE_ALGORITHM:
        raise ValueError(
            f"Unsupported signature algorithm: {algorithm!r}; expected "
            f"{SIGNATURE_ALGORITHM!r}"
        )

    signature_b64 = signature_block.get("signature", "")
    public_key_wire = signature_block.get("public_key", "")
    if not signature_b64 or not public_key_wire:
        raise ValueError("Manifest signature block missing signature/public_key")

    claims_hash_stored = manifest.get("claims_hash", "")
    if not isinstance(claims_hash_stored, str):
        raise ValueError("Manifest claims_hash must be a string")

    # Recompute the canonical claims object (excludes claims_hash + signature)
    claims_object = {
        "claim_generator": manifest.get("claim_generator", ""),
        "claim_generator_info": manifest.get("claim_generator_info", []),
        "format": manifest.get("format", MANIFEST_FORMAT),
        "assertions": assertions,
    }
    canonical_bytes = canonicalize(claims_object)
    claims_hash_recomputed_raw = _sha512_hex_raw(canonical_bytes)
    claims_hash_recomputed = "sha512:" + claims_hash_recomputed_raw.hex()
    claims_hash_valid = claims_hash_recomputed == claims_hash_stored

    # Signature verification — over RAW 64-byte SHA-512 digest
    signature_bytes = _b64_decode(signature_b64)
    signature_valid = False
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes, claims_hash_recomputed_raw
        )
        signature_valid = True
    except InvalidSignature:
        signature_valid = False

    # Extract Nanorix-namespaced fields for inspection
    nx_data = _extract_nanorix_assertion(assertions) or {}

    return C2PAVerificationResult(
        signature_valid=signature_valid,
        claims_hash_valid=claims_hash_valid,
        claim_generator=claim_generator,
        assertions=assertions,
        output_content_hash=nx_data.get("output_content_hash"),
        model_id=nx_data.get("model_id"),
        model_weights_hash=nx_data.get("model_weights_hash"),
        capsule_id=nx_data.get("capsule_id"),
        auditproof_id=nx_data.get("auditproof_id"),
        verification_key_id=nx_data.get("verification_key_id"),
        prompt_intent_hash=nx_data.get("prompt_intent_hash"),
        generation_timestamp=nx_data.get("generation_timestamp"),
    )


__all__ = [
    "AssertionType",
    "C2PAClaimGeneratorInfo",
    "C2PAManifest",
    "C2PAManifestBuilder",
    "C2PASignatureInfo",
    "C2PAVerificationResult",
    "C2PA_ACTION_CREATED",
    "CLAIM_GENERATOR_PREFIX",
    "DIGITAL_SOURCE_TYPE_AI",
    "FORBIDDEN_BRAND_LEAK_TOKENS",
    "FORBIDDEN_REGULATORY_TOKENS",
    "MANIFEST_FORMAT",
    "MAX_AUDITPROOF_ID_LEN",
    "MAX_CAPSULE_ID_LEN",
    "MAX_MODEL_ID_LEN",
    "MAX_PROMPT_INTENT_LEN",
    "MAX_TIMESTAMP_LEN",
    "MAX_VERIFICATION_KEY_ID_LEN",
    "NARROW_CLAIM_DISCLAIMER",
    "SIGNATURE_ALGORITHM",
    "VALID_ASSERTION_TYPES",
    "verify_c2pa_manifest",
]
