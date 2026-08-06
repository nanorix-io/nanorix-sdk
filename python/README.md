# Nanorix Python SDK

Sealed processing with a verifiable AuditProof for every capsule.

## Installation

```bash
pip install nanorix
```

## Quick Start

```python
import nanorix

client = nanorix.Client(api_key="nrx_live_...")

# Create a capsulefile (one-time setup)
capsulefile = client.capsulefiles.create(
    name="patient-risk-scorer",
    runtime="python3.11",
    entrypoint="python model.py",
    files=["model.py", "requirements.txt"],
)
client.capsulefiles.wait_until_finalized(capsulefile.id)

# Process data — capsule auto-destroyed after completion
outputs, cdp = client.capsules.run(
    capsulefile=capsulefile.id,
    inputs={"patient.csv": open("patient.csv", "rb")},
)

# The proof
print(cdp.verify_url)  # https://nanorix.io/verify/cap_...
```

## Workspace Mode

For multi-step workflows with full control:

```python
capsule = client.capsules.create(capsulefile="cf_...")

capsule.upload("patient.csv", data)
capsule.execute("python preprocess.py")
capsule.execute("python model.py")

prediction = capsule.download("prediction.json")
cdp = capsule.destroy()

# Share the proof with auditors
verification = cdp.verification()
```

## AuditProof Access

Every destroyed capsule produces an AuditProof covering the full lifecycle —
ingress, execution, egress, and destruction:

```python
# AuditRecord — your private audit trail
full = cdp.full()
print(full.activity)       # Complete event timeline
print(full.destruction)    # 8-step chain verification

# AuditProof — safe to share
verify = cdp.verification()
print(verify.data_ingested)   # File count and hashes only
print(verify.residency)       # How long data existed
# Zero filenames, commands, or operational details
```

## Error Handling

```python
from nanorix import ExecutionError, TimeoutError

try:
    outputs, cdp = client.capsules.run(capsulefile="cf_...", inputs=inputs)
except ExecutionError as e:
    print(f"Exit code: {e.exit_code}")
    print(f"CDP still generated: {e.cdp}")  # Destruction happened
except TimeoutError as e:
    print(f"Capsule still alive: {e.capsule.id}")
    # Decide: retry, debug, or destroy
```

## Async Support

```python
import nanorix

async with nanorix.AsyncClient(api_key="nrx_live_...") as client:
    outputs, cdp = await client.capsules.run(
        capsulefile="cf_...",
        inputs={"data.csv": data},
    )
```

## Configuration

```python
client = nanorix.Client(
    api_key="nrx_live_...",
    base_url="https://api.nanorix.io",  # default
    timeout=30.0,                        # default
    max_retries=3,                       # default
)
```

## Documentation

- [Verify an AuditProof](https://nanorix.io/verify/)
- [Dashboard](https://app.nanorix.io)
- [Verify a CDP](https://nanorix.io/verify)
