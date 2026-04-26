# Task 1 — Routing Guide

Routing isn't clean-cut. Some signals could go to multiple teams depending on context. This guide gives you the rules we have, but expect ambiguity — that's part of the task.

## Priority Definitions

| Priority | When to use |
|---|---|
| `P1` | Hull breach, life-support failure, containment failure, hostile contact, command-level emergency |
| `P2` | Major system failure, no workaround, multiple crew affected |
| `P3` | Standard operational issue with a workaround or limited impact |
| `P4` | Routine annoyance, question, or low-impact request |

### Override Rule

Anything mentioning potential hull breach, atmospheric compromise, decompression, or containment failure should be treated as **P1** even if the wording sounds calm.

## Primary Routing Summary

| Team | Typical issues |
|---|---|
| Crew Identity & Airlock Control | Biometric access, directory sync, SSO, lockouts, provisioning |
| Spacecraft Systems Engineering | Hardware faults, device issues, ShipOS, local fabricators, peripherals |
| Deep Space Communications | Subspace relay, local comms mesh, DNS beacons, bandwidth, signal routing |
| Mission Software Operations | FlightOS, navigation suite, mission apps, licensing, internal tools |
| Threat Response Command | Hostile activity, containment, malware, unauthorized access, certificate issues |
| Telemetry & Data Core | Data access, archives, backups, storage, telemetry pipelines |

## Routing Heuristics

- Biometric or access-policy issues usually start with **Crew Identity & Airlock Control**.
- Hardware and device faults usually start with **Spacecraft Systems Engineering**.
- Station and relay connectivity issues usually start with **Deep Space Communications**.
- Application behavior, licensing, and tool failures usually start with **Mission Software Operations**.
- Suspicious behavior, containment, or possible data exfiltration should go to **Threat Response Command**.
- Archives, backups, and data access requests usually go to **Telemetry & Data Core**.

## Gray Areas

These are intentionally messy:

- **BioAuth panel failures** could be identity, threat, or hardware depending on context.
- **SubComm issues** could be comms, software, or device hardware.
- **Station Core compute requests** do not map cleanly to a single owner.
- **Crew onboarding and departure** spans multiple teams.
- **Networked fabricators and scan-to-archive flows** can cross team boundaries.

## When To Escalate

Set `needs_escalation = true` when:

1. It's `P1`.
2. Hostile contact, containment, or malware risk.
3. Life-support threat (oxygen, pressure, temperature).
4. Navigation or trajectory risk.
5. Unauthorized access or data exfiltration.
6. Command-level reporter, or the same issue keeps coming back unresolved.
