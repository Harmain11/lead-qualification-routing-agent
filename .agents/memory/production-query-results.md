---
name: Production query result validation
description: Do not treat a success flag without SELECT results as a successful production query.
---

A production SQL callback can report success while returning only
`START TRANSACTION` and `ROLLBACK` when a SELECT references a nonexistent column.
That output is not proof of an empty result set.

**Why:** A diagnostic SELECT initially returned that combination; inspecting the
live schema and correcting the column name produced real tabular results.

**How to apply:** Validate the returned output as well as the success flag.
Inspect live schema when output lacks column headings/results; never infer
missing production data from transaction-only output.
