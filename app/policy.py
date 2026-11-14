"""Severity and routing (F6). Thresholds mirror corpus/policy/severity_policy.md [POL-SEV]."""
ROUTES = ["self-serve-runbook", "page-secondary", "incident-commander"]

def severity(req):
    m, v, lab = req.metric, req.value, {k.lower(): x for k, x in (req.labels or {}).items()}
    sev, why = 3, "no SEV1/SEV2 threshold crossed"
    if m == "disk_usage_pct":
        free = 100 - v
        if free < 10: sev, why = 1, f"disk free {free:.0f}% < 10% [POL-SEV]"
        elif free < 20: sev, why = 2, f"disk free {free:.0f}% < 20% [POL-SEV]"
        else: why = f"disk free {free:.0f}% >= 20%"
        try:
            if float(lab.get("time_to_full_min", 1e9)) < 60: sev, why = 1, f"time_to_full_min {lab['time_to_full_min']} < 60 [POL-SEV]"
        except (TypeError, ValueError): pass
    elif m == "replication_lag_s":
        sev, why = (1, f"lag {v:.0f}s > 1800s") if v > 1800 else (2, f"lag {v:.0f}s > 300s") if v > 300 else (3, f"lag {v:.0f}s <= 300s")
    elif m == "error_rate_pct":
        sev, why = (1, f"error rate {v}% >= 50%") if v >= 50 else (2, f"error rate {v}% > 5%") if v > 5 else (3, f"error rate {v}% <= 5%")
    elif m == "availability_pct":
        sev, why = (1, f"availability {v}% < 95%") if v < 95 else (2, f"availability {v}% < 99.5%") if v < 99.5 else (3, f"availability {v}% >= 99.5%")
    elif m == "connections_pct":
        sev, why = (1, f"connections {v}% >= 99%") if v >= 99 else (2, f"connections {v}% >= 95%") if v >= 95 else (3, f"connections {v}% < 95%")
    elif m == "latency_p99_ms":
        sev, why = (1, f"p99 {v}ms >= 10000ms") if v >= 10000 else (2, f"p99 {v}ms >= 5000ms") if v >= 5000 else (3, f"p99 {v}ms < 5000ms")
    elif m == "cpu_pct":
        sev, why = (2, f"cpu {v}% >= 95%") if v >= 95 else (3, f"cpu {v}% < 95%")
    return sev, why

def route_for(req, escalated: bool, reason: str = ""):
    sev, why = severity(req)
    route = {1: "incident-commander", 2: "page-secondary", 3: "self-serve-runbook"}[sev]
    basis = f"SEV{sev}: {why}"
    if escalated and route == "self-serve-runbook":
        route = "page-secondary"; basis += f"; raised to page-secondary because the system declined to answer ({reason}) [POL-ESC]"
    elif escalated:
        basis += f"; system also declined to answer ({reason}) [POL-ESC]"
    return {"severity": f"SEV{sev}", "route": route, "basis": basis}
