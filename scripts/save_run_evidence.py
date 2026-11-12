#!/usr/bin/env python3
"""Save a run's files under docs/face_validation: gzipped from GZIP_FROM_BYTES, with checksums.

WHY. The saved run evidence had grown to 25 MB, most of it artifact JSON saved byte for byte
(#313 alone added about 93,000 lines). The evidence is kept; only how it is stored changes.

THE RULE, for every file saved under docs/face_validation:
  * a file of GZIP_FROM_BYTES (100 KB) or more is stored as NAME.gz, gzip level 9 with no name
    and no time in the header, so the same file always gives the same bytes;
  * a smaller file is stored as it is;
  * either way the sha256 of the ORIGINAL file is recorded in docs/face_validation/
    CHECKSUMS.sha256 as "<sha256>  <path under docs/face_validation, original name>", so
    `gunzip -c NAME.gz | sha256sum` can be checked against the artifact downloaded again by its
    run id. A README names the run, the artifact and its zip's sha256.

    python3 scripts/save_run_evidence.py docs/face_validation/run-72 buoy_assign.json ...
    python3 scripts/save_run_evidence.py --verify        # every checksum against its file

Standard library only. pipeline/tests/test_face_validation_evidence.py holds the rule.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "face_validation"
CHECKSUMS = EVIDENCE / "CHECKSUMS.sha256"
GZIP_FROM_BYTES = 100 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_bytes(data: bytes) -> bytes:
    """Gzip at level 9 with mtime 0 and no file name: the same input gives the same output."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def read_checksums(path: Path = CHECKSUMS) -> dict[str, str]:
    """{relative path: sha256} from the checksum file, in file order."""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, rel = line.split("  ", 1)
                out[rel] = digest
    return out


def write_checksums(sums: dict[str, str], path: Path = CHECKSUMS) -> None:
    path.write_text("".join(f"{d}  {rel}\n" for rel, d in sorted(sums.items())), encoding="utf-8")


def save(dest_dir: Path, src: Path, evidence: Path = EVIDENCE, checksums: Path = CHECKSUMS) -> Path:
    """Store *src* in *dest_dir* by the rule; record its sha256. Returns the path written."""
    data = src.read_bytes()
    dest_dir.mkdir(parents=True, exist_ok=True)
    if len(data) >= GZIP_FROM_BYTES:
        out = dest_dir / (src.name + ".gz")
        out.write_bytes(gzip_bytes(data))
    else:
        out = dest_dir / src.name
        if src.resolve() != out.resolve():
            shutil.copyfile(src, out)
    sums = read_checksums(checksums)
    sums[(dest_dir / src.name).relative_to(evidence).as_posix()] = sha256_bytes(data)
    write_checksums(sums, checksums)
    return out


def stored(evidence: Path, rel: str) -> Path | None:
    """The file holding *rel*: rel itself, or rel.gz."""
    for p in (evidence / rel, evidence / (rel + ".gz")):
        if p.exists():
            return p
    return None


def verify(evidence: Path = EVIDENCE, checksums: Path = CHECKSUMS) -> list[str]:
    """One line per checksum that does not hold: a missing file or a different sha256."""
    bad = []
    for rel, digest in read_checksums(checksums).items():
        p = stored(evidence, rel)
        if p is None:
            bad.append(f"{rel}: missing")
            continue
        data = p.read_bytes()
        if p.suffix == ".gz" and not rel.endswith(".gz"):
            data = gzip.decompress(data)
        if sha256_bytes(data) != digest:
            bad.append(f"{rel}: sha256 {sha256_bytes(data)}, recorded {digest}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", nargs="?", help="the run's folder under docs/face_validation")
    ap.add_argument("files", nargs="*", help="files to save there")
    ap.add_argument("--verify", action="store_true", help="check every recorded sha256")
    a = ap.parse_args(argv)
    if a.verify:
        bad = verify()
        for line in bad:
            print(f"::error::{line}")
        print(f"{len(read_checksums())} checksums, {len(bad)} not holding")
        return 1 if bad else 0
    if not a.dest or not a.files:
        ap.error("give the run's folder and at least one file, or --verify")
    for f in a.files:
        out = save(Path(a.dest).resolve(), Path(f))
        print(f"saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
