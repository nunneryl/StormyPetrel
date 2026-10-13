# How forecast-pipeline is scheduled

GitHub's scheduler stopped being dependable for this workflow, so the runs are now started
from outside at fixed times. GitHub's cron is kept only as a fallback, and a separate service
emails when no full run has succeeded for too long.

| Piece | What it does | Where it lives |
|---|---|---|
| The slots | When the full pipeline should run | `DISPATCH_SLOTS` in `.github/workflows/forecast-pipeline.yml` |
| cron-job.org | Calls `workflow_dispatch` at each slot, and every hour for buoys | Three jobs on cron-job.org, defined below |
| GitHub cron | Fallback, 20 minutes after each slot, plus hourly buoys | `on.schedule` in the workflow |
| The gate | Skips a run that an earlier run already covers | `scripts/dispatch_gate.py`, the workflow's `gate` job |
| healthchecks.io | Emails when no successful full run has pinged for 8 hours | One check, defined below; its ping URL is the secret `HEALTHCHECKS_FULL_PIPELINE_URL` |

## Why

Over 30 days (2026-09-12 to 2026-10-12), forecast-pipeline's GitHub schedule did this:

- Scheduled full runs started a median 4.4 h late. The 03:30 slot started about 6 h late.
- Two slots had no run, and one run never got a runner.
- The hourly buoy-update ran in 22% of hours.
- All of this got worse over the 30 days.

A `workflow_dispatch` call starts a run within seconds.

## The slots

```
DISPATCH_SLOTS: '01:45 08:15 13:45 20:15'
```

All times are UTC. Each slot lands just after most NWPS offices publish a cycle. The times
come from the NWPS publication log (`scripts/nwps_publication_log.py --report`): 11 of its job
logs, 6,228 observations, and NWPS cycles from 2026-09-19 to 2026-10-12. "Caught" below means
the cycle the run targets was already on NOMADS when the run started, weighted by spots, over
the 21 complete days from 2026-09-20 to 2026-10-10.

| Slot | What it picks up | Caught |
|---|---|---|
| 01:45 | 18Z from the offices that run four cycles a day: chs, gyx, ilm, jax, lox, phi, sju | 97.4%. Every miss is a jax 18Z cycle that was never published. |
| 08:15 | 00Z from all 25 offices that have spots (pqr's 06Z) | 98.7% |
| 13:45 | 06Z from the same seven offices as 01:45 | 96.7%. jax 06Z is erratic. |
| 20:15 | 12Z from all 25 offices (pqr's 18Z) | 97.7% |

Seventeen offices publish only twice a day, at 00Z and 12Z: akq, box, bro, crp, eka, hfo, hgx,
mfl, mfr, mhx, mlb, mtr, okx, sew, sgx, tae and tbw. Together they cover 386 of the 648 spots,
and only the 08:15 and 20:15 runs bring them anything new.

The publication data was all collected under US daylight time. After DST ends on 1 November,
re-run `--report` to see whether any office now publishes later in UTC. If one does, move the
slot (see the last section).

## The fallback cron and the gate

GitHub's cron runs 20 minutes after each slot. On 30 days of evidence it usually fires hours
late, which is why it is only a fallback:

| Slot | Fallback | Cron |
|---|---|---|
| 01:45 and 13:45 | 02:05 and 14:05 | `5 2,14 * * *` |
| 08:15 and 20:15 | 08:35 and 20:35 | `35 8,20 * * *` |
| buoy, every hour | every hour | `0 * * * *` |

The `gate` job runs first in every run. The rule is in `scripts/dispatch_gate.py`:

- A manual run from the Actions tab (`trigger: manual`, the default) always goes ahead.
- For a scheduled run or an external dispatch:
  - The **anchor** is the latest slot of the run's job at or before now plus 5 minutes. Buoy
    slots are the top of every hour.
  - The run is **skipped** when another run of the same job, with a lower run id, was created
    in the 5 minutes before the anchor or any time after it, and is queued, running, or
    succeeded.
  - A run that failed or was cancelled covers nothing, so a late cron run after a failed
    dispatch acts as a retry.
- The gate **fails open**. If it cannot decide, the run goes ahead: a duplicate run costs
  runner minutes, while a missed run costs a cycle.

The gate tells the two jobs' runs apart by the run title. The workflow's `run-name` starts
with the job, for example `full-pipeline (external)`.

Runs of the same job also share one concurrency group whoever started them, so a dispatched run
and a late cron run never run at the same time. The group used to be keyed on the trigger,
which put a scheduled full run and a dispatched one in different groups.

A run the gate turns away ends green, with both work jobs skipped. The failure-issue job
reports nothing for it and does not close an open issue: a skipped job is neither a failure
nor a success, and the gate's own success says nothing about either job.

## cron-job.org

Set the account's time zone to **UTC**. Create these three jobs. All three use the same URL,
method and headers:

- **URL:** `https://api.github.com/repos/nunneryl/StormyPetrel/actions/workflows/forecast-pipeline.yml/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <the fine-grained token>`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
- **Success:** GitHub answers `204 No Content` with an empty body. Any 2xx counts as success.
- **Notifications:** on failure.

### 1. "StormyPetrel full 01:45 + 13:45"

- Schedule: every day, hours 1 and 13, minute 45 (crontab `45 1,13 * * *`).
- URL: `https://api.github.com/repos/nunneryl/StormyPetrel/actions/workflows/forecast-pipeline.yml/dispatches`
- Body:

```json
{"ref":"main","inputs":{"job":"full-pipeline","trigger":"external"}}
```

### 2. "StormyPetrel full 08:15 + 20:15"

- Schedule: every day, hours 8 and 20, minute 15 (crontab `15 8,20 * * *`).
- URL: `https://api.github.com/repos/nunneryl/StormyPetrel/actions/workflows/forecast-pipeline.yml/dispatches`
- Body:

```json
{"ref":"main","inputs":{"job":"full-pipeline","trigger":"external"}}
```

### 3. "StormyPetrel buoy hourly"

- Schedule: every hour, minute 0 (crontab `0 * * * *`).
- URL: `https://api.github.com/repos/nunneryl/StormyPetrel/actions/workflows/forecast-pipeline.yml/dispatches`
- Body:

```json
{"ref":"main","inputs":{"job":"buoy-update","trigger":"external"}}
```

### The token

The token is a fine-grained personal access token, created as follows:

- **Resource owner:** nunneryl
- **Repository access:** only `nunneryl/StormyPetrel`
- **Repository permissions:** Actions, Read and write. Metadata, read-only, is added
  automatically. Nothing else.

With this token someone can start, cancel, re-run or delete runs. They cannot read secrets or
change code. Give it an expiry date and renew it before then: an expired token makes every
dispatch fail with 401, and then only the fallback cron runs.

## healthchecks.io

Create one check:

- Name: `forecast-pipeline full`
- Schedule: **Simple**
- Period: **7 hours**
- Grace: **1 hour**
- Integration: email

The full-pipeline job pings the check's URL as its last step before the failure handlers. It
pings only when every step succeeded, the column check included. The ping URL is stored as the
repository secret `HEALTHCHECKS_FULL_PIPELINE_URL`. Until that secret exists, the step says so
and sends nothing. The step is `continue-on-error` with a 2-minute timeout, so the ping can
never fail or delay the run.

Why 7 hours plus 1:

- A run ends roughly 25–35 minutes after its slot.
- The slots are 5.5 or 6.5 hours apart, so the longest normal gap between pings is about
  6.5 hours plus a few minutes.
- The alert therefore fires 8 hours after the last good run, about 1.5 hours after a missed or
  failed slot should have finished.
- If you would rather hear only about two missed runs in a row, use Period 13 hours, Grace 1
  hour.

The ping URL is a credential. Anyone who has it can ping, and so hide an outage.

## When an outside service is down

| What breaks | What happens |
|---|---|
| cron-job.org down, or its job disabled | Only the gated GitHub cron runs, late. healthchecks.io emails after 8 h. |
| Dispatches fail (expired or revoked token, renamed workflow, changed inputs) | cron-job.org records the failure and emails; it may disable the job after repeated failures. The fallback cron and healthchecks.io cover the gap. |
| healthchecks.io down | No alert. The pipeline is unaffected because the ping cannot fail it. Failed runs are still reported by the failure issue. |
| Both down | Runs fall back to GitHub's late cron, and no alert is sent. |
| GitHub Actions down, or no runner | Nothing starts. healthchecks.io emails. |

## Changing a slot

1. Edit `DISPATCH_SLOTS` in `.github/workflows/forecast-pipeline.yml`.
2. Move the matching fallback cron, keeping it 20 minutes after the slot.
3. Update the `github.event.schedule == '…'` comparisons in the full-pipeline job's `if:`.
4. Update this file.
5. Edit the matching cron-job.org job.

`pipeline/tests/test_dispatch_gate.py` fails until the workflow and this file agree. It cannot
see cron-job.org, so step 5 is up to you.
