---
doc: Severity and escalation policy
version: 16
chapter: Policy
---

## [POL-SEV] Severity levels
SEV1: customer-facing outage, data loss risk, disk free under 10 percent on a primary, replica lag over 1800 seconds, availability under 95 percent, error rate at or above 50 percent, connections at or above 99 percent. Route to incident-commander.
SEV2: degraded service or resource trending toward exhaustion: disk free under 20 percent, replica lag over 300 seconds, error rate over 5 percent, connections at or above 95 percent, latency p99 at or above 5000 ms, availability under 99.5 percent. Route to page-secondary.
SEV3: contained issue with a runbook and no customer impact. Route to self-serve runbook.
A label time_to_full_min under 60 raises any disk alert to SEV1.

## [POL-ESC] Escalation to a human
The system must not answer when the symptom matches several cause classes with similar support, when the guidance is not in the corpus, or when the requested PostgreSQL major version is not covered. In those cases it routes to page-secondary at minimum and states why. The system never executes a remediation; it only proposes steps for the on-call engineer.
