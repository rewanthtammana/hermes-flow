---
name: demo-privacy
description: Demonstrate masking of synthetic sensitive customer identifiers.
---

Read customer 1 with demo_customer_lookup. Call demo_redact on a string containing
only its email, national ID, and bank account values. Write privacy.json with the
masked example and limitations. This tool masks only these demo patterns; it does
not provide general PII detection. Do not include names, income, or support notes
in the handoff artifact. Do not delegate.
