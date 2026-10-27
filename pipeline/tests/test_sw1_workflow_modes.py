"""sw1-raycast.yml: mode=nwps-shelter and mode=rating-preview each run only their read-only job.

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
        "validate", "full", "star-preview", "tide-preview", "nwps-shelter", "rating-preview",
        "data-quality", "node-preview", "face-sources", "face-calibration", "face-followup", "face-seaward",
        "face-compare"]


def test_only_the_measurement_job_runs_for_nwps_shelter():
    jobs = _jobs()
    assert set(jobs) == {"raycast", "star-preview", "tide-preview", "nwps-shelter",
                         "rating-preview", "data-quality", "node-preview", "face-sources", "face-calibration",
                         "face-followup", "face-seaward", "face-compare"}
    assert _if(jobs["nwps-shelter"]) == "${{ inputs.mode == 'nwps-shelter' }}"
    assert _if(jobs["raycast"]) == (
        "${{ inputs.mode != 'star-preview' && inputs.mode != 'tide-preview' "
        "&& inputs.mode != 'nwps-shelter' && inputs.mode != 'rating-preview' "
        "&& inputs.mode != 'data-quality' && inputs.mode != 'node-preview' "
        "&& inputs.mode != 'face-sources' "
        "&& inputs.mode != 'face-calibration' && inputs.mode != 'face-followup' "
        "&& inputs.mode != 'face-seaward' && inputs.mode != 'face-compare' }}")
    assert _if(jobs["star-preview"]) == "${{ inputs.mode == 'star-preview' }}"
    assert _if(jobs["tide-preview"]) == "${{ inputs.mode == 'tide-preview' }}"
    assert _if(jobs["rating-preview"]) == "${{ inputs.mode == 'rating-preview' }}"


def test_the_rating_preview_only_reads():
    job = _jobs()["rating-preview"]
    assert "    permissions:\n      contents: read\n" in job
    assert "python -m pipeline.star_preview --onshore blown-2.5,blown-2.5-measured \\\n" in job
    assert "--since 2026-10-23T00:00:00+00:00 \\\n" in job
    assert '--baseline "$RUNNER_TEMP/baseline.json" --out rating_preview.json' in job
    assert "BASELINE_REF: ${{ inputs.baseline_ref }}" in job
    assert "SPOTS: ${{ inputs.spots }}" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


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


def test_at_most_ten_dispatch_inputs():
    """GitHub has long documented ten as the most workflow_dispatch inputs; more and no mode
    can be dispatched. Ten is the count today."""
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.split("    inputs:\n", 1)[1].split("\nenv:", 1)[0]
    names = re.findall(r"(?m)^      ([a-z_]+):$", block)
    assert names == ["mode", "workers", "debug_blockers", "sweep_mainland_solid", "spots",
                     "then_enrich", "then_preview", "arcs_ref", "baseline_ref", "tide_modes"]


def test_the_data_quality_sweep_only_reads():
    job = _jobs()["data-quality"]
    assert _if(job) == "${{ inputs.mode == 'data-quality' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert "python -m pipeline.data_quality --sections hrrr,true-north,wind-preview,partitions,tides,cams \\\n" in job
    assert "SPOTS: ${{ inputs.spots }}" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_face_sources_check_only_reads_and_holds_no_secrets():
    job = _jobs()["face-sources"]
    assert _if(job) == "${{ inputs.mode == 'face-sources' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert "python -m pipeline.face_sources --out face_sources.json" in job
    assert "secrets." not in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_face_calibration_only_reads_and_keeps_its_results():
    job = _jobs()["face-calibration"]
    assert _if(job) == "${{ inputs.mode == 'face-calibration' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert "python -m pipeline.face_calibration --since 2025-06-01T00:00:00+00:00 \\\n" in job
    assert "--out face_calibration.json --csv face_calibration_pairs.csv \\\n" in job
    assert "--sheltered-csv face_sheltered.csv" in job
    assert "            face_sheltered.csv\n" in job                      # kept in the artifact
    assert "retention-days: 90" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_face_followup_only_reads_and_keeps_its_results():
    job = _jobs()["face-followup"]
    assert _if(job) == "${{ inputs.mode == 'face-followup' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert "run: python -m pipeline.face_followup --out face_followup.json --csv-dir face_followup" in job
    assert "key: geodata-v1-${{ hashFiles('pipeline/download_geodata.sh') }}" in job   # the raycast's cache
    assert "libeccodes0" in job and "retention-days: 90" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_face_seaward_run_only_reads_and_keeps_its_results():
    job = _jobs()["face-seaward"]
    assert _if(job) == "${{ inputs.mode == 'face-seaward' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ("run: python -m pipeline.face_followup --sections deeper --out face_seaward.json "
            "--csv-dir face_seaward") in job
    assert "key: geodata-v1-${{ hashFiles('pipeline/download_geodata.sh') }}" in job
    assert "libeccodes0" in job and "retention-days: 90" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_node_preview_only_reads():
    job = _jobs()["node-preview"]
    assert _if(job) == "${{ inputs.mode == 'node-preview' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert 'python -m pipeline.node_preview --baseline "$RUNNER_TEMP/baseline.json" \\\n' in job
    assert "--out node_preview.json --csv node_preview_hours.csv" in job
    assert "BASELINE_REF: ${{ inputs.baseline_ref }}" in job
    assert 'git show "$base:pipeline/spots_enriched.json" > "$RUNNER_TEMP/baseline.json"' in job
    assert "libeccodes0" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_face_compare_run_only_reads_and_keeps_its_results():
    job = _jobs()["face-compare"]
    assert _if(job) == "${{ inputs.mode == 'face-compare' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ("run: python -m pipeline.face_compare --out face_compare.json "
            "--csv-prefix face_compare") in job
    assert "SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}" in job
    assert "retention-days: 90" in job and "face_compare_*.csv" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb
