# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities privately. Do not open a public issue.

Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, or email **security@nanorix.io**.

Please include enough detail to reproduce: affected SDK and version, what you observed, and
what you expected. If a proof-of-concept is involved, a minimal one is more useful than a
complete exploit.

We will acknowledge your report and keep you updated as we investigate. If a fix affects the
verification path, the advisory will state plainly whether previously issued AuditProofs are
affected.

## Scope

This repository holds client SDKs and examples. Findings that matter most here:

- Anything that causes an SDK to accept an AuditProof it should reject, or to report a
  verification result that does not match the reference implementation
- Credential handling — API keys logged, cached, or transmitted anywhere other than the
  Nanorix API
- Data reaching a destination the caller did not declare

Vulnerabilities in the Nanorix runtime or API itself are also in scope for
**security@nanorix.io**, though they are not fixed in this repository.

## What is not a vulnerability

An AuditProof that fails verification is usually working correctly. The verifier is designed to
reject on any chain or signature discrepancy rather than degrade to a partial pass. If you
believe an authentic proof is being rejected, that *is* worth reporting — it is a
correctness bug in the verifier and we treat it as one.
