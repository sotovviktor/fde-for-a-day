# Task 1 — Customer Brief

Commander Kapoor runs Mission Ops on a deep-space station with ~2,000 crew. Her team triages about 180 signals a day by hand. It's slow and broken:

- Time-to-route: **3.4 hours** average
- Misroute rate: **42%**
- Missing info means constant back-and-forth over a 4-minute light delay
- Ops staff spend all their time routing instead of resolving

## What She Wants

An API that does first-pass triage — classify, prioritize, route, flag missing info, suggest next steps. Fast enough to be useful. JSON, not a chatbot.

## Targets

- Misrouting under 15%
- Time-to-route under 5 minutes
- Missing info flagged proactively
- Remediation steps that are actually useful

## Signal Channels

- `subspace_relay`: longer, more detailed reports
- `holodeck_comm`: short crew chatter, often missing context
- `bridge_terminal`: structured form input with inconsistent quality
- `emergency_beacon`: noisy and often panicked transcriptions

The stream also contains junk: auto-replies, thank-you messages, cryo notifications, and non-incident transmissions.

## Specialist Teams

| Team | What they own |
|---|---|
| Crew Identity & Airlock Control | Biometric access, identity, provisioning, directory sync |
| Spacecraft Systems Engineering | Devices, workstation issues, ShipOS, peripherals, hardware faults |
| Deep Space Communications | Subspace relay, local comms mesh, DNS beacons, routing, inter-deck links |
| Mission Software Operations | Mission apps, licensing, integrations, internal tools |
| Threat Response Command | Hostile activity, containment, suspicious access, data breaches, certificate issues |
| Telemetry & Data Core | Data cores, archives, backups, storage, telemetry pipelines |

## Things To Know

- People say "urgent" about everything. Context matters more than keywords.
- Quiet signals can be the real emergencies.
- Hull breach, atmospheric compromise, restricted access → always escalation-worthy.
- Team ownership is messy on purpose. Your system needs judgment, not just pattern matching.
