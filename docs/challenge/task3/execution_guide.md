# Task 3 — Execution Guide

This is an execution task. The benchmark rewards workflows that achieve the right outcome while respecting constraints — not plans that read well on paper.

**Use the actual tools.** Keep the trace grounded in real calls. `constraint_compliance` is the highest-value dimension.

**Handle failures.** Retries, skips, and partial progress all need to be explicit in the trace.

**Good solutions tend to:** track state across steps, validate tool outputs before the next call, record why a branch was skipped, and prefer small verifiable progress over one opaque leap.
