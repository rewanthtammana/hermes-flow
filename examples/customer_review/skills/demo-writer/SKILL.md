---
name: demo-writer
description: Draft a fictional customer portfolio report and hand it to a reviewer.
---

Read analysis.json and privacy.json with demo_artifact_read. Write draft.md with
aggregate counts/amounts and the masked example, labeled SYNTHETIC DEMO DATA.
Delegate exactly one reviewer. Instruct it to load demo-reviewer, read the draft
and both source artifacts, verify the figures and masking, write review.json
including approved true/false, write report.md, and call demo_audit_receipt.
Wait for its result. Never invent missing source artifacts.
