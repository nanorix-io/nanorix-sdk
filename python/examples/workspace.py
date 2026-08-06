"""
Nanorix SDK — Workspace Mode

Multi-step workflow with full control over the capsule lifecycle.
Upload files, run multiple commands, download results, then destroy.

Usage:
    export NANORIX_API_KEY="nrx_live_..."
    python workspace.py
"""
import os
import nanorix

api_key = os.environ.get("NANORIX_API_KEY", "nrx_live_YOUR_KEY_HERE")
client = nanorix.Client(api_key=api_key)

# Start a capsule from an existing capsulefile
capsule = client.capsules.create(capsulefile="cf_YOUR_CAPSULEFILE_ID")
print(f"Capsule created: {capsule.id}, status: {capsule.status}")

# Upload input data
capsule.upload("patient.csv", b"id,age,risk\n1,45,high\n2,32,low\n")
print("Data uploaded")

# Run preprocessing
capsule.execute("python preprocess.py")
print("Preprocessing complete")

# Run the model
capsule.execute("python model.py")
print("Model execution complete")

# Download results
prediction = capsule.download("prediction.json")
print(f"Prediction: {prediction.decode()}")

# List all output files
outputs = capsule.list_outputs()
for f in outputs:
    print(f"  Output: {f.filename} ({f.size_bytes} bytes)")

# Destroy — returns the AuditProof
cdp = capsule.destroy()
print(f"\nCapsule destroyed. CDP verify URL: {cdp.verify_url}")

# Share the verification CDP with auditors (zero operational details)
verification = cdp.verification()
print(f"Document hash: {verification.document_hash}")

client.close()
