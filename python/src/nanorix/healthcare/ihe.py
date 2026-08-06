"""IHE XDS-I.b + XDR profile mapping adapter for capsule workloads.

IHE (Integrating the Healthcare Enterprise) profiles define
interoperability patterns for clinical and imaging document exchange.
This adapter covers the two profiles that dominate enterprise medical
imaging integration as of 2026:

- **XDS-I.b** (Cross-Enterprise Document Sharing for Imaging) — the
  imaging-specific extension of the XDS.b document-sharing profile.
  Used by hospital enterprise archives, regional health information
  exchanges (HIEs), and federated PACS networks to register imaging
  documents (KOS — Key Object Selection — referencing DICOM studies)
  against a Document Registry and retrieve them from a Document
  Repository. Used by Overjet (FDA-cleared dental imaging AI), Cleerly
  (cardiovascular AI), PathAI (digital pathology), and the broader
  medical-imaging AI cohort that integrates with hospital enterprise
  archives rather than only standalone DICOMweb endpoints.
- **XDR** (Cross-Enterprise Document Reliable Interchange) — point-to-
  point document push (no registry) and federated cross-community
  retrieval/query. Used by XCA-style federation gateways and direct
  EHR-to-EHR document exchange.

This adapter translates IHE ITI-NN (IT Infrastructure) and RAD-NN
(Radiology) transaction identifiers into AuditProof structural
metadata. The adapter does NOT interpret SOAP envelope clinical
content, validate against a specific IHE Implementation Guide profile
(e.g., the IHE RAD-TF Profile, the IHE XDS Patient Identity Feed
profile family), perform Document Repository conformance certification,
or classify PHI inside SOAP payloads. The adapter extracts structural
identifiers supplied by the customer (XDSSubmissionSet.uniqueId,
Document.uniqueId values, StudyInstanceUID values, homeCommunityId)
and emits a SHA-256 hash of the structural envelope (NOT pixel data
or clinical document content) for integrity anchoring.

Transactions covered:

- **ITI-41** (Provide and Register Document Set-b) — XDS-I.b and XDR
  document submission. Per IHE ITI-TF Vol. 2 §3.41.
- **ITI-43** (Retrieve Document Set) — XDS-I.b document retrieval.
  Per IHE ITI-TF Vol. 2 §3.43.
- **ITI-38** (Cross Gateway Query) — XDR federated cross-community
  query. Per IHE ITI-TF Vol. 2 §3.38.
- **ITI-39** (Cross Gateway Retrieve) — XDR federated cross-community
  retrieval. Per IHE ITI-TF Vol. 2 §3.39.
- **RAD-68** (Provide and Register Imaging Document Set-MTOM/XOP) —
  imaging-document submission with MTOM/XOP binary attachment. Per
  IHE RAD-TF Vol. 2 §4.68.
- **RAD-69** (Retrieve Imaging Document Set) — imaging-document
  retrieval. Per IHE RAD-TF Vol. 2 §4.69.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied IHE SOAP envelope identifiers and emits
    metadata fields suitable for AuditProof binding. The adapter does
    NOT perform IHE conformance certification, validate against a
    specific Implementation Guide profile, interpret pixel data inside
    referenced DICOM studies, or classify PHI inside SOAP payloads.
    IHE conformance certification, document content interpretation,
    PHI classification, and downstream consumer responsibilities
    remain the customer's. IHE Connectathon testing certifies the
    customer's gateway/repository/registry — not Nanorix.

Customer-sole-declarer (ADR-013):
    The customer supplies the parsed SOAP envelope identifiers (UIDs,
    OIDs, repository URIs). The adapter validates structural format
    only; Nanorix never infers PHI fields from XML content or asserts
    document classification.

Forever-Standard discipline (ADR-006 I0):
    Helper emits metadata via the existing
    ``Option<HashMap<String, String>>`` field (ADR-010 A4). No new
    API surface; no changes to AuditProof / Capsulefile shape.

Optional dependency: ``lxml>=4.9`` is the standard XML library for
parsing IHE SOAP/ebRIM/ebRS envelopes in production pipelines. This
adapter does NOT require lxml — the customer's pipeline parses the
SOAP envelope and supplies structural identifiers directly to this
helper. Install with ``pip install nanorix[ihe]`` if lxml support is
desired. NOTE: IHE XDS-I.b / XDR ride ebRIM/ebRS over SOAP, NOT HL7
v2 — the ``hl7apy`` dependency wired for :mod:`nanorix.healthcare.hl7_v2`
is unrelated to this helper.

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Patient identifiers,
    Document.uniqueId OIDs, StudyInstanceUID values, and home community
    IDs are obviously fictitious. NEVER real PHI in any code path even
    in fixtures.

Companion posture document: ``docs/healthcare/ihe-profile-mapping.md``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Tuple

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    MAX_VALUE_LENGTH,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
)

_FRAMEWORK_VERSION = "ihe-iti-rad-2024"
"""IHE Technical Frameworks baseline version identifier. ITI-TF + RAD-TF
revisions are published annually; the 2024 edition is the most current
as of 2026-05 and is what production deployments target."""

# Closed enum of supported IHE profiles. Mirrors the
# ``_VALID_VERB_FAMILIES`` pattern from :mod:`nanorix.healthcare.dicomweb`.
# Append-only per ADR-006 I0 (closed-enum extension follows ADR amendment).
_VALID_PROFILES: FrozenSet[str] = frozenset({"XDS-I.b", "XDR"})

# Closed enum of supported IHE transaction identifiers. Each entry maps
# to either the XDS-I.b profile, the XDR profile, or both (ITI-41 is
# reused by both XDS-I.b and XDR for document submission).
_VALID_TRANSACTION_TYPES: FrozenSet[str] = frozenset(
    {
        "ITI-41",  # Provide and Register Document Set-b (XDS-I.b + XDR)
        "ITI-43",  # Retrieve Document Set (XDS-I.b)
        "ITI-38",  # Cross Gateway Query (XDR federation)
        "ITI-39",  # Cross Gateway Retrieve (XDR federation)
        "RAD-68",  # Provide and Register Imaging Document Set-MTOM/XOP
        "RAD-69",  # Retrieve Imaging Document Set
    }
)

# Mapping of transaction type to the profile(s) that define it. Used by
# the validator to verify caller-supplied (profile, transaction_id)
# pairs are coherent per IHE TF specifications.
_TRANSACTION_PROFILES: Dict[str, FrozenSet[str]] = {
    "ITI-41": frozenset({"XDS-I.b", "XDR"}),
    "ITI-43": frozenset({"XDS-I.b"}),
    "ITI-38": frozenset({"XDR"}),
    "ITI-39": frozenset({"XDR"}),
    "RAD-68": frozenset({"XDS-I.b"}),
    "RAD-69": frozenset({"XDS-I.b"}),
}

# OID / UID pattern per ISO/IEC 9834-1: dotted-decimal ASCII digits.
# Same pattern as DICOM PS3.5 §9 (DICOM UIDs are a subset of ISO OIDs).
# Document.uniqueId values in IHE XDS metadata are OIDs (typically
# DICOM-style 1.2.840.NNNN... hierarchy or arbitrary registered OIDs);
# this validator structurally checks the dotted-decimal form. Max 64
# chars matches DICOM PS3.5 §9 and IHE ITI-TF Vol. 3 §4.2.3.1.6.
_UID_PATTERN = re.compile(r"^\d+(\.\d+)*$")


def _is_valid_uid(s: str) -> bool:
    """Check DICOM-UID / OID structural format.

    Mirrors :func:`nanorix.healthcare.dicomweb._is_valid_uid` to maintain
    consistent UID-validation semantics across the imaging adapters.

    Per DICOM PS3.5 §9 + ISO/IEC 9834-1: ASCII digits and dots, max 64
    chars, dotted-decimal hierarchy. Used for XDSSubmissionSet.uniqueId,
    Document.uniqueId, StudyInstanceUID, and homeCommunityId structural
    validation.
    """
    if not s or len(s) > 64:
        return False
    return bool(_UID_PATTERN.match(s))


class IHEBindingError(ValueError):
    """Structural anomaly during IHE profile mapping.

    Raised by :class:`IHEAdapter` when the caller supplies a transaction
    identifier that is not coherent with the declared profile, when
    required structural fields are absent, or when (in strict mode)
    UIDs/OIDs fail structural validation. Subclasses :class:`ValueError`
    so catch-all handlers picking up :class:`ValueError` keep working.

    Three-role discipline (ADR-017): this exception flags STRUCTURAL
    anomalies. It NEVER signals IHE conformance failure, IHE
    Connectathon test failure, or clinical document validation failure
    — those determinations are the customer's responsibility.
    """


@dataclass(frozen=True)
class IHETransactionType:
    """Frozen value-object wrapper for a closed-enum IHE transaction
    identifier.

    Mirrors the pattern of :class:`nanorix.healthcare.dicomweb.DICOMwebTransaction`
    using a closed-enum string rather than a true ``enum.Enum`` so that
    customer code can pass either the literal string ``"ITI-41"`` or the
    wrapper interchangeably. The wrapper carries the canonical mapping
    to its valid profiles so :class:`IHEAdapter` can validate coherence.

    Usage::

        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=envelope,
            submission_set_uid="1.2.840.113619.2.1.1.999.1",
            ...,
        )

    Construction goes through :meth:`from_identifier` which validates
    against the closed enum.
    """

    identifier: str
    """The IHE transaction identifier (e.g., ``"ITI-41"``, ``"RAD-68"``).
    Required non-empty string. Validated against the closed enum."""

    profiles: Tuple[str, ...]
    """Tuple of profile names this transaction is defined under. ``ITI-41``
    is reused by both ``"XDS-I.b"`` and ``"XDR"``; ``RAD-68``/``RAD-69``
    are XDS-I.b only; ``ITI-38``/``ITI-39`` are XDR only."""

    @classmethod
    def from_identifier(cls, identifier: str) -> "IHETransactionType":
        """Build an :class:`IHETransactionType` from a string identifier.

        :param identifier: One of the closed-enum transaction codes
            (``"ITI-41"``, ``"ITI-43"``, ``"ITI-38"``, ``"ITI-39"``,
            ``"RAD-68"``, ``"RAD-69"``).
        :returns: The wrapper instance.
        :raises IHEBindingError: If the identifier is not in the closed
            enum.
        """
        if not isinstance(identifier, str) or not identifier:
            raise IHEBindingError(
                "transaction_id required (non-empty string); "
                f"must be one of {sorted(_VALID_TRANSACTION_TYPES)}"
            )
        if identifier not in _VALID_TRANSACTION_TYPES:
            raise IHEBindingError(
                f"unsupported transaction_id '{identifier}'; must be one "
                f"of {sorted(_VALID_TRANSACTION_TYPES)}"
            )
        return cls(
            identifier=identifier,
            profiles=tuple(sorted(_TRANSACTION_PROFILES[identifier])),
        )

    @classmethod
    def all_values(cls) -> List["IHETransactionType"]:
        """Return the closed-enum list of all supported transaction
        types (sorted by identifier).

        Mirrors the ``supported_verb_families`` accessor pattern in
        :class:`nanorix.healthcare.dicomweb.DICOMwebAdapter`.
        """
        return [
            cls.from_identifier(name)
            for name in sorted(_VALID_TRANSACTION_TYPES)
        ]

    def applies_to_profile(self, profile: str) -> bool:
        """Whether this transaction is defined under the given profile.

        Naming uses ``applies_to`` per the narrow-claim discipline
        (``feedback_narrow_signed_claim_auditor_certifies.md``). Never
        ``MEETS`` / ``SATISFIES`` / ``COMPLIANT``.
        """
        return profile in self.profiles


def _validate_profile(profile: str) -> None:
    """Validate the declared profile against the closed enum."""
    if not isinstance(profile, str) or not profile:
        raise IHEBindingError(
            "profile required (non-empty string); must be one of "
            f"{sorted(_VALID_PROFILES)}"
        )
    if profile not in _VALID_PROFILES:
        raise IHEBindingError(
            f"unsupported profile '{profile}'; must be one of "
            f"{sorted(_VALID_PROFILES)}"
        )


def _ensure_iso8601_utc(timestamp: str) -> str:
    """Validate or default the transaction timestamp.

    If ``timestamp`` is empty, returns the current UTC time in ISO 8601
    format. If non-empty, returns it verbatim (the customer's pipeline
    is the canonical source of the IHE submissionTime element).
    """
    if not timestamp:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return timestamp


@dataclass(frozen=True)
class IHETransaction:
    """Structural parse result for an IHE XDS-I.b / XDR transaction.

    Produced by :meth:`IHEAdapter.parse_submission` or
    :meth:`IHEAdapter.parse_query_response`. Captures the transaction
    identifier (ITI-41 / RAD-68 / etc.), the profile (XDS-I.b or XDR),
    the structural identifiers supplied by the customer (submission
    set UID, document UIDs, study instance UIDs, home community ID),
    and a SHA-256 hash of the structural envelope (NOT pixel data) for
    integrity anchoring.

    Three-role discipline (ADR-017): Nanorix structurally parses;
    customer interprets and certifies. The ``payload_sha256`` field is
    the narrow signed claim per ``feedback_narrow_signed_claim_auditor_certifies.md``
    — it anchors the structural envelope bytes only and never asserts
    IHE conformance, RAD-TF profile pass, or HIPAA applicability.
    """

    transaction_id: str
    """The IHE transaction identifier (e.g., ``"ITI-41"``, ``"RAD-68"``).
    Required non-empty string; validated against the closed enum."""

    profile: str
    """The IHE profile (``"XDS-I.b"`` or ``"XDR"``). Required."""

    repository_uri: str
    """The endpoint URI of the Document Repository (XDS-I.b) or the
    XCA Responding Gateway (XDR). Required non-empty for ITI-41 / ITI-43
    / RAD-68 / RAD-69 / ITI-38 / ITI-39. Format: a customer-supplied URI
    string (typically https:// to a SOAP endpoint per IHE ITI-TF Vol. 2
    Appendix V on WS-I Basic Profile transport bindings)."""

    submission_set_uid: str
    """The XDSSubmissionSet.uniqueId — an OID identifying the submission
    set per IHE ITI-TF Vol. 3 §4.2.3.1.6. Required for submission
    transactions (ITI-41 / RAD-68); empty for query/retrieve responses."""

    document_uids: Tuple[str, ...]
    """Tuple of Document.uniqueId values — OIDs identifying each
    document in the submission set per IHE ITI-TF Vol. 3 §4.2.3.2.10.
    For RAD-68 imaging submissions these reference DICOM KOS objects
    (Key Object Selection) that point into specific StudyInstanceUIDs.
    May be empty for query-only transactions (ITI-38) that return
    metadata without document URIs."""

    study_instance_uids: Tuple[str, ...]
    """Tuple of StudyInstanceUID values referenced by the imaging
    documents. Populated for RAD-68 / RAD-69 transactions that submit
    or retrieve imaging document sets. Empty for non-imaging XDS
    transactions and for query-only ITI-38 transactions."""

    home_community_id: str
    """The homeCommunityId OID per IHE ITI-TF Vol. 2 §3.55. Required
    for XDR (cross-community routing requires the community OID);
    optional for XDS-I.b. Identifies which XCA community the documents
    originate from."""

    payload_sha256: str
    """SHA-256 hex hash of the structural SOAP envelope bytes. Used for
    integrity anchoring without exposing content. Hashes the envelope
    that defines the structural transaction (ebRIM/ebRS metadata over
    SOAP); does NOT hash pixel data or referenced DICOM imaging content
    — those remain inside the customer's PACS/repository and outside the
    AuditProof binding (per ADR-017 narrow-claim discipline)."""

    instance_count: int
    """Number of document entries in the submission (or query result
    set). Reflects the structural envelope; the customer's pipeline
    is responsible for verifying the count matches their downstream
    accounting."""

    transaction_timestamp: str
    """The transaction timestamp in ISO 8601 UTC format
    (``YYYY-MM-DDTHH:MM:SSZ``). Defaults to the adapter's call-time UTC
    if the customer's envelope does not carry an explicit
    XDSSubmissionSet.submissionTime element."""

    @property
    def is_submission(self) -> bool:
        """Whether this is a submission transaction (ITI-41 / RAD-68)."""
        return self.transaction_id in {"ITI-41", "RAD-68"}

    @property
    def is_retrieve(self) -> bool:
        """Whether this is a retrieve transaction (ITI-43 / RAD-69 / ITI-39)."""
        return self.transaction_id in {"ITI-43", "RAD-69", "ITI-39"}

    @property
    def is_query(self) -> bool:
        """Whether this is a query transaction (ITI-38)."""
        return self.transaction_id == "ITI-38"

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        structural fields from the IHE transaction. Suitable for
        binding into the AuditProof's ``metadata_hash`` per ADR-010 A4.

        The metadata captures structural identifiers + envelope hash
        only; clinical document content and pixel data are NOT emitted.
        Customer application code processes those inside the capsule
        workspace.

        Three-role discipline (ADR-017): structural parse only;
        Nanorix does not interpret SOAP envelope content or certify
        IHE Connectathon conformance.

        List-typed fields (``document_uids``, ``study_instance_uids``)
        are comma-joined into a single value to fit the
        ``Dict[str, str]`` schema. Customer-side pipelines should
        re-split on ``","`` to recover the original list.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "IHE",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "transaction_id": self.transaction_id,
            "profile": self.profile,
            "repository_uri": self.repository_uri,
            "payload_sha256": self.payload_sha256,
            "instance_count": str(self.instance_count),
            "transaction_timestamp": self.transaction_timestamp,
        }
        if self.submission_set_uid:
            meta["submission_set_uid"] = self.submission_set_uid
        if self.home_community_id:
            meta["home_community_id"] = self.home_community_id
        if self.document_uids:
            joined = ",".join(self.document_uids)
            if len(joined) <= MAX_VALUE_LENGTH:
                meta["document_uids"] = joined
            else:
                # Long UID chains: emit only the count to stay under the
                # wire-value limit; customer-side pipeline can recover
                # individual UIDs from their canonical submission record.
                meta["document_uids_count"] = str(len(self.document_uids))
        if self.study_instance_uids:
            joined = ",".join(self.study_instance_uids)
            if len(joined) <= MAX_VALUE_LENGTH:
                meta["study_instance_uids"] = joined
            else:
                meta["study_instance_uids_count"] = str(
                    len(self.study_instance_uids)
                )

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, "
                f"max {MAX_METADATA_KEYS}"
            )
        size = estimate_serialized_size(meta)
        if size > 4096:
            raise ValueError(
                f"to_metadata serializes to {size} bytes, max 4096"
            )
        return meta


class IHEAdapter:
    """IHE XDS-I.b + XDR profile structural adapter.

    Translates customer-supplied IHE SOAP envelope identifiers and
    transaction codes into :class:`IHETransaction` results with a
    SHA-256 envelope hash + structural metadata suitable for AuditProof
    binding.

    Covered profiles: XDS-I.b (Cross-Enterprise Document Sharing for
    Imaging), XDR (Cross-Enterprise Document Reliable Interchange).

    Covered transactions:

    - ITI-41 (Provide and Register Document Set-b) — XDS-I.b + XDR
    - ITI-43 (Retrieve Document Set) — XDS-I.b
    - ITI-38 (Cross Gateway Query) — XDR
    - ITI-39 (Cross Gateway Retrieve) — XDR
    - RAD-68 (Provide and Register Imaging Document Set-MTOM/XOP) —
      XDS-I.b
    - RAD-69 (Retrieve Imaging Document Set) — XDS-I.b

    Three-role discipline (ADR-017): Adapter is structural; IHE
    conformance certification, RAD-TF / ITI-TF Implementation Guide
    validation, IHE Connectathon test certification, document content
    interpretation, and PHI classification remain the customer's
    responsibility.

    Customer-sole-declarer (ADR-013): Customer supplies parsed SOAP
    envelope identifiers (UIDs, OIDs, repository URIs); adapter
    validates structural format only and emits structural metadata.

    Optional dependency: ``lxml>=4.9`` for full XML parsing in the
    customer's pipeline. This adapter does NOT depend on lxml — the
    customer's pipeline produces parsed identifiers; the adapter
    handles only the structural metadata emission.

    Example::

        adapter = IHEAdapter()
        # Customer pipeline parses the SOAP envelope and supplies UIDs.
        soap_envelope = read_soap_response_bytes()
        tx = adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="RAD-68",
            soap_envelope_bytes=soap_envelope,
            repository_uri="https://repository.example.org/ihe",
            submission_set_uid="1.2.840.113619.2.1.1.999.1",
            document_uids=["1.2.840.113619.2.1.1.999.1.1"],
            study_instance_uids=["1.2.840.113619.2.1.1.123"],
            home_community_id="1.3.6.1.4.1.21367.13.20.1000",
        )
        capsule = client.capsules.create(
            capsulefile="cf_imaging_workflow",
            metadata=tx.to_metadata(),
        )
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise :class:`IHEBindingError` on any
            structural anomaly (malformed UIDs/OIDs, missing
            ``repository_uri``, empty ``document_uids`` for a submission,
            transaction_id not defined under the declared profile). If
            False (default), best-effort parsing with empty-string /
            empty-tuple fallbacks for missing optional fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the adapter is operating in strict mode."""
        return self._strict

    @property
    def supported_profiles(self) -> List[str]:
        """The canonical sorted list of supported IHE profiles.

        Returns a sorted copy; mutation does not affect adapter state.
        """
        return sorted(_VALID_PROFILES)

    @property
    def supported_transactions(self) -> List[IHETransactionType]:
        """The canonical sorted list of supported IHE transaction types.

        Each entry is a :class:`IHETransactionType` value-object that
        carries the identifier + the profile(s) under which it is
        defined. Returns a fresh list per call.
        """
        return IHETransactionType.all_values()

    def parse_submission(
        self,
        profile: str,
        transaction_id: str,
        soap_envelope_bytes: bytes,
        repository_uri: str = "",
        submission_set_uid: str = "",
        document_uids: Tuple[str, ...] = (),
        study_instance_uids: Tuple[str, ...] = (),
        home_community_id: str = "",
        transaction_timestamp: str = "",
    ) -> IHETransaction:
        """Parse a customer-supplied IHE submission envelope.

        Used for ITI-41 (XDS-I.b + XDR document submission) and RAD-68
        (imaging document set submission). The customer's pipeline
        parses the SOAP envelope and supplies the structural
        identifiers; the adapter validates them, computes the envelope
        SHA-256, and emits :class:`IHETransaction`.

        :param profile: ``"XDS-I.b"`` or ``"XDR"``. Required.
        :param transaction_id: The IHE transaction identifier
            (e.g., ``"ITI-41"`` or ``"RAD-68"``). Required.
        :param soap_envelope_bytes: The structural SOAP envelope bytes
            (the ebRIM/ebRS metadata wrapped by SOAP). Required —
            empty bytes raise :class:`IHEBindingError`. Does NOT
            include pixel data or referenced DICOM imaging content.
        :param repository_uri: The Document Repository (XDS-I.b) or
            XCA gateway (XDR) endpoint URI. Required for submission
            transactions in strict mode; non-strict mode tolerates
            empty string.
        :param submission_set_uid: The XDSSubmissionSet.uniqueId OID.
            Required for submission transactions in strict mode.
        :param document_uids: Tuple of Document.uniqueId OIDs. May be
            empty for query-only transactions; required non-empty for
            submission transactions in strict mode.
        :param study_instance_uids: Tuple of StudyInstanceUID values
            referenced by imaging documents. Optional; populated
            primarily for RAD-68.
        :param home_community_id: The homeCommunityId OID. Required
            for XDR transactions in strict mode; optional for XDS-I.b.
        :param transaction_timestamp: ISO 8601 UTC timestamp; defaults
            to call-time UTC when empty.
        :returns: :class:`IHETransaction`.
        :raises IHEBindingError: On structural anomalies (see ``strict``
            docstring on :meth:`__init__`).
        """
        return self._parse_envelope(
            profile=profile,
            transaction_id=transaction_id,
            soap_envelope_bytes=soap_envelope_bytes,
            repository_uri=repository_uri,
            submission_set_uid=submission_set_uid,
            document_uids=document_uids,
            study_instance_uids=study_instance_uids,
            home_community_id=home_community_id,
            transaction_timestamp=transaction_timestamp,
            expect_submission=True,
        )

    def parse_query_response(
        self,
        profile: str,
        transaction_id: str,
        soap_envelope_bytes: bytes,
        repository_uri: str = "",
        submission_set_uid: str = "",
        document_uids: Tuple[str, ...] = (),
        study_instance_uids: Tuple[str, ...] = (),
        home_community_id: str = "",
        transaction_timestamp: str = "",
    ) -> IHETransaction:
        """Parse a customer-supplied IHE query / retrieve response
        envelope.

        Used for ITI-43 (XDS-I.b retrieve), RAD-69 (imaging retrieve),
        ITI-38 (XDR cross-gateway query), and ITI-39 (XDR cross-gateway
        retrieve). The customer's pipeline parses the SOAP envelope
        and supplies the structural identifiers; the adapter validates
        them, computes the envelope SHA-256, and emits
        :class:`IHETransaction`.

        Same parameter semantics as :meth:`parse_submission`, but
        ``submission_set_uid`` and ``document_uids`` may be empty for
        query-only transactions (ITI-38 returns metadata without
        document URIs in its response).

        :returns: :class:`IHETransaction`.
        :raises IHEBindingError: On structural anomalies.
        """
        return self._parse_envelope(
            profile=profile,
            transaction_id=transaction_id,
            soap_envelope_bytes=soap_envelope_bytes,
            repository_uri=repository_uri,
            submission_set_uid=submission_set_uid,
            document_uids=document_uids,
            study_instance_uids=study_instance_uids,
            home_community_id=home_community_id,
            transaction_timestamp=transaction_timestamp,
            expect_submission=False,
        )

    def _parse_envelope(
        self,
        *,
        profile: str,
        transaction_id: str,
        soap_envelope_bytes: bytes,
        repository_uri: str,
        submission_set_uid: str,
        document_uids: Tuple[str, ...],
        study_instance_uids: Tuple[str, ...],
        home_community_id: str,
        transaction_timestamp: str,
        expect_submission: bool,
    ) -> IHETransaction:
        """Common structural validator + envelope-hash computation."""
        _validate_profile(profile)
        tx_type = IHETransactionType.from_identifier(transaction_id)
        if not tx_type.applies_to_profile(profile):
            raise IHEBindingError(
                f"transaction_id '{transaction_id}' is not defined under "
                f"profile '{profile}'; valid profiles for this transaction "
                f"are {list(tx_type.profiles)}"
            )

        if not isinstance(soap_envelope_bytes, (bytes, bytearray)):
            raise IHEBindingError(
                "soap_envelope_bytes expects bytes (got "
                f"{type(soap_envelope_bytes).__name__})"
            )
        if not soap_envelope_bytes:
            raise IHEBindingError(
                "soap_envelope_bytes must be non-empty; the structural "
                "envelope is required for AuditProof anchoring"
            )

        # Coerce list inputs to tuple for the frozen dataclass + canonical
        # ordering. Empty inputs stay empty.
        document_uids_tup = tuple(document_uids) if document_uids else ()
        study_instance_uids_tup = (
            tuple(study_instance_uids) if study_instance_uids else ()
        )

        # Strict-mode structural validations. Submissions require a
        # non-empty repository_uri + submission_set_uid; retrieves
        # require a non-empty repository_uri.
        if self._strict:
            if not repository_uri:
                raise IHEBindingError(
                    "repository_uri required in strict mode "
                    f"(transaction_id={transaction_id})"
                )
            if expect_submission and transaction_id in {"ITI-41", "RAD-68"}:
                if not submission_set_uid:
                    raise IHEBindingError(
                        "submission_set_uid required in strict mode for "
                        f"submission transactions (transaction_id={transaction_id})"
                    )
                if not document_uids_tup:
                    raise IHEBindingError(
                        "document_uids required non-empty in strict mode "
                        f"for submission transactions (transaction_id={transaction_id})"
                    )
            if profile == "XDR" and not home_community_id:
                raise IHEBindingError(
                    "home_community_id required in strict mode for XDR "
                    "profile (cross-community routing requires the "
                    "community OID per IHE ITI-TF Vol. 2 §3.55)"
                )

        # Structural UID/OID validation. Strict mode rejects malformed
        # values; non-strict mode preserves them as supplied (customer
        # is responsible for upstream correctness — adapter is structural
        # only per ADR-017).
        if submission_set_uid and not _is_valid_uid(submission_set_uid):
            if self._strict:
                raise IHEBindingError(
                    f"submission_set_uid '{submission_set_uid}' does not "
                    "match OID structural format (ISO/IEC 9834-1: ASCII "
                    "digits and dots, max 64 chars)"
                )
        if home_community_id and not _is_valid_uid(home_community_id):
            if self._strict:
                raise IHEBindingError(
                    f"home_community_id '{home_community_id}' does not "
                    "match OID structural format"
                )
        for idx, uid in enumerate(document_uids_tup):
            if not _is_valid_uid(uid):
                if self._strict:
                    raise IHEBindingError(
                        f"document_uids[{idx}] '{uid}' does not match OID "
                        "structural format"
                    )
        for idx, uid in enumerate(study_instance_uids_tup):
            if not _is_valid_uid(uid):
                if self._strict:
                    raise IHEBindingError(
                        f"study_instance_uids[{idx}] '{uid}' does not "
                        "match DICOM UID structural format"
                    )

        envelope_hash = hashlib.sha256(bytes(soap_envelope_bytes)).hexdigest()
        instance_count = len(document_uids_tup)
        resolved_timestamp = _ensure_iso8601_utc(transaction_timestamp)

        return IHETransaction(
            transaction_id=transaction_id,
            profile=profile,
            repository_uri=repository_uri,
            submission_set_uid=submission_set_uid,
            document_uids=document_uids_tup,
            study_instance_uids=study_instance_uids_tup,
            home_community_id=home_community_id,
            payload_sha256=envelope_hash,
            instance_count=instance_count,
            transaction_timestamp=resolved_timestamp,
        )

    @staticmethod
    def is_valid_uid(s: str) -> bool:
        """Public wrapper around the OID/UID structural validator.

        OIDs and DICOM UIDs share the same dotted-decimal ASCII-digit
        structural form per ISO/IEC 9834-1 + DICOM PS3.5 §9. The
        adapter applies the validator to ``submission_set_uid``,
        ``home_community_id``, each ``document_uids`` entry, and each
        ``study_instance_uids`` entry.
        """
        return _is_valid_uid(s)


__all__ = [
    "IHEAdapter",
    "IHEBindingError",
    "IHETransaction",
    "IHETransactionType",
]
