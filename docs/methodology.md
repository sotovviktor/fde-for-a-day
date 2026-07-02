# Methodology

## Approach

Foundation first, then fan out. I built Task 1 as one vertical slice — but I built
the parts that all three tasks would share (the Azure client, settings, the
`prompt.yaml` + `prompt.py` + domain-module pattern) as task-agnostic from the
start. Once that spine existed, Tasks 2 and 3 were mostly new prompts and new
domain logic on top of it, and they landed together in a single push.

The other constant: the scorer was the spec. Before writing each task I read its
scorer in `fdebenchkit` and let the weights drive the design. That is why triage
optimizes five specific fields, extraction preserves exact strings, and
orchestration records exact resolved parameters — each maps to how points are
actually awarded, not to what looked impressive.

## Time allocation

The commit history tells the story (all on 2026-07-01):

| Time | Commit | What landed |
|---|---|---|
| 00:12 | Triage task | Task 1 **+ the shared spine** (LLM client, settings, prompt/guardrails pattern) |
| 08:33 | task 2 and 3 | Extract + orchestrate, built on the Task 1 foundation |
| 10:32 | Refactor code | First consolidation pass |
| 11:00 | Infra + refactor | Pulumi scaffold begins |
| 11:10 | refactor tests | Test suite reshaped |
| 12:52 | Refactor | Further cleanup |
| 14:23 | Finish pulumi | Deployment finished |

Two things stand out. Task 1 got a dedicated block because it carried the shared
foundation; Tasks 2 and 3 were comparatively cheap because that foundation already
existed. And roughly half the wall-clock after the features worked went to
refactoring, tests, and infrastructure — hardening cost about as much as building.

## Task 1: Signal Triage

One structured LLM call, validated against the fixed `TriageResponse`, then a
deterministic guardrail pass. The design came straight from the scorer: only five
fields are scored (category, priority, team at 24% each; missing-info 17%;
escalation 11%), so `next_best_action` and `remediation_steps` stay short to
protect latency, and the prompt is organized dimension by dimension. It is now
**zero-shot**: the few-shot examples were removed once they proved to hurt
robustness on adversarial inputs (they made the small model rigid). Two things
replace what they taught implicitly — an explicit output-format rule ("emit the
bare priority code, no label") and a `priority` / `category` / `assigned_team`
before-validator that coerces a labelled reply such as `"P2 (Yellow Alert)"` back
to `"P2"`. That coercion matters more than it looks — see *Post-submission* below.

The guardrails are where I stopped trusting the model: force `team = None` on
non-signals, a deliberately narrow always-escalate phrase list, de-dupe
missing-info, re-stamp the `ticket_id`. Narrow was a choice — a broad
always-escalate list would wreck escalation precision, which is scored as a binary.
What I would not defend: the team back-fill is a static category→team map, so the
documented gray areas (a security-dominant biometric issue) live only in the
prompt, not in code. The eval bore this out unevenly: `category` (0.760) and
`routing` (0.711) are the strong dimensions, but `missing_info` (0.249) is the
weakest of any dimension across all three tasks and the main drag on triage
resolution.

## Task 2: Document Extraction

One vision call, with the per-document `json_schema` injected into the prompt
rather than enforced as a strict structured output — the real schemas use optional
fields, `additionalProperties`, `enum`, `pattern`, and `format`, which strict mode
rejects. The scorer drove the hard call in normalization: information accuracy
(70%) coerces numbers, but text fidelity (30%) is an exact string match. So I
**preserve strings exactly and coerce only numbers and booleans** — stripping a
`$` would lose fidelity points, while `float("1,234")` raises unless the grouping
comma is removed. That single asymmetry is most of the extraction logic.

The constraint I kept an eye on was the timeout budget: `timeout × (retries + 1)`
has to stay under the platform's 60s deadline, so vision gets an 18s per-call
timeout and a capped retry count. Deferred on purpose: image downscaling
(Pillow) — worth it only if latency runs hot on big multi-page tables, which I did
not measure. In hindsight I should have: extraction is the slowest task (P95
14.2 s) and the only one that fails a resilience probe (`concurrent_burst`), so
latency and load — not the schema logic — are where it loses points.

## Task 3: Workflow Orchestration

The hardest task, and the one where I rejected the obvious tool. I did **not** use
the Microsoft Agent Framework: its native loop spends one LLM round-trip per tool
call, which fights the 1,500 ms latency target and hands away control of the exact
`steps_executed` trace the scorer reads. Instead: a lean, bounded plan → execute →
observe loop over the shared client — now resolving to **one planning call** for the
common case, and taking another round only for genuine discovery tails (bounded at
3 rounds, 30 calls).

A loop was necessary, not decorative. Constraint compliance (40%) and goal
completion (20%) are checked against exact resolved parameters, and workflows like
churn or re-engagement need entities discovered at runtime (`crm_search` →
`subscription_check` → conditional act) — a single blind plan cannot do that.
Failures become observations the planner can react to; consecutive same-tool calls
run in parallel for the fan-out latency win. `status` is honest — `completed` only
when the loop finished cleanly, because `goal_completion` scores zero otherwise.
The design paid off where it was aimed — `constraint_compliance` (0.760, the 40%
driver) and `tool_selection` (0.759) are the strong dimensions — but
`goal_completion` (0.452) is the weak point: workflows still stop short of the full
goal, and the multi-round loop makes this the worst task on latency (P95 11.9 s,
efficiency 40.0).

## Post-submission: reliability at scale

The local harness said 65.6 ([evals.md](evals.md)); the portal said **44.9**, with
Task 3 resolution at **2.7** on only **31 of 500** items scored. That gap was the
most important finding of the exercise — and it was not a modelling problem.

**Root cause: 429 throttling at scale.** The hidden set is ~2,000 items
(1,000 / 500 / 500); the public set is 50. At 50 items and low concurrency the app
never approached the Azure OpenAI rate limit, so the local score looked healthy. At
portal scale the endpoints pushed a single shared deployment past its RPM quota,
Azure returned `429 rate_limit_exceeded`, the three retries exhausted, and the
request surfaced as a 503. **An errored item scores 0 on every dimension**, so the
aggregate collapsed. Task 3 suffered most because the old loop made up to four LLM
calls per workflow — four chances to be throttled. The arithmetic is stark: 31 of
500 items averaging ~43 is exactly the 2.7 the portal reported. A reliability
failure, not an accuracy one.

**The fixes (all in the app, validated locally and by live smoke):**

1. **A global token-bucket rate limiter** in the shared `AzureLLMClient`. All
   three endpoints pace their request *starts* against the one quota they share;
   the retry path is gated too, so retries can't amplify a storm. The single
   highest-impact change.
2. **Task 3 plan-then-execute.** The loop emits the whole workflow in one planning
   call when every parameter is known from the goal, and re-plans only for
   discovery tails. Typical LLM calls per workflow fell from four to **one** — a
   direct cut to latency and throttling. A live incident-response workflow across
   three warehouses completed in one round, ~3.9 s, `status=completed`.
3. **Triage went zero-shot** — which also deleted the implicit output-format
   lesson, so the model began returning `priority: "P2 (Yellow Alert)"`, failing
   the strict `Literal` validation and 503-ing (another 0-score path). Fixed with
   an explicit format rule and a before-validator that coerces the label back to
   `"P2"`.
4. **Extraction now iterates the schema's properties,** not the model's returned
   keys, so an omitted field becomes `null` instead of vanishing — the nested-field
   gap that most hurt information accuracy.

**Tuning against the probes.** `concurrent_burst` (20 requests in 500 ms, ≥18 must
succeed) is what most constrains a rate limiter. The deployment quota was raised to
**1,000 RPM** in test and prod, and the limiter sized so the worst-case minute
(burst budget + refill) stays under quota while the burst still clears — a local
run now passes all seven probes. An earlier attempt against the old 150-RPM test
deployment failed the burst probe: a deployment-quota confound, not a code bug.

**Why local missed it.** The 65.6 was real but not portal-predictive: N=50 at low
concurrency exercises neither the rate limit nor the adversarial subset (public
gold omits `difficulty`). The earlier "measure while building" lesson gets a
sharper edge — measure at representative *scale and concurrency*, not just
representative inputs. A 50-item pass will never surface a throttling cliff that
only exists at 2,000.

## What worked

- **Building the shared spine during Task 1.** The payoff is visible in the git log: Tasks 2 and 3 shipped in one commit because the client, settings, and patterns already existed.
- **Reading the scorer before the prompt.** Every task's key decision — five-field focus, string-fidelity preservation, exact-parameter recording — traces to a specific weight in `fdebenchkit`.
- **Judgment in the prompt, contracts in code.** Guardrails, normalization, and loop caps make model mistakes recoverable and unit-testable without a network call.
- **One nano model everywhere.** It maxes the cost sub-score (1.000 on every task), and resolution held up better than feared (avg 64.9) with prompt engineering and deterministic checks doing the accuracy work. (Latency was the price — see below.)
- **Diagnosing the portal collapse from first principles.** The errored-item count (31/500) and the `429` logs pinned it to throttling, not accuracy — which pointed at a rate limiter and fewer round-trips rather than more prompt tuning.

## What didn't work

- **I measured too late.** The full eval pass exists now ([evals.md](evals.md), composite 65.6), but I ran it after tuning — so its findings are post-mortem, not design input. It surfaced latency as the systemic drag (efficiency avg 50.4) and three resolution gaps I would have designed around: triage `missing_info` (0.249), orchestration `goal_completion` (0.452), extraction `text_fidelity` (0.600).
- **Latency undercut the model bet.** nano maxed the cost sub-score (1.000 everywhere) but is not fast enough here: every task's latency score is ≤ 0.262 and orchestration's P95 (11.9 s) zeroes the penalty. The "cheap and fast" assumption was half wrong.
- **`/extract` breaks under concurrent load.** It is the only probe failure in the run — the 20-request `concurrent_burst` — and the least robust task (72.8).
- **The failure contract drifted.** Extract and orchestrate return a scored 200 fallback; triage returns 503. Defensible per task, inconsistent as a whole, and never reconciled.
- **`accounts_processed` is still a heuristic.** The other aggregate fields (`emails_skipped`, `skip_reasons`, `constraints_satisfied`) are now populated from the planner's final self-report, but `accounts_processed` still scrapes `account_id` parameters from the trace.
- **Domain rules as prose.** Orchestration's roles, templates, and channels live in the prompt, which risks overfitting the public set instead of generalizing.
- Adding examples to prompts collapsed under real run

## Key learnings

- **The scorer is the specification.** Reading `fdebenchkit` first was the highest-leverage habit; it changed the design of all three tasks.
- **Reuse compounds — invest in the foundation early.** The one-commit Task 2/3 build was bought by the extra care taken on Task 1's shared spine.
- **Decide the failure contract once, up front.** The 503-vs-200 split is a small decision that should have been made globally on day zero, not per task.
- **Measure while building, not after.** I did run the full harness in the end (composite 65.6), but late — measuring earlier would have caught the latency drag and triage's `missing_info` gap while there was still time to fix them.
- **Removing a prompt crutch can break an implicit contract.** The few-shots were also teaching the output format; deleting them re-introduced a 503. De-risk enum outputs with a lenient before-validator, not just prose.
