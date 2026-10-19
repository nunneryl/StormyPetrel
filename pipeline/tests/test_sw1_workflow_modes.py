"""sw1-raycast.yml: mode=nwps-shelter runs only the read-only measurement job.

Read as text: PyYAML is not among the pipeline's requirements. Each job is the block under
`jobs:` that starts at two spaces of indent.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sw1-raycast.yml"


def _jobs():
    text = WORKFLOW.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z0-9-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


def _if(job):
    return re.search(r"(?m)^    if: (.+)$", job).group(1).strip()


def test_nwps_shelter_is_a_mode():
    text = WORKFLOW.read_text(encoding="utf-8")
    options = text.split("options:", 1)[1].split("workers:", 1)[0]
    assert re.findall(r"- ([a-z-]+)", options) == [
        "validate", "full", "star-preview", "tide-preview", "nwps-shelter"]


def test_only_the_measurement_job_runs_for_nwps_shelter():
    jobs = _jobs()
    assert set(jobs) == {"raycast", "star-preview", "tide-preview", "nwps-shelter"}
    assert _if(jobs["nwps-shelter"]) == "${{ inputs.mode == 'nwps-shelter' }}"
    assert _if(jobs["raycast"]) == (
        "${{ inputs.mode != 'star-preview' && inputs.mode != 'tide-preview' "
        "&& inputs.mode != 'nwps-shelter' }}")
    assert _if(jobs["star-preview"]) == "${{ inputs.mode == 'star-preview' }}"
    assert _if(jobs["tide-preview"]) == "${{ inputs.mode == 'tide-preview' }}"


def test_the_measurement_job_only_reads():
    job = _jobs()["nwps-shelter"]
    assert "    permissions:\n      contents: read\n" in job
    assert "python -m pipeline.star_preview --nwps-shelter \\\n" in job
    assert '--baseline "$RUNNER_TEMP/baseline.json" --out nwps_shelter.json' in job
    assert "SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_measurement_reads_the_baseline_ref():
    job = _jobs()["nwps-shelter"]
    assert "BASELINE_REF: ${{ inputs.baseline_ref }}" in job
    assert 'git show "$base:pipeline/spots_enriched.json" > "$RUNNER_TEMP/baseline.json"' in job
