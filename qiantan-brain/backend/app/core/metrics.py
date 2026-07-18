"""Custom Prometheus business metrics for Qiantan operations monitoring."""

from prometheus_client import Gauge, Histogram


# Dead letter queue depth by status
dead_letter_gauge = Gauge(
    "qiantan_dead_letter_total",
    "Dead letter queue depth",
    ["status"],
)

# Device health status count
device_health_gauge = Gauge(
    "qiantan_device_health",
    "Device health status count",
    ["status"],  # online / offline / degraded
)

# Reconciliation task status count
reconciliation_status_gauge = Gauge(
    "qiantan_reconciliation_status",
    "Reconciliation task status count",
    ["status"],  # pending / balanced / exception / resolved
)

# HTTP request duration histogram (custom, for business-specific slicing)
http_request_duration = Histogram(
    "qiantan_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
)
