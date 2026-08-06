"""Legal eDiscovery TAR 2.0 / CAL reference workload — production pattern.

Pipeline shape: Continuous Active Learning loop (TAR 2.0).
  iteration 0: seed-set ingestion (humans label initial documents)
  iteration N: train classifier on accumulated labels
              → score remaining unlabeled corpus
              → identify uncertainty-band documents (top 5% near 0.5)
              → emit those as "next batch for human review"
              → checkpoint state (classifier + iteration count + labels)
  human review of uncertainty-band → labels added → next iteration
  iterate until coverage threshold (recall ≥ 0.80, etc) reached.

This bundle demonstrates the iteration *shape* + persistence
contract. The placeholder classifier is logistic regression on
TF-IDF features (scikit-learn) — production deployments substitute
fine-tuned transformer (legal-BERT, ContractBERT, or
matter-corpus-tuned models) when the legal team has labeled
corpus to support it. The CAL loop shape is identical regardless
of classifier choice.

TAR 2.0 / CAL is the production eDiscovery review pattern. One-shot
batch classification is for triage / privilege-log generation only;
court-defensible review (FRCP 26(g) good-faith defensibility, FRE 502
clawback risk reduction) requires CAL with documented seed-set,
iteration history, and uncertainty-band coverage.

Memory artifact composition
---------------------------
Cross-iteration state continuity uses the memory artifact pattern:
each iteration's capsule writes the trained classifier coefficients
+ iteration counter + accumulated labels as JSON to
/data/memory/cal_state.json. The next iteration loads it. Parent
CDP chain proves "iteration N derives from iteration N-1's state."
See long-running-agent bundle for memory artifact runtime detail.

The classifier is serialized as explicit JSON (TF-IDF vocab +
weights + LogisticRegression coefficients) rather than pickled,
because memory artifacts cross capsule boundaries and pickle is
untrusted-content-unsafe. Production deployments using transformer
classifiers serialize via safetensors or external storage refs
(e.g. pointer to a customer-KMS-encrypted blob in their object
store) — the principle (no pickle of customer-derived blobs) holds.

Court-defensibility
-------------------
The output JSON per-document carries:
  - iteration_id        — which CAL iteration produced this score
  - classifier_version  — hash of the trained classifier weights
  - score               — uncertainty-band detection
  - decision_route      — "auto_classified" | "uncertainty_band_for_review" | "seed_set_label"
  - matter_id           — matter scoping for privilege log
  - bates               — production-set Bates number

The full court-defensibility record assembles across iterations: the
seed_set + iteration history + final corpus scoring + reviewer
acknowledgments form a defensible production. The capsule's
AuditProof per iteration is one piece of that record.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
MEMORY_PATH = Path("/data/memory/cal_state.json")
UNCERTAINTY_BAND_PCT = float(os.environ.get("UNCERTAINTY_BAND_PCT", "0.05"))
COVERAGE_THRESHOLD_RECALL = float(os.environ.get("COVERAGE_THRESHOLD_RECALL", "0.80"))


# -- Document + label types ----------------------------------------------


PrivilegeLabel = Literal[
    "attorney_client", "work_product", "responsive", "non_responsive", "unlabeled"
]
DecisionRoute = Literal[
    "seed_set_label", "auto_classified", "uncertainty_band_for_review"
]


class DocumentRecord(BaseModel):
    custom_id: str  # client-side stable id (matter ID + bates number)
    text: str
    matter_id: str
    bates: str | None = None


class LabeledDocument(DocumentRecord):
    label: PrivilegeLabel  # human-applied label


class ScoredDocument(BaseModel):
    custom_id: str
    matter_id: str
    bates: str | None
    iteration_id: int
    classifier_version: str  # hash of trained classifier weights
    predicted_label: PrivilegeLabel
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)  # 1 - max_class_probability
    decision_route: DecisionRoute


# -- JSON-serializable classifier representation --------------------------


class SerializableClassifier(BaseModel):
    """Explicit JSON-serializable classifier shape.

    Stores the TF-IDF vocabulary + IDF weights + LogisticRegression
    coefficients explicitly. Avoids pickle (which is unsafe to
    deserialize across capsule boundaries via memory artifact).

    Production substitutions:
      - sentence-transformer + LR head: serialize embedding model
        reference (HF model id + revision) + LR coefficients
      - fine-tuned transformer: store via safetensors + reference
        the customer-KMS-encrypted blob in their object store
        from this state shape

    The principle: no pickle of customer-derived blobs through
    memory artifact mounts. Memory artifacts cross capsule
    boundaries and arrive as untrusted-content into the next
    capsule's volatile memory.
    """

    classifier_kind: Literal["tfidf_logistic_regression"] = "tfidf_logistic_regression"
    # TF-IDF state
    vocabulary: dict[str, int]  # token → index
    idf_values: list[float]  # parallel to feature index
    ngram_min: int
    ngram_max: int
    # LogisticRegression state
    classes: list[str]
    coefficients: list[list[float]]  # [n_classes, n_features]
    intercepts: list[float]


# -- CAL state (persisted across iterations) -----------------------------


class CalState(BaseModel):
    """Cross-iteration CAL state (JSON-serializable).

    Persists via memory artifact pattern. Each iteration's capsule
    loads this from /data/memory/cal_state.json, runs one round, and
    writes back. Parent CDP chain proves continuity.
    """

    iteration_id: int = 0
    matter_id: str
    seed_set_count: int = 0
    accumulated_label_count: int = 0
    label_distribution: dict[str, int] = Field(default_factory=dict)
    classifier_version: str = ""  # SHA-256 of classifier JSON
    classifier: SerializableClassifier | None = None
    coverage_recall_estimate: float = 0.0
    iteration_history: list[dict[str, Any]] = Field(default_factory=list)


# -- Memory artifact load / persist --------------------------------------


def load_state(matter_id: str) -> CalState:
    """Load CAL state from memory artifact mount; init fresh if absent.

    Memory artifact pattern: customer's KMS-encrypted state blob is
    mounted at /data/memory/ at capsule start IF the capsule was
    created with `parent_cdp_id` referencing a prior capsule. The
    blob never decrypts on Nanorix infrastructure; KMS roundtrip
    happens inside the capsule's volatile memory.
    """
    if not MEMORY_PATH.exists():
        print(f"  [memory] No prior state at {MEMORY_PATH}; iteration 0 fresh start")
        return CalState(matter_id=matter_id)
    try:
        state = CalState.model_validate_json(MEMORY_PATH.read_text())
        print(
            f"  [memory] Loaded state: iteration={state.iteration_id} "
            f"labels={state.accumulated_label_count} "
            f"recall_est={state.coverage_recall_estimate:.2f}"
        )
        return state
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  [memory] Failed to load state ({exc}); starting fresh", file=sys.stderr)
        return CalState(matter_id=matter_id)


def persist_state(state: CalState) -> None:
    """Persist CAL state for next iteration's capsule.

    On capsule destroy, the file is encrypted using the customer's
    KMS key and stored as the new memory artifact. The next capsule
    the customer creates with `parent_cdp_id` referencing this
    destruction's AuditProof gets the decrypted state at capsule
    start.
    """
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(state.model_dump_json(indent=2))
    print(
        f"  [memory] Persisted state: iteration={state.iteration_id} "
        f"labels={state.accumulated_label_count}"
    )


# -- Placeholder classifier (TF-IDF + LogisticRegression) ----------------


def _import_sklearn():
    """Import sklearn lazily so the bundle still imports + runs in
    seed-only / uncertainty-only modes when sklearn isn't installed.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        return TfidfVectorizer, LogisticRegression, Pipeline
    except ImportError:
        return None, None, None


def train_classifier(
    labeled_docs: list[LabeledDocument],
) -> tuple[Any, SerializableClassifier, str]:
    """Train placeholder classifier on accumulated labels.

    Returns (in-memory classifier, JSON-serializable form, version_hash).

    The hash is SHA-256 of the JSON serialization, providing a stable
    classifier_version identifier that flows into per-document
    output for cross-iteration reproducibility.

    Production deployments substitute this with fine-tuned
    transformer training (legal-BERT, ContractBERT, or matter-
    corpus-tuned). The interface — labeled_docs in, (classifier,
    serialized_form, version) out — is identical.
    """
    TfidfVectorizer, LogisticRegression, Pipeline = _import_sklearn()
    if TfidfVectorizer is None:
        raise RuntimeError(
            "scikit-learn not installed; this iteration cannot train. "
            "Install via capsulefile.yaml packages or substitute a "
            "production transformer classifier."
        )

    if len(labeled_docs) < 10:
        raise RuntimeError(
            f"Insufficient labels ({len(labeled_docs)}) — need ≥10 for "
            "first classifier train. Add seed-set labels."
        )

    texts = [d.text for d in labeled_docs]
    labels = [d.label for d in labeled_docs]

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(max_features=10_000, ngram_range=(1, 2))),
        ("clf", LogisticRegression(max_iter=1000, multi_class="auto")),
    ])
    pipeline.fit(texts, labels)

    # Serialize to explicit JSON shape — no pickle.
    tfidf = pipeline.named_steps["tfidf"]
    clf = pipeline.named_steps["clf"]

    serialized = SerializableClassifier(
        vocabulary={k: int(v) for k, v in tfidf.vocabulary_.items()},
        idf_values=[float(v) for v in tfidf.idf_],
        ngram_min=tfidf.ngram_range[0],
        ngram_max=tfidf.ngram_range[1],
        classes=[str(c) for c in clf.classes_],
        coefficients=[[float(v) for v in row] for row in clf.coef_],
        intercepts=[float(v) for v in clf.intercept_],
    )

    version_payload = serialized.model_dump_json(indent=None).encode("utf-8")
    version = "sha256:" + hashlib.sha256(version_payload).hexdigest()
    return pipeline, serialized, version


def score_corpus(
    classifier: Any, unlabeled_docs: list[DocumentRecord]
) -> list[tuple[DocumentRecord, str, float]]:
    """Score remaining unlabeled corpus using in-memory classifier.

    Returns list of (document, predicted_label, max_class_probability).
    Uncertainty is derived in caller from max_class_probability — the
    closer to 1/N (uniform across N classes), the higher the
    uncertainty.
    """
    if not unlabeled_docs:
        return []
    texts = [d.text for d in unlabeled_docs]
    predictions = classifier.predict(texts)
    probabilities = classifier.predict_proba(texts)
    return [
        (doc, str(pred), float(max(probs)))
        for doc, pred, probs in zip(unlabeled_docs, predictions, probabilities)
    ]


# -- Iteration execution -------------------------------------------------


def run_iteration(
    state: CalState,
    seed_set: list[LabeledDocument],
    new_labels_this_iter: list[LabeledDocument],
    unlabeled_corpus: list[DocumentRecord],
) -> tuple[CalState, list[ScoredDocument]]:
    """Run one CAL iteration.

    Inputs:
      - state: cross-iteration state from memory artifact
      - seed_set: initial human-labeled set (only present iteration 0)
      - new_labels_this_iter: human labels applied since last iteration
        (uncertainty-band documents from prior iter that were reviewed)
      - unlabeled_corpus: remaining unlabeled documents to score

    Outputs:
      - updated state (iteration_id += 1, classifier retrained, etc)
      - list of scored documents — auto-classified + uncertainty-band

    Iteration 0 special case: seed_set carries the bootstrap labels.
    Iteration N>0: seed_set is empty; new_labels_this_iter carries
    the reviewer feedback.
    """
    state.iteration_id += 1

    if state.iteration_id == 1:
        accumulated = list(seed_set)
        state.seed_set_count = len(seed_set)
        state.accumulated_label_count = len(accumulated)
    else:
        # Subsequent iteration: in this reference, the customer
        # ships labeled corpus alongside each iteration's input
        # (new_labels.jsonl carries the reviewer-applied labels for
        # the prior iteration's uncertainty band). Production
        # implementations might persist all prior labels in the
        # state itself — kept simple here.
        accumulated = list(new_labels_this_iter)
        state.accumulated_label_count += len(accumulated)

    # Distribution snapshot
    label_dist: dict[str, int] = {}
    for d in accumulated:
        label_dist[d.label] = label_dist.get(d.label, 0) + 1
    state.label_distribution = label_dist

    # Train classifier on accumulated labels
    classifier, serialized, version = train_classifier(accumulated)
    state.classifier_version = version
    state.classifier = serialized

    # Score remaining unlabeled corpus
    raw_scores = score_corpus(classifier, unlabeled_corpus)

    # Uncertainty = 1 - max_class_probability
    # Documents in uncertainty band: top UNCERTAINTY_BAND_PCT by uncertainty
    scored: list[ScoredDocument] = []
    if raw_scores:
        scored_with_uncertainty = [
            (doc, label, 1.0 - max_prob, max_prob)
            for doc, label, max_prob in raw_scores
        ]
        # Sort descending by uncertainty (highest uncertainty first)
        scored_with_uncertainty.sort(key=lambda t: t[2], reverse=True)
        band_count = max(1, int(len(scored_with_uncertainty) * UNCERTAINTY_BAND_PCT))
        for idx, (doc, label, uncertainty, max_prob) in enumerate(scored_with_uncertainty):
            decision_route: DecisionRoute = (
                "uncertainty_band_for_review" if idx < band_count else "auto_classified"
            )
            scored.append(ScoredDocument(
                custom_id=doc.custom_id,
                matter_id=doc.matter_id,
                bates=doc.bates,
                iteration_id=state.iteration_id,
                classifier_version=state.classifier_version,
                predicted_label=label,  # type: ignore[arg-type]
                confidence=max_prob,
                uncertainty=uncertainty,
                decision_route=decision_route,
            ))

    # Add seed-set + new-label entries to output (decision_route="seed_set_label")
    label_records = seed_set if state.iteration_id == 1 else new_labels_this_iter
    for d in label_records:
        scored.append(ScoredDocument(
            custom_id=d.custom_id,
            matter_id=d.matter_id,
            bates=d.bates,
            iteration_id=state.iteration_id,
            classifier_version=state.classifier_version,
            predicted_label=d.label,
            confidence=1.0,  # human label = full confidence
            uncertainty=0.0,
            decision_route="seed_set_label",
        ))

    # Coverage estimate: simple heuristic — fraction of corpus with
    # auto_classified > 0.85 confidence. Production substitutes a
    # proper recall estimate against a control set.
    auto_count = sum(1 for s in scored if s.decision_route == "auto_classified" and s.confidence >= 0.85)
    total_corpus = len(unlabeled_corpus) + len(accumulated)
    state.coverage_recall_estimate = (
        auto_count / total_corpus if total_corpus > 0 else 0.0
    )

    state.iteration_history.append({
        "iteration_id": state.iteration_id,
        "labels_added_this_iter": len(label_records),
        "accumulated_labels": state.accumulated_label_count,
        "classifier_version": state.classifier_version,
        "uncertainty_band_count": sum(1 for s in scored if s.decision_route == "uncertainty_band_for_review"),
        "auto_classified_count": sum(1 for s in scored if s.decision_route == "auto_classified"),
        "coverage_recall_estimate": state.coverage_recall_estimate,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    })

    return state, scored


# -- I/O ----------------------------------------------------------------


def load_seed_set() -> list[LabeledDocument]:
    p = INPUT_DIR / "seed_set.jsonl"
    if not p.exists():
        return []
    return [
        LabeledDocument.model_validate_json(line)
        for line in p.read_text().splitlines()
        if line.strip()
    ]


def load_new_labels() -> list[LabeledDocument]:
    p = INPUT_DIR / "new_labels.jsonl"
    if not p.exists():
        return []
    return [
        LabeledDocument.model_validate_json(line)
        for line in p.read_text().splitlines()
        if line.strip()
    ]


def load_unlabeled_corpus() -> list[DocumentRecord]:
    p = INPUT_DIR / "corpus.jsonl"
    if not p.exists():
        return []
    return [
        DocumentRecord.model_validate_json(line)
        for line in p.read_text().splitlines()
        if line.strip()
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    seed_set = load_seed_set()
    new_labels = load_new_labels()
    unlabeled = load_unlabeled_corpus()

    if not seed_set and not new_labels:
        print(
            "No seed_set.jsonl or new_labels.jsonl in /data/input/uploads. "
            "Iteration 0 requires seed_set.jsonl; iteration N>0 requires "
            "new_labels.jsonl carrying reviewer-applied labels for the "
            "prior iteration's uncertainty band.",
            file=sys.stderr,
        )
        return

    # Resolve matter_id from seed_set or new_labels first record
    matter_id = (seed_set[0].matter_id if seed_set else new_labels[0].matter_id)

    state = load_state(matter_id)

    print(f"Running CAL iteration on matter_id={matter_id}")
    print(f"  seed_set: {len(seed_set)} docs")
    print(f"  new_labels: {len(new_labels)} docs")
    print(f"  unlabeled corpus: {len(unlabeled)} docs")

    state, scored = run_iteration(state, seed_set, new_labels, unlabeled)

    # Output: per-document scoring + iteration summary
    output_path = OUTPUT_DIR / "scored.jsonl"
    output_path.write_text(
        "\n".join(s.model_dump_json() for s in scored)
    )

    summary_path = OUTPUT_DIR / "iteration_summary.json"
    summary_path.write_text(json.dumps({
        "matter_id": matter_id,
        "iteration_id": state.iteration_id,
        "classifier_version": state.classifier_version,
        "accumulated_label_count": state.accumulated_label_count,
        "label_distribution": state.label_distribution,
        "uncertainty_band_count": sum(
            1 for s in scored if s.decision_route == "uncertainty_band_for_review"
        ),
        "auto_classified_count": sum(
            1 for s in scored if s.decision_route == "auto_classified"
        ),
        "seed_set_label_count": sum(
            1 for s in scored if s.decision_route == "seed_set_label"
        ),
        "coverage_recall_estimate": state.coverage_recall_estimate,
        "coverage_threshold_met": state.coverage_recall_estimate >= COVERAGE_THRESHOLD_RECALL,
        "iteration_history": state.iteration_history,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    print(
        f"Iteration {state.iteration_id} complete: "
        f"classifier={state.classifier_version[:24]}... "
        f"uncertainty_band={sum(1 for s in scored if s.decision_route == 'uncertainty_band_for_review')} "
        f"auto={sum(1 for s in scored if s.decision_route == 'auto_classified')} "
        f"coverage_recall_est={state.coverage_recall_estimate:.2f} "
        f"threshold_met={state.coverage_recall_estimate >= COVERAGE_THRESHOLD_RECALL}"
    )

    # Persist state for next iteration's capsule
    persist_state(state)


if __name__ == "__main__":
    main()
