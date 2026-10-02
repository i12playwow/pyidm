"""Zip the portable PyIDM bundle deterministically.

Usage: python portable_zip.py <bundle_dir> <zip_path>

`downloads` folders inside the bundle hold the user's own downloads;
they are data, not application, and never belong in the distributable.
Entries are archived under a stable `<bundle-name>/` root (not the build
machine's absolute path), so the zip extracts identically everywhere.

Determinism: two runs over the same bundle tree produce byte-identical
zips. Everything the ZIP format records is pinned:

* entries are written in sorted order (filesystem walk order is not),
* every timestamp is the ZIP epoch (1980-01-01 00:00:00) instead of the
  file's mtime,
* create_system and permissions are fixed (unix / 0644) instead of being
  taken from the building machine's stat(),

so future repo-copy syncs against a release asset are verifiable by
sha256. The one residual variable is the zlib build compressing the
deflate streams: identical CPython builds (e.g. CI's windows-latest
3.12.x and a local 3.12.x) produce identical bytes; a different zlib
version may not.
"""
from __future__ import annotations

import os
import sys
import zipfile

EXCLUDED_DIRS = {"downloads", "__pycache__"}
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)  # smallest timestamp a DOS zip can hold


def deterministic_info(arcname: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3  # unix, so external_attr reads as 0644
    zi.external_attr = 0o100644 << 16
    return zi


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: portable_zip.py <bundle_dir> <zip_path>", file=sys.stderr)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    top = os.path.basename(os.path.normpath(src)) or "bundle"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for walk_root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
            for f in sorted(files):
                p = os.path.join(walk_root, f)
                arcname = f"{top}/{os.path.relpath(p, src)}".replace(os.sep, "/")
                with open(p, "rb") as fh:
                    z.writestr(deterministic_info(arcname), fh.read())
    with zipfile.ZipFile(dst) as z:
        n = len(z.namelist())
    print(f"{dst}: {n} files, {os.path.getsize(dst) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
