"""Epic SMART-on-FHIR launch context → AuditProof metadata binding.

The SMART App Launch v2 specification (HL7 SMART) defines the standard
OAuth2-based authorization framework for FHIR-based clinical
applications. Epic implements the standard scope syntax including
``patient/*.read``, ``user/*.read``, ``system/*.read`` and returns
the standard launch-context fields (``patient``, ``encounter``,
``fhirUser``) in the token response.

Per industry-benchmark verification, Epic's value-add over generic
SMART App Launch v2 is NOT scope-syntax differentiation — Epic
implements the standard. The Epic-specific value lies in:

1. **App Orchard / Vendor Test approval workflow** (production
   deployment requirements + Vendor Test environment validation)
2. **Token-response shape** (``patient`` + ``encounter`` +
   ``fhirUser`` context fields)
3. **Scope-grant restrictions** (Epic-side admin determines which
   scopes are grantable per app + per organization)

This helper is therefore framed as an **Epic deployment-pattern
helper**, not an "Epic SMART scope helper." Customers integrating
with Epic deployments bind the Epic-issued launch context into
AuditProof metadata at capsule create time, providing
encounter-level + patient-level + user-level accountability evidence
in the AuditProof.

**Scope discipline (per industry-benchmark verification):**

This helper does NOT:

- Implement SMART App Launch v2 OAuth2 flow (use Epic's official
  client library or ``fhirclient`` for the OAuth dance)
- Validate token signatures (token signature validation is the
  customer's identity-provider responsibility)
- Decode encrypted JWE tokens — the helper accepts decoded token
  responses
- Verify Epic-side scope grants (Epic-side admin determines
  scope-grant restrictions per app + per organization)
- Substitute for Epic App Orchard approval — production deployment
  requires Epic-side approval

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper structurally
    captures Epic-issued launch context into AuditProof metadata. The
    helper does NOT certify Epic App Orchard approval status, validate
    OAuth2 token signatures, or interpret patient + encounter context
    semantics. Approval status, signature validation, and clinical
    interpretation remain the customer's.

Customer-sole-declarer (ADR-013):
    The customer is the sole declarer of capsule content. This helper
    structurally captures customer-supplied launch context to produce
    metadata only; Nanorix injects nothing beyond the published
    base-image spec.

Optional dependency: ``python-jose[cryptography]>=3.3`` if the
customer's pipeline needs JWT signature validation outside the helper
(the helper itself accepts decoded token responses). Install with
``pip install nanorix[epic-smart]``.

Companion posture document:
``docs/healthcare/epic-smart-on-fhir-deployment.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "smart-app-launch-v2"
"""SMART App Launch baseline version identifier. The helper targets
SMART App Launch v2 which is the dominant production version across
Epic deployments through 2026. The version string is a non-calendar
helper-baseline identifier."""

# Standard SMART App Launch v2 token-response launch-context fields.
# Per build.fhir.org/ig/HL7/smart-app-launch/scopes-and-launch-context.html
_KNOWN_CONTEXT_FIELDS = frozenset(
    {
        "patient",
        "encounter",
        "fhirUser",
        "smart_style_url",
        "need_patient_banner",
        "intent",
        "tenant",
    }
)

# Epic App Orchard deployment environments. App Orchard apps deploy
# through a staged-promotion model: Sandbox → Vendor Test → Production.
_VALID_DEPLOYMENT_ENVIRONMENTS = frozenset(
    {
        "epic_sandbox",
        "epic_vendor_test",
        "epic_production",
        "epic_local_test",  # customer-side hosted Epic test instance
        "other",
    }
)


@dataclass(frozen=True)
class EpicLaunchContextResult:
    """Structural result for an Epic SMART-on-FHIR launch context
    binding.

    Captures the Epic-issued launch-context fields (``patient``,
    ``encounter``, ``fhirUser``) from the OAuth2 token response, plus
    Epic-specific deployment context (App Orchard environment, tenant
    identifier). Suitable for AuditProof binding at capsule create
    time.

    The dataclass does NOT include the OAuth2 access token itself —
    token material is sensitive; customer-side application code holds
    the token, and only the structural launch-context identifiers are
    surfaced into AuditProof metadata.

    Three-role discipline (ADR-017): Nanorix structurally captures;
    customer's identity provider validates token signatures + scope
    grants.
    """

    epic_client_id: str
    """The Epic App Orchard-assigned client ID for this application.
    Required for application-attribution evidence."""

    deployment_environment: str
    """The Epic deployment environment. Must be one of the values in
    :attr:`deployment_environment_set`. Required.

    Use ``"epic_production"`` for Epic-hosted production deployments
    (post App Orchard approval); ``"epic_vendor_test"`` for the Epic
    Vendor Test environment (pre-production); ``"epic_sandbox"`` for
    the Epic open-developer sandbox; ``"epic_local_test"`` for a
    customer-side hosted Epic test instance."""

    patient_fhir_id: Optional[str] = None
    """The ``patient`` launch-context field — the FHIR Patient resource
    ID for the patient context of this launch. Optional; standalone
    launches without patient context omit this field."""

    encounter_fhir_id: Optional[str] = None
    """The ``encounter`` launch-context field — the FHIR Encounter
    resource ID. Optional; non-encounter-scoped launches omit this
    field."""

    fhir_user: Optional[str] = None
    """The ``fhirUser`` launch-context field — a URL-format reference
    to the FHIR resource representing the user (e.g.,
    ``Practitioner/abc123`` or ``Patient/xyz789``). Optional but
    strongly recommended for user-accountability evidence."""

    tenant: Optional[str] = None
    """The Epic-issued tenant identifier (e.g., the Epic customer
    organization identifier). Optional; appears in some Epic
    deployments."""

    intent: Optional[str] = None
    """The ``intent`` launch-context field — application-defined
    intent string. Optional; customer-app-specific."""

    scopes_granted: List[str] = field(default_factory=list)
    """The OAuth2 scopes granted in the token response (e.g.,
    ``["patient/Patient.read", "patient/Observation.read",
    "launch"]``). Helper records the granted-scope list verbatim from
    the token response."""

    @property
    def has_patient_context(self) -> bool:
        """Whether the launch has patient context (``patient_fhir_id``
        is set)."""
        return self.patient_fhir_id is not None and bool(
            self.patient_fhir_id
        )

    @property
    def has_encounter_context(self) -> bool:
        """Whether the launch has encounter context
        (``encounter_fhir_id`` is set)."""
        return self.encounter_fhir_id is not None and bool(
            self.encounter_fhir_id
        )

    @property
    def has_user_context(self) -> bool:
        """Whether the launch has user context (``fhir_user`` is
        set)."""
        return self.fhir_user is not None and bool(self.fhir_user)

    @property
    def is_production(self) -> bool:
        """Whether this launch is in Epic production environment."""
        return self.deployment_environment == "epic_production"

    @property
    def deployment_environment_set(self) -> List[str]:
        """The canonical list of supported deployment environments.

        Returns a sorted copy.
        """
        return sorted(_VALID_DEPLOYMENT_ENVIRONMENTS)

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        Epic launch-context fields. Suitable for binding into the
        AuditProof's ``metadata_hash`` per ADR-010 A4.

        Token material (access_token, id_token) is NOT emitted —
        customer-side application code holds tokens; only structural
        launch-context identifiers surface in metadata.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "EpicSMART",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "epic_client_id": self.epic_client_id,
            "deployment_environment": self.deployment_environment,
        }
        if self.patient_fhir_id is not None and self.patient_fhir_id:
            meta["patient_fhir_id"] = self.patient_fhir_id
        if (
            self.encounter_fhir_id is not None
            and self.encounter_fhir_id
        ):
            meta["encounter_fhir_id"] = self.encounter_fhir_id
        if self.fhir_user is not None and self.fhir_user:
            meta["fhir_user"] = self.fhir_user
        if self.tenant is not None and self.tenant:
            meta["tenant"] = self.tenant
        if self.intent is not None and self.intent:
            meta["intent"] = self.intent
        if self.scopes_granted:
            meta["scopes_granted"] = " ".join(self.scopes_granted)

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


class EpicLaunchContext:
    """Epic SMART-on-FHIR launch context → AuditProof metadata binder.

    Accepts an Epic-issued OAuth2 token response (already decoded;
    customer-side code performs OAuth dance + signature validation)
    and produces an :class:`EpicLaunchContextResult` with the Epic-
    issued launch-context fields suitable for AuditProof binding.

    Per industry-benchmark verification, Epic SMART scopes are
    largely at-baseline with generic SMART App Launch v2 — Epic
    implements the standard, not a custom variant. This helper's
    differentiated value is:

    1. Capturing the Epic-issued ``patient`` + ``encounter`` +
       ``fhirUser`` token-response fields
    2. Recording the Epic deployment environment (Sandbox / Vendor
       Test / Production) for App-Orchard-deployment-attribution
       evidence
    3. Capturing granted scopes for accountable-deployment evidence

    Three-role discipline (ADR-017): Helper is structural; OAuth2
    flow + signature validation remain the customer's responsibility.

    Customer-sole-declarer (ADR-013): Customer supplies the decoded
    token response; helper produces structural metadata only.

    Example::

        # Customer-side OAuth2 flow with fhirclient or similar:
        token_response = customer_oauth_client.exchange(code=code)
        # token_response: {"access_token": "...", "patient": "abc123",
        #                  "encounter": "xyz789", "fhirUser":
        #                  "Practitioner/u-456", "scope": "patient/*.read
        #                  launch", ...}

        binder = EpicLaunchContext(
            epic_client_id="acme-app-prod",
            deployment_environment="epic_production",
        )
        result = binder.from_token_response(token_response)
        capsule = client.capsules.create(
            capsulefile="cf_...",
            metadata=result.to_metadata(),
        )
    """

    def __init__(
        self,
        *,
        epic_client_id: str,
        deployment_environment: str,
    ) -> None:
        """Initialize the binder with Epic App Orchard deployment
        context.

        :param epic_client_id: The Epic App Orchard-assigned client ID
            for this application. Required non-empty.
        :param deployment_environment: The Epic deployment environment
            (e.g., ``"epic_production"``, ``"epic_vendor_test"``,
            ``"epic_sandbox"``, ``"epic_local_test"``). Required; must
            be one of the values in
            :attr:`deployment_environment_set`.
        :raises ValueError: If required arguments are missing/invalid.
        """
        if not epic_client_id:
            raise ValueError(
                "epic_client_id is required and must be non-empty"
            )
        if not deployment_environment:
            raise ValueError(
                "deployment_environment is required and must be "
                "non-empty"
            )
        if (
            deployment_environment
            not in _VALID_DEPLOYMENT_ENVIRONMENTS
        ):
            raise ValueError(
                f"deployment_environment="
                f"{deployment_environment!r} not in "
                f"{sorted(_VALID_DEPLOYMENT_ENVIRONMENTS)}"
            )
        validate_value_length("epic_client_id", epic_client_id)

        self._epic_client_id = epic_client_id
        self._deployment_environment = deployment_environment

    @property
    def epic_client_id(self) -> str:
        """The Epic App Orchard client ID."""
        return self._epic_client_id

    @property
    def deployment_environment(self) -> str:
        """The Epic deployment environment."""
        return self._deployment_environment

    @property
    def deployment_environment_set(self) -> List[str]:
        """The canonical list of supported deployment environments.

        Returns a sorted copy.
        """
        return sorted(_VALID_DEPLOYMENT_ENVIRONMENTS)

    @property
    def known_context_fields(self) -> List[str]:
        """The canonical list of SMART App Launch v2 launch-context
        token-response fields.

        Returns a sorted copy. Useful for customer introspection of
        the standard context-field taxonomy.
        """
        return sorted(_KNOWN_CONTEXT_FIELDS)

    def from_token_response(
        self, token_response: Dict[str, Any]
    ) -> EpicLaunchContextResult:
        """Build an :class:`EpicLaunchContextResult` from an Epic-issued
        OAuth2 token response.

        :param token_response: The decoded OAuth2 token response dict
            from Epic's token endpoint. Must contain at least one of
            the launch-context fields (``patient``, ``encounter``,
            ``fhirUser``); standalone launches without any context
            are accepted but produce a metadata-empty result.
        :returns: :class:`EpicLaunchContextResult` with structural
            fields.
        :raises ValueError: If the token response is not a dict or
            contains a non-string launch-context value.

        The helper does NOT inspect or store the ``access_token`` /
        ``id_token`` fields — token material is sensitive; only
        structural launch-context identifiers are extracted.
        """
        if not isinstance(token_response, dict):
            raise ValueError(
                "from_token_response: token_response must be a dict "
                f"(got {type(token_response).__name__})"
            )

        patient = token_response.get("patient")
        encounter = token_response.get("encounter")
        fhir_user = token_response.get("fhirUser")
        tenant = token_response.get("tenant")
        intent = token_response.get("intent")
        scope = token_response.get("scope", "")

        for field_name, val in [
            ("patient", patient),
            ("encounter", encounter),
            ("fhirUser", fhir_user),
            ("tenant", tenant),
            ("intent", intent),
        ]:
            if val is not None and not isinstance(val, str):
                raise ValueError(
                    f"from_token_response: token_response[{field_name!r}]"
                    f" must be a string (got {type(val).__name__})"
                )

        if not isinstance(scope, str):
            raise ValueError(
                "from_token_response: token_response['scope'] must be "
                f"a string (got {type(scope).__name__})"
            )

        scopes_granted = (
            [s for s in scope.split() if s] if scope else []
        )

        # Validate wire constraints on optional fields.
        if patient:
            validate_value_length("patient_fhir_id", patient)
        if encounter:
            validate_value_length("encounter_fhir_id", encounter)
        if fhir_user:
            validate_value_length("fhir_user", fhir_user)
        if tenant:
            validate_value_length("tenant", tenant)
        if intent:
            validate_value_length("intent", intent)
        if scopes_granted:
            joined = " ".join(scopes_granted)
            validate_value_length("scopes_granted(joined)", joined)

        return EpicLaunchContextResult(
            epic_client_id=self._epic_client_id,
            deployment_environment=self._deployment_environment,
            patient_fhir_id=patient if patient else None,
            encounter_fhir_id=encounter if encounter else None,
            fhir_user=fhir_user if fhir_user else None,
            tenant=tenant if tenant else None,
            intent=intent if intent else None,
            scopes_granted=scopes_granted,
        )

    def from_components(
        self,
        *,
        patient_fhir_id: Optional[str] = None,
        encounter_fhir_id: Optional[str] = None,
        fhir_user: Optional[str] = None,
        tenant: Optional[str] = None,
        intent: Optional[str] = None,
        scopes_granted: Optional[List[str]] = None,
    ) -> EpicLaunchContextResult:
        """Build an :class:`EpicLaunchContextResult` from individually
        supplied launch-context components.

        Useful when the customer's OAuth2 client library does not
        surface the raw token response dict, or when the customer's
        application code has already extracted launch-context fields.

        :param patient_fhir_id: The FHIR Patient resource ID.
        :param encounter_fhir_id: The FHIR Encounter resource ID.
        :param fhir_user: The ``fhirUser`` URL reference.
        :param tenant: The Epic tenant identifier.
        :param intent: The customer-app-defined intent string.
        :param scopes_granted: The list of granted OAuth2 scopes.
        :returns: :class:`EpicLaunchContextResult`.
        """
        if patient_fhir_id is not None:
            validate_value_length("patient_fhir_id", patient_fhir_id)
        if encounter_fhir_id is not None:
            validate_value_length(
                "encounter_fhir_id", encounter_fhir_id
            )
        if fhir_user is not None:
            validate_value_length("fhir_user", fhir_user)
        if tenant is not None:
            validate_value_length("tenant", tenant)
        if intent is not None:
            validate_value_length("intent", intent)

        scopes_list = list(scopes_granted) if scopes_granted else []
        for idx, sc in enumerate(scopes_list):
            if not isinstance(sc, str):
                raise ValueError(
                    f"from_components: scopes_granted[{idx}] must be "
                    f"a string (got {type(sc).__name__})"
                )
            if not sc:
                raise ValueError(
                    f"from_components: scopes_granted[{idx}] must be "
                    "non-empty"
                )
        if scopes_list:
            joined = " ".join(scopes_list)
            validate_value_length("scopes_granted(joined)", joined)

        return EpicLaunchContextResult(
            epic_client_id=self._epic_client_id,
            deployment_environment=self._deployment_environment,
            patient_fhir_id=(
                patient_fhir_id if patient_fhir_id else None
            ),
            encounter_fhir_id=(
                encounter_fhir_id if encounter_fhir_id else None
            ),
            fhir_user=fhir_user if fhir_user else None,
            tenant=tenant if tenant else None,
            intent=intent if intent else None,
            scopes_granted=scopes_list,
        )

    @staticmethod
    def deployment_environment_taxonomy() -> List[Tuple[str, str]]:
        """The canonical deployment-environment taxonomy with
        descriptions.

        Returns a list of (identifier, description) tuples.
        """
        return [
            (
                "epic_sandbox",
                "Epic open-developer sandbox (fhir.epic.com)",
            ),
            (
                "epic_vendor_test",
                "Epic Vendor Test environment (pre-production)",
            ),
            (
                "epic_production",
                "Epic production environment "
                "(post App Orchard approval)",
            ),
            (
                "epic_local_test",
                "Customer-side hosted Epic test instance",
            ),
            ("other", "Other deployment environment"),
        ]
