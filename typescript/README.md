# @nanorix/sdk

Sealed processing with AuditProofs.

## Installation

```bash
npm install @nanorix/sdk
```

Requires Node.js >= 18.

## Quick Start

```typescript
import { NanorixClient } from "@nanorix/sdk";
import { readFileSync } from "node:fs";

const client = new NanorixClient({ apiKey: "nrx_live_..." });

// Create a capsulefile (one-time setup)
const capsulefile = await client.capsulefiles.create({
  name: "patient-risk-scorer",
  runtime: "python3.11",
  entrypoint: "python model.py",
  files: ["model.py", "requirements.txt"],
});
await client.capsulefiles.waitUntilFinalized(capsulefile.id);

// Process data — capsule auto-destroyed after completion
const [outputs, cdp] = await client.capsules.run({
  capsulefile: capsulefile.id,
  inputs: { "patient.csv": readFileSync("patient.csv") },
});

// The proof
console.log(cdp.verifyUrl); // https://nanorix.io/verify/cap_...
```

## Workspace Mode

For multi-step workflows with full control:

```typescript
const capsule = await client.capsules.create({ capsulefile: "cf_..." });

await capsule.upload("patient.csv", data);
await capsule.execute("python preprocess.py");
await capsule.execute("python model.py");

const prediction = await capsule.download("prediction.json");
const cdp = await capsule.destroy();

// Share the proof with auditors
const verification = cdp.verification();
```

## Error Handling

```typescript
import { ExecutionError, NanorixError } from "@nanorix/sdk";

try {
  const [outputs, cdp] = await client.capsules.run({ capsulefile: "cf_...", inputs });
} catch (err) {
  if (err instanceof ExecutionError) {
    console.log(`Exit code: ${err.exitCode}`);
    console.log(`CDP still generated: ${err.cdp?.capsule_id}`);
  }
}
```

Destruction errors always carry the CDP — the proof exists even when execution fails.

## Documentation

- [Verify an AuditProof](https://nanorix.io/verify/)
- [Python SDK](https://pypi.org/project/nanorix/)
- [Verify a CDP](https://nanorix.io/verify)
