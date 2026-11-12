"""docs/face_validation: run evidence stored gzipped from 100 KB, every checksum holding.

scripts/save_run_evidence.py saves a run's files; this holds the folder to its rule and the script
to what it does. The sha256 values are written out here: sha256("abc") is the FIPS 180-2 test
vector, and run 71's was taken with `sha256sum` on the artifact's file when it was downloaded.
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import save_run_evidence as S  # noqa: E402

EVIDENCE = ROOT / "docs" / "face_validation"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --------------------------------------------------------------------------- #
# The folder                                                                   #
# --------------------------------------------------------------------------- #
def test_no_uncompressed_file_of_100_kb_or_more():
    big = sorted(str(p.relative_to(EVIDENCE)) for p in EVIDENCE.rglob("*")
                 if p.is_file() and p.suffix != ".gz" and p.stat().st_size >= 100 * 1024)
    assert big == [], ("save these with scripts/save_run_evidence.py, which gzips them: "
                       + ", ".join(big))


def test_every_recorded_checksum_holds():
    assert S.verify() == []


def test_the_eighteen_files_gzipped_on_2026_11_12_are_recorded():
    sums = S.read_checksums()
    assert len(sums) >= 18
    # run 71's node_preview.json, as downloaded from artifact node-preview-71 (sha256sum then)
    assert sums["run-71/node_preview.json"] == \
        "790b43c98d23182a2bb8018316b66938669525f442c65bb88856836ec4b8f27c"
    for rel in sums:
        assert (EVIDENCE / (rel + ".gz")).exists() or (EVIDENCE / rel).exists(), rel


def test_each_gzipped_file_is_named_in_its_readme():
    for rel in S.read_checksums():
        top = rel.split("/")[0]
        readme = (EVIDENCE / top / "README.md").read_text(encoding="utf-8")
        assert "Stored gzipped since 2026-11-12" in readme, top


# --------------------------------------------------------------------------- #
# The script                                                                   #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def world(tmp_path):
    ev = tmp_path / "face_validation"
    ev.mkdir()
    return ev, ev / "CHECKSUMS.sha256"


def test_a_small_file_is_copied_and_its_checksum_recorded(world, tmp_path):
    ev, sums = world
    src = tmp_path / "small.json"
    src.write_bytes(b"abc")
    out = S.save(ev / "run-99", src, ev, sums)
    assert out == ev / "run-99" / "small.json" and out.read_bytes() == b"abc"
    assert sums.read_text() == f"{ABC_SHA256}  run-99/small.json\n"
    assert S.verify(ev, sums) == []


def test_a_file_of_100_kb_is_gzipped_deterministically(world, tmp_path):
    ev, sums = world
    src = tmp_path / "big.json"
    data = b"a" * (100 * 1024)                               # exactly the threshold
    src.write_bytes(data)
    out = S.save(ev / "run-99", src, ev, sums)
    assert out.name == "big.json.gz" and not (ev / "run-99" / "big.json").exists()
    assert gzip.decompress(out.read_bytes()) == data
    # gzip header: no file name flag, mtime 0
    head = out.read_bytes()[:10]
    assert head[:3] == b"\x1f\x8b\x08" and head[3] == 0 and head[4:8] == b"\x00\x00\x00\x00"
    first = out.read_bytes()
    S.save(ev / "run-99", src, ev, sums)
    assert out.read_bytes() == first                          # same file, same bytes
    assert S.verify(ev, sums) == []


def test_one_byte_under_the_threshold_is_not_gzipped(world, tmp_path):
    ev, sums = world
    src = tmp_path / "under.json"
    src.write_bytes(b"a" * (100 * 1024 - 1))
    assert S.save(ev / "run-99", src, ev, sums).name == "under.json"


def test_saving_again_replaces_the_line_and_the_file_is_sorted(world, tmp_path):
    ev, sums = world
    a, b = tmp_path / "b.json", tmp_path / "a.json"
    a.write_bytes(b"one")
    b.write_bytes(b"abc")
    S.save(ev / "run-99", a, ev, sums)
    S.save(ev / "run-99", b, ev, sums)
    a.write_bytes(b"abc")
    S.save(ev / "run-99", a, ev, sums)
    assert sums.read_text() == (f"{ABC_SHA256}  run-99/a.json\n"
                                f"{ABC_SHA256}  run-99/b.json\n")


def test_verify_names_a_changed_or_missing_file(world, tmp_path):
    ev, sums = world
    src = tmp_path / "x.json"
    src.write_bytes(b"abc")
    out = S.save(ev / "run-99", src, ev, sums)
    out.write_bytes(b"abd")
    bad = S.verify(ev, sums)
    assert len(bad) == 1 and bad[0].startswith("run-99/x.json: sha256 ") \
        and bad[0].endswith(f", recorded {ABC_SHA256}")
    out.unlink()
    assert S.verify(ev, sums) == ["run-99/x.json: missing"]


def test_main_verifies_and_needs_files(capsys):
    assert S.main(["--verify"]) == 0
    assert capsys.readouterr().out.endswith(" checksums, 0 not holding\n")
    with pytest.raises(SystemExit):
        S.main(["docs/face_validation/run-99"])


def test_main_verify_fails_with_one_line_per_problem(monkeypatch, capsys):
    monkeypatch.setattr(S, "verify", lambda: ["run-99/x.json: missing"])
    assert S.main(["--verify"]) == 1
    assert capsys.readouterr().out.startswith("::error::run-99/x.json: missing\n")
