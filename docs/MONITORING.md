# Monitoring — makers-agent

## Endpoints

| Path | Purpose |
|------|---------|
| `/api/v1/agent/health/live` | Liveness |
| `/api/v1/agent/health/ready` | Readiness (token/config/data/session/job/lance) |
| `/metrics` | Prometheus scrape (network-restrict in production) |

## Suggested scrape config

```yaml
scrape_configs:
  - job_name: makers-agent
    metrics_path: /metrics
    static_configs:
      - targets: ["agent:8080"]
```

## Alert rules

File: [`deploy/prometheus/agent-alerts.yml`](../deploy/prometheus/agent-alerts.yml)

| Alert | Intent |
|-------|--------|
| `AgentHighHTTP5xxRate` | 5xx share > 5% for 10m |
| `AgentHighLatencyP95` | p95 latency > 15s for 15m |
| `AgentJobFailureSpike` | failed job rate elevated |
| `AgentReadinessDown` | scrape target down |

Tune thresholds for your traffic. Pair with `AGENT_LOG_FORMAT=json` and `request_id` for incident correlation.

## Key metrics

- `agent_http_requests_total{method,path,status}`
- `agent_http_request_duration_seconds_*`
- `agent_active_sessions`
- `agent_jobs_terminal_total{job_type,state}`
