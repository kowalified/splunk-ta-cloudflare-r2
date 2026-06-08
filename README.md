# Cloudflare R2 Log Ingestion Add-on for Splunk

A Splunk Technology Add-on (TA) that ingests Cloudflare Logpush log files from
Cloudflare R2 via a Python modular input using the S3-compatible API.

**AppInspect**: 0 errors | 0 failures | 0 future_failures

---

## Why this exists

The [Splunk Add-on for AWS](https://splunkbase.splunk.com/app/1876) does not work
with Cloudflare R2. It calls `sts.get_caller_identity()` to validate credentials
before saving them. R2 is S3-compatible but has no AWS STS service, so the add-on
rejects R2 credentials unconditionally. This is a confirmed dead-end as of v8.1.2.

This TA solves the gap with a purpose-built modular input that uses SigV4 auth
and path-style S3 calls directly - no STS dependency.

---

## What it does

```
Cloudflare R2 bucket  →  ListObjectsV2 + GetObject  →  gunzip  →  Splunk index
(Logpush NDJSON files)    (SigV4, path-style)          (one event per JSON line)
```

- Works with **any Cloudflare Logpush dataset** (Gateway DNS, HTTP Requests,
  Access, Audit, etc.) - fully dataset-agnostic
- One input instance per bucket/prefix, each with its own sourcetype and index
- Checkpointing via `StartAfter` - only new files are processed after each poll
- Checkpoints survive Splunk restarts - zero duplicate events

---

## Installation

1. Download `TA-cloudflare-r2-0.1.0.tgz` from [Releases](../../releases)
2. In Splunk Web: **Apps > Manage Apps > Install app from file**
3. Upload the `.tgz` file and click **Upload**

---

## Configuration

Go to **Settings > Data Inputs > Cloudflare R2 Log Ingestion > New**.

| Field | Description |
|---|---|
| Input Name | Unique name for this input instance |
| Cloudflare Account ID | 32-character hex string from your Cloudflare dashboard URL |
| R2 Access Key ID | Generate via: Cloudflare Dashboard > R2 > Manage R2 API Tokens |
| R2 Secret Access Key | Shown once at token creation. Object Read permission required. |
| R2 Bucket Name | Name of the R2 bucket containing Logpush files |
| Key Prefix | Subfolder to read from (e.g. `gateway_dns/`). Leave blank for all files. |
| Polling Interval | How often to check for new files, in seconds. Default: 300 |
| Verify SSL Certificate | Uncheck only if your network performs TLS inspection |
| Source type | Splunk sourcetype for events (see table below) |
| Index | Splunk index to store events |

### Sourcetype mapping (for use with [Cloudflare App for Splunk](https://splunkbase.splunk.com/app/4501))

| Logpush Dataset | Key Prefix | Sourcetype |
|---|---|---|
| Zero Trust Gateway DNS | `gateway_dns/` | `cloudflare:dns` |
| Zero Trust Gateway HTTP | `gateway_http/` | `cloudflare:http` |
| Zero Trust Gateway Network | `gateway_network/` | `cloudflare:network` |
| Zone HTTP Requests | `http_requests/` | `cloudflare:json` |
| Zero Trust Access | `access/` | `cloudflare:access` |
| Cloudflare Audit Logs | `audit/` | `cloudflare:audit` |

### Multiple datasets from one bucket

Create one input per dataset, each pointing at a different prefix:

```ini
[cloudflare_r2://my_gateway_dns]
bucket_name = cloudflare-managed-c9c00975
key_prefix = gateway_dns/
sourcetype = cloudflare:dns
index = cloudflare_logs

[cloudflare_r2://my_http_requests]
bucket_name = cloudflare-managed-c9c00975
key_prefix = http_requests/
sourcetype = cloudflare:json
index = cloudflare_logs
```

### Multiple Cloudflare accounts

Each input has its own credentials, so multiple Cloudflare accounts work natively:

```ini
[cloudflare_r2://account_a_dns]
account_id = <account_a_id>
access_key_id = <account_a_key>
...

[cloudflare_r2://account_b_dns]
account_id = <account_b_id>
access_key_id = <account_b_key>
...
```

---

## How Logpush + R2 works

1. Configure a [Cloudflare Logpush job](https://developers.cloudflare.com/logs/logpush/)
   to write to an R2 bucket (use the automatic R2 setup in the dashboard)
2. Logpush writes gzipped NDJSON files every ~30 seconds:
   `gateway_dns/20260608/20260608T120000Z_20260608T120030Z_abc123.log.gz`
3. This TA polls R2 on your configured interval, downloads new files,
   decompresses them, and sends each JSON line as a Splunk event

---

## Resetting checkpoints (re-ingest all data)

```bash
splunk clean inputdata cloudflare_r2
```

---

## Requirements

- Splunk Enterprise 8.x or higher
- Python 3 (bundled with Splunk 8+)
- Cloudflare account with R2 enabled
- R2 API token with **Object Read** permission on the target bucket

---

## Known limitations

- **Credentials stored in inputs.conf** - the secret access key is not encrypted
  in this prototype version. A production deployment should implement a custom
  REST handler to store credentials via Splunk's `storage/passwords` API.
  This is documented in [HANDOFF.md](TA-cloudflare-r2/HANDOFF.md).
- **No Splunk Cloud validation** - tested on Splunk Enterprise 10.4.0 only.

---

## Status

This is a **reference prototype** intended for handoff to Cloudflare's Splunk
partnership team and their third-party vendor for productionization and official
Splunkbase publication.

**Do not publish this add-on to Splunkbase under a personal account.** Code
written by a Cloudflare employee in the course of their work is Cloudflare IP
and requires legal/management sign-off before any public release.
See [HANDOFF.md](TA-cloudflare-r2/HANDOFF.md) for full details.

---

## License

Apache 2.0 - see [LICENSE](TA-cloudflare-r2/LICENSE)
