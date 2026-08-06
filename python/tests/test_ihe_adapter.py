"""Tests for nanorix.healthcare.IHEAdapter (XDS-I.b + XDR profile mapping).

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Submission set UIDs, Document.uniqueId OIDs, StudyInstanceUID values,
and homeCommunityId OIDs are obviously fictitious; NEVER real PHI per
Wave 18-5 / Item 36 directive.

Batch 5 Tier 3 — Overjet + Cleerly + PathAI cohort.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from nanorix.compliance._constraints import (
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
)
from nanorix.healthcare import (
    IHEAdapter,
    IHEBindingError,
    IHETransactionType,
)


# ── Synthetic fixtures — NEVER real PHI. ────────────────────────────

ITI_41_ENVELOPE = (
    b'<?xml version="1.0"?>'
    b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    b"<soap:Body>"
    b"<lcm:SubmitObjectsRequest>"
    b'<rim:RegistryObjectList xmlns:rim="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0">'
    b'<rim:RegistryPackage id="SubmissionSet01"/>'
    b'<rim:ExtrinsicObject id="Document01"/>'
    b"</rim:RegistryObjectList>"
    b"</lcm:SubmitObjectsRequest>"
    b"</soap:Body>"
    b"</soap:Envelope>"
)

RAD_68_ENVELOPE = (
    b'<?xml version="1.0"?>'
    b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    b"<soap:Body>"
    b"<lcm:SubmitObjectsRequest>"
    b'<rim:RegistryObjectList xmlns:rim="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0">'
    b'<rim:RegistryPackage id="ImagingSubmissionSet01"/>'
    b'<rim:ExtrinsicObject id="KOS01"/>'
    b'<rim:ExtrinsicObject id="KOS02"/>'
    b"</rim:RegistryObjectList>"
    b"</lcm:SubmitObjectsRequest>"
    b"</soap:Body>"
    b"</soap:Envelope>"
)

ITI_38_QUERY_RESPONSE = (
    b'<?xml version="1.0"?>'
    b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    b"<soap:Body>"
    b'<query:AdhocQueryResponse status="Success">'
    b"<rim:RegistryObjectList/>"
    b"</query:AdhocQueryResponse>"
    b"</soap:Body>"
    b"</soap:Envelope>"
)

ITI_39_RETRIEVE_RESPONSE = (
    b'<?xml version="1.0"?>'
    b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    b"<soap:Body>"
    b"<xdsb:RetrieveDocumentSetResponse>"
    b'<rs:RegistryResponse status="Success"/>'
    b"</xdsb:RetrieveDocumentSetResponse>"
    b"</soap:Body>"
    b"</soap:Envelope>"
)

# Synthetic OIDs used across tests.
SYN_SUBMISSION_SET_UID = "1.2.840.113619.2.1.1.999.1"
SYN_DOCUMENT_UID = "1.2.840.113619.2.1.1.999.1.1"
SYN_DOCUMENT_UID_2 = "1.2.840.113619.2.1.1.999.1.2"
SYN_STUDY_INSTANCE_UID = "1.2.840.113619.2.1.1.123"
SYN_STUDY_INSTANCE_UID_2 = "1.2.840.113619.2.1.1.456"
SYN_HOME_COMMUNITY_ID = "1.3.6.1.4.1.21367.13.20.1000"
SYN_REPOSITORY_URI = "https://repository.example.org/ihe"


# ── Constructor + introspection tests. ──────────────────────────────


def test_adapter_default_constructor() -> None:
    adapter = IHEAdapter()
    assert adapter.strict_mode is False


def test_adapter_strict_mode_constructor() -> None:
    adapter = IHEAdapter(strict=True)
    assert adapter.strict_mode is True


def test_supported_profiles_returns_sorted_copy() -> None:
    adapter = IHEAdapter()
    profiles = adapter.supported_profiles
    assert profiles == ["XDR", "XDS-I.b"]
    # Mutation isolation.
    profiles.append("FAKE")
    assert "FAKE" not in adapter.supported_profiles


def test_supported_transactions_returns_full_closed_enum() -> None:
    adapter = IHEAdapter()
    transactions = adapter.supported_transactions
    identifiers = sorted(t.identifier for t in transactions)
    assert identifiers == [
        "ITI-38",
        "ITI-39",
        "ITI-41",
        "ITI-43",
        "RAD-68",
        "RAD-69",
    ]


# ── XDS-I.b ITI-41 submission parsing. ──────────────────────────────


def test_xds_ib_iti41_submission_basic() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
    )
    assert tx.transaction_id == "ITI-41"
    assert tx.profile == "XDS-I.b"
    assert tx.repository_uri == SYN_REPOSITORY_URI
    assert tx.submission_set_uid == SYN_SUBMISSION_SET_UID
    assert tx.document_uids == (SYN_DOCUMENT_UID,)
    assert tx.instance_count == 1
    assert tx.is_submission is True
    assert tx.is_retrieve is False
    assert tx.is_query is False


def test_xds_ib_iti41_envelope_hash_matches_sha256() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
    )
    assert tx.payload_sha256 == hashlib.sha256(ITI_41_ENVELOPE).hexdigest()
    assert len(tx.payload_sha256) == 64  # SHA-256 hex


def test_xds_ib_iti41_multi_document_submission() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID, SYN_DOCUMENT_UID_2),
    )
    assert len(tx.document_uids) == 2
    assert tx.instance_count == 2


def test_xds_ib_iti41_strict_rejects_invalid_submission_set_uid() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="submission_set_uid"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid="not-a-valid-oid",
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_xds_ib_iti41_strict_rejects_missing_repository_uri() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="repository_uri"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri="",
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_xds_ib_iti41_strict_rejects_missing_submission_set_uid() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="submission_set_uid"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid="",
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_xds_ib_iti41_strict_rejects_empty_document_uids() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="document_uids"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(),
        )


def test_xds_ib_iti41_lenient_preserves_invalid_uid_as_supplied() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid="customer-internal-id-123",
        document_uids=(SYN_DOCUMENT_UID,),
    )
    assert tx.submission_set_uid == "customer-internal-id-123"


# ── XDS-I.b RAD-68 / RAD-69 imaging document set transactions. ──────


def test_xds_ib_rad68_imaging_submission_basic() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID,),
    )
    assert tx.transaction_id == "RAD-68"
    assert tx.profile == "XDS-I.b"
    assert tx.study_instance_uids == (SYN_STUDY_INSTANCE_UID,)
    assert tx.is_submission is True


def test_xds_ib_rad68_multi_study_imaging_submission() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID, SYN_DOCUMENT_UID_2),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID, SYN_STUDY_INSTANCE_UID_2),
    )
    assert len(tx.document_uids) == 2
    assert len(tx.study_instance_uids) == 2


def test_xds_ib_rad69_imaging_retrieve_basic() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDS-I.b",
        transaction_id="RAD-69",
        soap_envelope_bytes=ITI_39_RETRIEVE_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
        document_uids=(SYN_DOCUMENT_UID,),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID,),
    )
    assert tx.transaction_id == "RAD-69"
    assert tx.profile == "XDS-I.b"
    assert tx.is_retrieve is True
    assert tx.is_submission is False


def test_xds_ib_iti43_retrieve_response() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDS-I.b",
        transaction_id="ITI-43",
        soap_envelope_bytes=ITI_39_RETRIEVE_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
        document_uids=(SYN_DOCUMENT_UID,),
    )
    assert tx.transaction_id == "ITI-43"
    assert tx.is_retrieve is True


def test_xds_ib_rad68_strict_rejects_invalid_study_instance_uid() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="study_instance_uids"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="RAD-68",
            soap_envelope_bytes=RAD_68_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
            study_instance_uids=("not-a-valid-uid",),
        )


def test_xds_ib_rad68_rejects_iti38_under_xds_ib_profile() -> None:
    """ITI-38 is XDR-only; using it under XDS-I.b must be rejected."""
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="not defined under"):
        adapter.parse_query_response(
            profile="XDS-I.b",
            transaction_id="ITI-38",
            soap_envelope_bytes=ITI_38_QUERY_RESPONSE,
            repository_uri=SYN_REPOSITORY_URI,
        )


# ── XDR ITI-38 / ITI-39 cross-community routing. ────────────────────


def test_xdr_iti41_cross_community_submission() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDR",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    assert tx.profile == "XDR"
    assert tx.home_community_id == SYN_HOME_COMMUNITY_ID


def test_xdr_iti38_cross_gateway_query_response() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDR",
        transaction_id="ITI-38",
        soap_envelope_bytes=ITI_38_QUERY_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    assert tx.transaction_id == "ITI-38"
    assert tx.profile == "XDR"
    assert tx.is_query is True
    assert tx.is_retrieve is False
    assert tx.is_submission is False


def test_xdr_iti39_cross_gateway_retrieve_response() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDR",
        transaction_id="ITI-39",
        soap_envelope_bytes=ITI_39_RETRIEVE_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    assert tx.transaction_id == "ITI-39"
    assert tx.is_retrieve is True


def test_xdr_strict_rejects_missing_home_community_id() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="home_community_id"):
        adapter.parse_submission(
            profile="XDR",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
            home_community_id="",
        )


def test_xdr_iti43_rejected_under_xdr_profile() -> None:
    """ITI-43 is XDS-I.b only; using it under XDR must be rejected."""
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="not defined under"):
        adapter.parse_query_response(
            profile="XDR",
            transaction_id="ITI-43",
            soap_envelope_bytes=ITI_39_RETRIEVE_RESPONSE,
            repository_uri=SYN_REPOSITORY_URI,
        )


def test_xdr_strict_rejects_invalid_home_community_oid() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="home_community_id"):
        adapter.parse_submission(
            profile="XDR",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
            home_community_id="not-an-oid",
        )


# ── to_metadata envelope. ───────────────────────────────────────────


def test_to_metadata_includes_reserved_framework_keys() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
    )
    meta = tx.to_metadata()
    assert meta[RESERVED_FRAMEWORK_KEY] == "IHE"
    assert meta[RESERVED_FRAMEWORK_VERSION_KEY] == "ihe-iti-rad-2024"


def test_to_metadata_payload_sha256_is_hex_string_no_raw_bytes() -> None:
    """Payload_sha256 MUST be a hex string (no raw bytes leak)."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID,),
    )
    meta = tx.to_metadata()
    sha256 = meta["payload_sha256"]
    assert isinstance(sha256, str)
    assert re.match(r"^[0-9a-f]{64}$", sha256), sha256


def test_to_metadata_omits_empty_optional_fields() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDR",
        transaction_id="ITI-38",
        soap_envelope_bytes=ITI_38_QUERY_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
    )
    meta = tx.to_metadata()
    # Empty-optionals are omitted from the metadata dict.
    assert "submission_set_uid" not in meta
    assert "home_community_id" not in meta
    assert "document_uids" not in meta
    assert "study_instance_uids" not in meta


def test_to_metadata_no_reserved_nanorix_prefix_in_customer_fields() -> None:
    """The adapter must NOT emit any reserved nanorix:* customer-field
    keys beyond the two whitelisted ``nanorix:framework`` /
    ``nanorix:framework_version`` entries."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDR",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    meta = tx.to_metadata()
    nanorix_keys = [k for k in meta.keys() if k.startswith("nanorix:")]
    assert sorted(nanorix_keys) == [
        RESERVED_FRAMEWORK_KEY,
        RESERVED_FRAMEWORK_VERSION_KEY,
    ]


# ── Strict mode tests. ──────────────────────────────────────────────


def test_strict_mode_rejects_invalid_document_uid() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="document_uids"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=("not-an-oid",),
        )


def test_lenient_mode_preserves_invalid_document_uid_as_supplied() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=("customer-internal-doc-id",),
    )
    assert tx.document_uids == ("customer-internal-doc-id",)


def test_strict_mode_rejects_invalid_study_instance_uid_in_imaging() -> None:
    adapter = IHEAdapter(strict=True)
    with pytest.raises(IHEBindingError, match="study_instance_uids"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="RAD-68",
            soap_envelope_bytes=RAD_68_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
            study_instance_uids=("malformed-uid",),
        )


def test_lenient_mode_accepts_unconstrained_submission_set_uid_in_lenient() -> None:
    """Lenient mode is best-effort: invalid OIDs pass through."""
    adapter = IHEAdapter(strict=False)
    tx = adapter.parse_submission(
        profile="XDR",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid="customer-supplied-id",
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    assert tx.submission_set_uid == "customer-supplied-id"


# ── IHETransactionType closed enum tests. ───────────────────────────


def test_transaction_type_from_identifier_valid() -> None:
    t = IHETransactionType.from_identifier("RAD-68")
    assert t.identifier == "RAD-68"
    assert "XDS-I.b" in t.profiles


def test_transaction_type_rejects_unknown_string() -> None:
    with pytest.raises(IHEBindingError, match="unsupported transaction_id"):
        IHETransactionType.from_identifier("ITI-99")


def test_transaction_type_rejects_empty_string() -> None:
    with pytest.raises(IHEBindingError, match="transaction_id required"):
        IHETransactionType.from_identifier("")


def test_transaction_type_applies_to_profile_logic() -> None:
    t = IHETransactionType.from_identifier("ITI-41")
    # ITI-41 is reused by both XDS-I.b and XDR.
    assert t.applies_to_profile("XDS-I.b") is True
    assert t.applies_to_profile("XDR") is True
    # ITI-38 is XDR-only.
    iti38 = IHETransactionType.from_identifier("ITI-38")
    assert iti38.applies_to_profile("XDR") is True
    assert iti38.applies_to_profile("XDS-I.b") is False


# ── Edge case tests. ────────────────────────────────────────────────


def test_empty_envelope_bytes_rejected() -> None:
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="soap_envelope_bytes"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes=b"",
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_non_bytes_envelope_rejected() -> None:
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="soap_envelope_bytes"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-41",
            soap_envelope_bytes="not bytes",  # type: ignore[arg-type]
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_empty_document_uids_allowed_in_lenient_for_query() -> None:
    """ITI-38 query may legitimately return no documents; this is
    valid in lenient mode."""
    adapter = IHEAdapter()
    tx = adapter.parse_query_response(
        profile="XDR",
        transaction_id="ITI-38",
        soap_envelope_bytes=ITI_38_QUERY_RESPONSE,
        repository_uri=SYN_REPOSITORY_URI,
        document_uids=(),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    assert tx.document_uids == ()
    assert tx.instance_count == 0


def test_missing_home_community_id_allowed_in_xds_ib() -> None:
    """Home_community_id is optional for XDS-I.b (only XDR strictly requires)."""
    adapter = IHEAdapter(strict=True)
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id="",
    )
    assert tx.home_community_id == ""


def test_very_long_uid_chain_emits_count_fallback() -> None:
    """Long UID chains exceeding the 256-char value limit emit a
    count-only fallback to stay within the wire constraint."""
    adapter = IHEAdapter()
    # Build a long document_uids tuple — each UID ~25 chars; 30 UIDs
    # easily exceeds the 256-char comma-joined value cap.
    long_uids = tuple(
        f"1.2.840.113619.2.1.1.999.{i}.{i}" for i in range(30)
    )
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=long_uids,
    )
    meta = tx.to_metadata()
    # The joined value would be far over 256 chars, so the fallback
    # count-only key is emitted instead of the comma-joined chain.
    assert "document_uids_count" in meta
    assert meta["document_uids_count"] == "30"
    assert "document_uids" not in meta


# ── Integration with Capsule.create(metadata=...). ──────────────────


def test_metadata_under_4kb_limit_for_typical_submission() -> None:
    """To_metadata must serialize under the 4 KB wire-cap."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID, SYN_DOCUMENT_UID_2),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID, SYN_STUDY_INSTANCE_UID_2),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    meta = tx.to_metadata()
    assert len(json.dumps(meta)) < 4096


def test_metadata_key_count_under_16_limit() -> None:
    """To_metadata must respect MAX_METADATA_KEYS = 16."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    meta = tx.to_metadata()
    assert len(meta) <= 16


def test_metadata_contains_required_routing_keys() -> None:
    """To_metadata must always include transaction_id, profile,
    repository_uri, payload_sha256, instance_count, transaction_timestamp."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDR",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        home_community_id=SYN_HOME_COMMUNITY_ID,
    )
    meta = tx.to_metadata()
    for required_key in (
        "transaction_id",
        "profile",
        "repository_uri",
        "payload_sha256",
        "instance_count",
        "transaction_timestamp",
    ):
        assert required_key in meta, f"missing {required_key}"


def test_metadata_no_phi_leakage_in_keys_or_values() -> None:
    """Metadata must never include patient identifiers, names, DOBs,
    or other PHI fields — only structural identifiers."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="RAD-68",
        soap_envelope_bytes=RAD_68_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        study_instance_uids=(SYN_STUDY_INSTANCE_UID,),
    )
    meta = tx.to_metadata()
    forbidden_keys = {
        "patient_id", "patient_name", "patient_dob",
        "mrn", "ssn", "dob", "name", "address",
    }
    for forbidden in forbidden_keys:
        assert forbidden not in meta


# ── Dataclass frozen-invariant tests. ───────────────────────────────


def test_transaction_dataclass_frozen() -> None:
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
    )
    with pytest.raises(Exception):
        tx.profile = "XDR"  # type: ignore[misc]


def test_transaction_type_dataclass_frozen() -> None:
    t = IHETransactionType.from_identifier("ITI-41")
    with pytest.raises(Exception):
        t.identifier = "ITI-43"  # type: ignore[misc]


# ── Profile/transaction validation tests. ───────────────────────────


def test_unknown_profile_rejected() -> None:
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="unsupported profile"):
        adapter.parse_submission(
            profile="XCA",  # XCA is a separate profile, not in scope
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_unknown_transaction_id_rejected() -> None:
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="unsupported transaction_id"):
        adapter.parse_submission(
            profile="XDS-I.b",
            transaction_id="ITI-99",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_empty_profile_rejected() -> None:
    adapter = IHEAdapter()
    with pytest.raises(IHEBindingError, match="profile required"):
        adapter.parse_submission(
            profile="",
            transaction_id="ITI-41",
            soap_envelope_bytes=ITI_41_ENVELOPE,
            repository_uri=SYN_REPOSITORY_URI,
            submission_set_uid=SYN_SUBMISSION_SET_UID,
            document_uids=(SYN_DOCUMENT_UID,),
        )


def test_is_valid_uid_helper() -> None:
    assert IHEAdapter.is_valid_uid("1.2.840.113619.2.1.1.999.1")
    assert IHEAdapter.is_valid_uid("1.3.6.1.4.1.21367.13.20.1000")
    assert not IHEAdapter.is_valid_uid("")
    assert not IHEAdapter.is_valid_uid("not-an-oid")
    assert not IHEAdapter.is_valid_uid("1.2.840.")  # trailing dot
    assert not IHEAdapter.is_valid_uid(".1.2.840")  # leading dot
    assert not IHEAdapter.is_valid_uid("1." * 100)  # exceeds 64 chars


def test_transaction_timestamp_defaults_to_iso8601_utc() -> None:
    """When the customer doesn't supply transaction_timestamp, adapter
    fills in current UTC ISO 8601."""
    adapter = IHEAdapter()
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        transaction_timestamp="",
    )
    # Format: YYYY-MM-DDTHH:MM:SSZ
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        tx.transaction_timestamp,
    ), tx.transaction_timestamp


def test_transaction_timestamp_passthrough_when_supplied() -> None:
    adapter = IHEAdapter()
    custom_ts = "2026-05-27T10:30:45Z"
    tx = adapter.parse_submission(
        profile="XDS-I.b",
        transaction_id="ITI-41",
        soap_envelope_bytes=ITI_41_ENVELOPE,
        repository_uri=SYN_REPOSITORY_URI,
        submission_set_uid=SYN_SUBMISSION_SET_UID,
        document_uids=(SYN_DOCUMENT_UID,),
        transaction_timestamp=custom_ts,
    )
    assert tx.transaction_timestamp == custom_ts


# ── Module re-export sanity. ────────────────────────────────────────


def test_module_reexports_public_names() -> None:
    from nanorix import healthcare

    assert hasattr(healthcare, "IHEAdapter")
    assert hasattr(healthcare, "IHEBindingError")
    assert hasattr(healthcare, "IHETransaction")
    assert hasattr(healthcare, "IHETransactionType")


def test_iheBindingError_is_value_error_subclass() -> None:
    """Catch-all ValueError handlers should still catch IHE errors."""
    assert issubclass(IHEBindingError, ValueError)
