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
        "data-quality", "node-preview", "surf-preview", "period-preview", "nwps-cells",
        "tide-station", "buoy-assign", "nwps-blank"]


def test_only_the_measurement_job_runs_for_nwps_shelter():
    jobs = _jobs()
    assert set(jobs) == {"raycast", "star-preview", "tide-preview", "nwps-shelter",
                         "rating-preview", "data-quality", "node-preview", "surf-preview",
                         "period-preview", "nwps-cells", "tide-station", "buoy-assign",
                         "nwps-blank"}
    assert _if(jobs["nwps-shelter"]) == "${{ inputs.mode == 'nwps-shelter' }}"
    assert _if(jobs["raycast"]) == (
        "${{ inputs.mode != 'star-preview' && inputs.mode != 'tide-preview' "
        "&& inputs.mode != 'nwps-shelter' && inputs.mode != 'rating-preview' "
        "&& inputs.mode != 'data-quality' && inputs.mode != 'node-preview' "
        "&& inputs.mode != 'surf-preview' && inputs.mode != 'period-preview' "
        "&& inputs.mode != 'nwps-cells' && inputs.mode != 'tide-station' "
        "&& inputs.mode != 'buoy-assign' && inputs.mode != 'nwps-blank' }}")
    assert _if(jobs["star-preview"]) == "${{ inputs.mode == 'star-preview' }}"
    assert _if(jobs["tide-preview"]) == "${{ inputs.mode == 'tide-preview' }}"
    assert _if(jobs["rating-preview"]) == "${{ inputs.mode == 'rating-preview' }}"


def test_the_nwps_cells_job_only_reads():
    job = _jobs()["nwps-cells"]
    assert _if(job) == "${{ inputs.mode == 'nwps-cells' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ('python -m pipeline.nwps_cells --spots "$SPOTS" --out nwps_cells.json '
            '--csv nwps_cells.csv') in job
    assert "SPOTS: ${{ inputs.spots }}" in job and "libeccodes0" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_nwps_blank_job_only_reads():
    job = _jobs()["nwps-blank"]
    assert _if(job) == "${{ inputs.mode == 'nwps-blank' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ('python -m pipeline.nwps_blank --spots "$SPOTS" --out nwps_blank.json '
            '--csv nwps_blank.csv') in job
    assert "SPOTS: ${{ inputs.spots }}" in job and "libeccodes0" in job
    assert "timeout-minutes: 150" in job
    assert "name: nwps-blank-${{ github.run_number }}" in job
    rest = job.replace("inputs.mode == 'nwps-blank'", "").replace("${{ inputs.spots }}", "")
    for verb in ("git push", "git commit", "db_import", "--write", "inputs."):
        assert verb not in rest, verb


def test_the_tide_station_job_only_reads():
    job = _jobs()["tide-station"]
    assert _if(job) == "${{ inputs.mode == 'tide-station' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert 'python -m pipeline.tide_station --spots "$SPOTS" --out tide_station.json' in job
    assert "SPOTS: ${{ inputs.spots }}" in job
    for verb in ("git push", "git commit", "db_import", "--write", "SUPABASE"):
        assert verb not in job, verb


def test_the_buoy_assign_job_only_reads():
    job = _jobs()["buoy-assign"]
    assert _if(job) == "${{ inputs.mode == 'buoy-assign' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ('python -m pipeline.buoy_assign --out buoy_assign.json --spots "$SPOTS" '
            '--recent-days 7') in job
    assert "SPOTS: ${{ inputs.spots }}" in job
    assert '--fresh-xml "$RUNNER_TEMP/ndbc/ndbc_stations.xml"' in job
    assert "buoy_assign_recent_wave.json" in job
    assert "uses: actions/cache/restore@v6" in job       # restores the geodata cache, never saves it
    assert "name: buoy-assign-${{ github.run_number }}" in job
    rest = job.replace("inputs.mode == 'buoy-assign'", "").replace("${{ inputs.spots }}", "")
    for verb in ("git push", "git commit", "db_import", "--write", "SUPABASE", "inputs."):
        assert verb not in rest, verb


def test_full_mode_lists_recent_wave_stations_before_enrich():
    job = _jobs()["raycast"]
    listing = job.index("python -m pipeline.ndbc_recent_wave --out pipeline/geodata/ndbc_recent_wave.json")
    assert listing < job.index("python -m pipeline.enrich --skip-raycast")


def test_full_mode_refreshes_ndbcs_files_before_the_list_and_enrich():
    # The cached copy kept 46453, silent since 2026-09-28, as a wave station (run 65).
    job = _jobs()["raycast"]
    xml = job.index('mv "$RUNNER_TEMP/ndbc/ndbc_stations.xml" pipeline/geodata/ndbc_stations.xml')
    obs = job.index('mv "$RUNNER_TEMP/ndbc/ndbc_latest_obs.txt" pipeline/geodata/ndbc_latest_obs.txt')
    listing = job.index("python -m pipeline.ndbc_recent_wave --out pipeline/geodata/ndbc_recent_wave.json")
    assert max(xml, obs) < listing < job.index("python -m pipeline.enrich --skip-raycast")
    start = job.index("- name: Download NDBC's station list and latest observations for this run")
    step = job[start:job.index("- name:", start + 1)]
    assert "if: ${{ inputs.mode == 'full' && inputs.then_enrich }}" in step
    assert "set -euo pipefail" in step
    assert step.count("curl -fsSL --retry 4 --retry-delay 5") == 2


def _refresh_step():
    job = _jobs()["raycast"]
    start = job.index("- name: Download NDBC's station list and latest observations for this run")
    return job, job[start:job.index("- name:", start + 1)]


def _refresh_script():
    _job, step = _refresh_step()
    lines = []
    for ln in step.split("run: |\n", 1)[1].splitlines():
        if ln.strip() and not ln.startswith(" " * 10):
            break                           # the next step's comments
        lines.append(ln[10:])
    return "\n".join(lines) + "\n"


def test_a_failed_refresh_stops_the_job_and_nothing_after_it_runs():
    job, step = _refresh_step()
    assert "continue-on-error" not in job
    assert '|| fail "Could not download NDBC\'s activestations.xml."' in step
    assert '|| fail "Could not download NDBC\'s latest_obs.txt."' in step
    check = step.index("python -m pipeline.ndbc_fresh")
    assert check < step.index('mv "$RUNNER_TEMP/ndbc/ndbc_stations.xml"')
    assert check < step.index('mv "$RUNNER_TEMP/ndbc/ndbc_latest_obs.txt"')
    # The steps that would use the files run only when every step before them passed.
    after = job[job.index(step) + len(step):job.index("- name: Push the review branch")]
    nxt = job.index("- name:", job.index("- name: Push the review branch") + 1)
    for chunk in (after, job[job.index("- name: Push the review branch"):nxt]):
        for cond in re.findall(r"(?m)^        if: (.+)$", chunk):
            assert "always()" not in cond and "failure()" not in cond, cond


# The step's own script, run with a stand-in curl. Each case starts from cached files that say
# CACHED; a failed or broken download must leave them as they were and exit 1 with an ::error::.
_GOOD_XML = "<stations>" + "".join(
    f'<station id="s{i}" lat="{i % 80}.5" lon="-{i % 170}.5" name="n{i}"/>' for i in range(500)
) + "</stations>\n"
_GOOD_OBS = ("#STN LAT LON WVHT\n#text deg deg m\n"
             + "".join(f"s{i} 1.0 2.0 {'MM' if i % 2 else '1.2'}\n" for i in range(200)))


def _run_refresh(tmp_path, curl_body):
    import os
    import subprocess
    import sys
    work, bin_, temp = tmp_path / "work", tmp_path / "bin", tmp_path / "temp"
    (work / "pipeline" / "geodata").mkdir(parents=True)
    bin_.mkdir(); temp.mkdir()
    for f in ("ndbc_stations.xml", "ndbc_latest_obs.txt"):
        (work / "pipeline" / "geodata" / f).write_text("CACHED\n")
    (tmp_path / "good.xml").write_text(_GOOD_XML)
    (tmp_path / "good.txt").write_text(_GOOD_OBS)
    curl = bin_ / "curl"
    curl.write_text("#!/usr/bin/env bash\nout=\nwhile [[ $# -gt 0 ]]; do\n"
                    '  if [[ "$1" == -o ]]; then out="$2"; shift; fi; url="$1"; shift\ndone\n'
                    + curl_body.format(good=tmp_path))
    curl.chmod(0o755)
    (bin_ / "python").symlink_to(sys.executable)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "RUNNER_TEMP": str(temp),
           "PYTHONPATH": str(WORKFLOW.parents[2])}
    r = subprocess.run(["bash", "-c", _refresh_script()], cwd=work, env=env,
                       capture_output=True, text=True)
    cached = [(work / "pipeline" / "geodata" / f).read_text()
              for f in ("ndbc_stations.xml", "ndbc_latest_obs.txt")]
    return r, cached


def test_a_download_that_fails_stops_the_run_and_keeps_nothing(tmp_path):
    r, cached = _run_refresh(tmp_path, 'echo "curl: (22) The requested URL returned error: 503" >&2\nexit 22\n')
    assert r.returncode == 1
    assert "::error::Could not download NDBC's activestations.xml. The full run stops here" in r.stdout
    assert cached == ["CACHED\n", "CACHED\n"]


def test_a_200_with_the_wrong_body_stops_the_run_and_keeps_nothing(tmp_path):
    r, cached = _run_refresh(tmp_path, 'echo "<html>Service unavailable</html>" > "$out"\n')
    assert r.returncode == 1
    assert "::error::NDBC activestations.xml lists 0 stations with coordinates" in r.stdout
    assert "::error::NDBC latest_obs.txt has no header row naming STN and WVHT." in r.stdout
    assert "::error::NDBC's files downloaded but do not look right (see above)." in r.stdout
    assert cached == ["CACHED\n", "CACHED\n"]


def test_good_files_replace_the_cached_copies(tmp_path):
    body = ('case "$url" in *activestations*) cp {good}/good.xml "$out";; '
            '*) cp {good}/good.txt "$out";; esac\n')
    r, cached = _run_refresh(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ndbc_fresh: both NDBC files look right" in r.stdout
    assert cached == [_GOOD_XML, _GOOD_OBS]


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


def test_the_node_preview_only_reads():
    job = _jobs()["node-preview"]
    assert _if(job) == "${{ inputs.mode == 'node-preview' }}"
    assert "    permissions:\n      contents: read\n      actions: read " in job
    assert "write" not in job.split("    permissions:")[1].split("    env:")[0]
    assert 'python -m pipeline.node_preview --baseline "$RUNNER_TEMP/baseline.json" \\\n' in job
    assert ('--reference "$SPOTS" --out node_preview.json --csv node_preview_hours.csv \\\n'
            '            --logger-log "$RUNNER_TEMP/nwps_pub.log"') in job
    # the double-post rule's evidence: the publication logger's last 12 successful runs, read-only
    assert "GH_TOKEN: ${{ github.token }}" in job
    assert ("gh run list --repo \"$GITHUB_REPOSITORY\" --workflow nwps-publication-log.yml \\\n"
            "                  --status success --limit 12 --json databaseId") in job
    assert 'gh run view "$id" --repo "$GITHUB_REPOSITORY" --log >> "$RUNNER_TEMP/nwps_pub.log" || true' in job
    assert job.index("--logger-log") > job.index("gh run view")
    assert "SPOTS: ${{ inputs.spots }}" in job
    assert "BASELINE_REF: ${{ inputs.baseline_ref }}" in job
    assert 'git show "$base:pipeline/spots_enriched.json" > "$RUNNER_TEMP/baseline.json"' in job
    assert "libeccodes0" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_surf_preview_only_reads():
    job = _jobs()["surf-preview"]
    assert _if(job) == "${{ inputs.mode == 'surf-preview' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ("python -m pipeline.surf_preview --out surf_preview.json --csv surf_preview_hours.csv \\\n"
            "            --compare-out face_compare.json --srf-prefix face_compare") in job
    assert "SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}" in job
    assert "name: surf-preview-${{ github.run_number }}" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb


def test_the_period_preview_only_reads():
    job = _jobs()["period-preview"]
    assert _if(job) == "${{ inputs.mode == 'period-preview' }}"
    assert "    permissions:\n      contents: read\n" in job
    assert ("python -m pipeline.period_preview --out period_preview.json --csv period_preview_hours.csv \\\n"
            "            --archive-csv period_preview_archive.csv --compare-out face_compare.json") in job
    assert "name: period-preview-${{ github.run_number }}" in job
    for verb in ("git push", "git commit", "db_import", "--write"):
        assert verb not in job, verb
