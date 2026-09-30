"""Zip the portable PyIDM bundle, excluding user data.

Usage: python portable_zip.py <bundle_dir> <zip_path>

`downloads` folders inside the bundle hold the user's own downloads;
they are data, not application, and never belong in the distributable.
Entries are archived under a stable `<bundle-name>/` root (not the build
machine's absolute path), so the zip extracts identically everywhere.
"""
from __future__ import annotations

import os
import sys
import zipfile

EXCLUDED_DIRS = {"downloads", "__pycache__"}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: portable_zip.py <bundle_dir> <zip_path>", file=sys.stderr)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    top = os.path.basename(os.path.normpath(src)) or "bundle"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for walk_root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for f in files:
                p = os.path.join(walk_root, f)
                arcname = f"{top}/{os.path.relpath(p, src)}".replace(os.sep, "/")
                z.write(p, arcname)
    with zipfile.ZipFile(dst) as z:
        n = len(z.namelist())
    print(f"{dst}: {n} files, {os.path.getsize(dst) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
