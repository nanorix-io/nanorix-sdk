"""
Nanorix SDK — Quick Start

One-shot processing: send data, run computation, get results + AuditProof.
The capsule is automatically destroyed after completion.

Usage:
    export NANORIX_API_KEY="nrx_live_..."
    python quickstart.py
"""
import os
import nanorix

api_key = os.environ.get("NANORIX_API_KEY", "nrx_live_YOUR_KEY_HERE")
client = nanorix.Client(api_key=api_key)

# 1. Create a capsulefile (one-time setup)
capsulefile = client.capsulefiles.create(
    name="patient-risk-scorer",
    runtime="python3.11",
    entrypoint="python model.py",
    files={"model.py": b"import json, sys\ndata = open('/data/input/patient.csv').read()\nresult = {'score': 0.95, 'rows': len(data.splitlines())}\nwith open('/data/output/prediction.json', 'w') as f:\n    json.dump(result, f)\n"},
)
capsulefile = client.capsulefiles.wait_until_finalized(capsulefile.id)
print(f"Capsulefile finalized: {capsulefile.id}")

# 2. Process data — capsule auto-destroyed after completion
outputs, cdp = client.capsules.run(
    capsulefile=capsulefile.id,
    inputs={"patient.csv": b"id,age,risk\n1,45,high\n2,32,low\n"},
)

# 3. Results
print(f"Output files: {list(outputs.keys())}")
print(f"Prediction: {outputs['prediction.json'].decode()}")

# 4. The proof — AuditProof
print(f"CDP verify URL: {cdp.verify_url}")
full = cdp.full()
print(f"Destruction trigger: {full.destruction_trigger}")

# 5. Cleanup
client.capsulefiles.delete(capsulefile.id)
client.close()
