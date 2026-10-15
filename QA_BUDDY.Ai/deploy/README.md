# Deploy

Files for running QABuddy on a server. The full steps are in the [QABuddy README](../README.md).

| File | What it is |
|---|---|
| [Caddyfile](Caddyfile) | HTTPS reverse proxy in front of the app. Set `QABUDDY_DOMAIN` in `.env` to a DNS name that points at the server, and Caddy gets and renews the certificate. Answers stream token by token, so it does not buffer them |
| [qabuddy.cron](qabuddy.cron) | Crontab lines for the hourly refresh (`git pull`, Jira sync, then incremental ingest) on a Linux server, for a plain install or for Docker. Add yours with `crontab -e`; see [Auto-ingestion](../README.md#auto-ingestion-every-hour) |
