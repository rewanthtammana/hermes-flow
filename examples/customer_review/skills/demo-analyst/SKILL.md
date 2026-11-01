---
name: demo-analyst
description: Read synthetic SQLite records and prepare aggregate analysis.
---

Call demo_portfolio_summary and demo_customer_lookup for customer 1. Treat the
record as synthetic sensitive data. Write analysis.json with the aggregate
summary only. Delegate one privacy specialist, instruct it to load demo-privacy,
look up customer 1, redact the email/national ID/account values, and write
privacy.json. Wait for it. Return artifact names, without raw customer records.
