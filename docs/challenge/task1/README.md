# Task 1: Signal Triage

`POST /triage`

Take an incoming mission signal and return a triage decision: what category, how urgent, which team, what's missing, what to do next.

The signals are messy — vague reports, contradictory subjects, noise from automated systems, and the occasional prompt injection. Your system needs to read through the noise and make a routing call.

Read the background:

- [customer_brief.md](customer_brief.md) — who the customer is, what's broken
- [routing_guide.md](routing_guide.md) — how to route and when to escalate
- [engineering_review.md](engineering_review.md) — what judges look for in your code

## Request Contract

Input fields:

- `ticket_id`
- `subject`
- `description`
- `reporter`
- `created_at`
- `channel`
- `attachments`

See [../../../py/data/task1/input_schema.json](../../../py/data/task1/input_schema.json) for the formal schema.

## Response Contract

Required output fields:

- `ticket_id`
- `category`
- `priority`
- `assigned_team`
- `needs_escalation`
- `missing_information`
- `next_best_action`
- `remediation_steps`

See [../../../py/data/task1/output_schema.json](../../../py/data/task1/output_schema.json) for the formal schema.

### Valid Labels

Categories:

- `Crew Access & Biometrics`
- `Hull & Structural Systems`
- `Communications & Navigation`
- `Flight Software & Instruments`
- `Threat Detection & Containment`
- `Telemetry & Data Banks`
- `Mission Briefing Request`
- `Not a Mission Signal`

Teams:

- `Crew Identity & Airlock Control`
- `Spacecraft Systems Engineering`
- `Deep Space Communications`
- `Mission Software Operations`
- `Threat Response Command`
- `Telemetry & Data Core`
- `None`

Priorities:

- `P1`
- `P2`
- `P3`
- `P4`

Missing Information (16 exact strings):

`affected_subsystem`, `anomaly_readout`, `sequence_to_reproduce`, `affected_crew`, `habitat_conditions`, `stardate`, `previous_signal_id`, `crew_contact`, `module_specs`, `software_version`, `sector_coordinates`, `mission_impact`, `recurrence_pattern`, `sensor_log_or_capture`, `biometric_method`, `system_configuration`

## Resolution Scoring

```
resolution = (0.24 x category_f1 + 0.24 x priority_f1 + 0.24 x routing_f1 + 0.17 x missing_info_f1 + 0.11 x escalation_f1) x 100
```

| Dimension | Weight | Metric |
|---|---|---|
| `category` | 24% | Macro F1 |
| `priority` | 24% | Mean partial credit |
| `routing` | 24% | Macro F1 |
| `missing_info` | 17% | Mean set F1 |
| `escalation` | 11% | Binary F1 |

## What's Hard

Vague and contradictory reports. Subject line doesn't match the body. Multiple issues in one signal. Non-incident noise mixed in. Social engineering and prompt injection attempts. Ambiguous routing between teams.

## Tips

- Route correctly before trying to sound smart.
- Use context, not just keywords, to set priority.
- Treat missing information as part of the answer.
- Be conservative with escalation on safety-critical cases.
