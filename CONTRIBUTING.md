# Contributing to PyIDM

Thanks for helping out. This repo keeps `main` green and releasable at all
times; these rules exist so that stays true.

## Branch protection: `main` accepts PRs only

Direct pushes to `main` are rejected — a fresh commit cannot have passing
checks yet, so the protection hook declines it. The only path in is:

1. **Branch** from the latest `main`:
   ```bash
   git checkout main && git pull
   git checkout -b <branch-name>
   ```
2. **Commit** your work. Keep commits focused; the repo history favors
   descriptive messages over `Update` / `Fix`.
3. **Push the branch** and **open a PR** against `main`.
4. **Wait for all six required checks** (see below). The PR cannot merge
   until every one is green.
5. **Merge** (merge commits are used to keep feature history). **Delete the
   branch** afterwards — both the remote and your local copy.

## Branch naming

Descriptive and lowercase, one branch per change. Existing examples:
`readme-badges`, `dependabot-config`. Dependabot uses its own scheme
(`dependabot/pip/…`, `dependabot/github_actions/…`).

- Features: `feat-…` / descriptive name (`subtitle-cache`, `gui-drag-drop`)
- Fixes: `fix-…` plus the symptom (`fix-vacuum-flaky-test`)
- Docs/config/docs-only: a plain descriptive name (`readme-badges`,
  `dependabot-config`)

## The six required checks

CI runs on every PR — the only way into `main`, since branch protection
rejects direct pushes. All six must pass before merge; the lint
jobs fail fast so you can stop a doomed run early. To reproduce locally
(Python 3.12):

```bash
pip install -e ".[dev]"
ruff check .
mypy                      # py39 target, win32 platform — see [tool.mypy]
coverage run -m pytest tests     # full suite; also enforces fail_under=80 on idm/
vermin -t=3.9- --violations --no-tips idm tests bench portable_zip.py
```

| # | Check | What it does |
|---|-------|--------------|
| 1 | `pytest (py3.9)` | full suite on the oldest supported floor |
| 2 | `pytest (py3.10)` | full suite |
| 3 | `pytest (py3.11)` | full suite |
| 4 | `pytest (py3.12)` | full suite + coverage gate (`fail_under=80` on `idm/`) |
| 5 | `ruff + mypy (py39 target)` | lint (`target-version py39`) + static types (`platform = "win32"`, py39 syntax target) |
| 6 | `ruff + stdlib drift (py3.9 interpreter)` | vermin fails on any stdlib API newer than 3.9 |

### The 3.9 floor: two special rules

PyIDM supports Python 3.9–3.12. Ruff's `py39` target guards **syntax**, but
not stdlib *signatures* — `Path.write_text(..., newline=)` looks fine and is
3.10-only. Two guards exist for this:

- **vermin** (check 6) fails on any stdlib API requiring > 3.9.
- **[tests/test_py39_write_text.py](tests/test_py39_write_text.py)** is an
  AST-level guard test: it fails on any `.write_text(newline=)` /
  `.read_text(newline=)` call. Use
  `idm.utils.write_text_newlines()` instead — byte-exact line endings on
  every version.

If a change genuinely needs a newer API, gate it explicitly with
`sys.version_info` and provide the 3.9 path — then both guards stay green.

## Commits

- Imperative mood, descriptive subject; explain the *why* in the body when
  it isn't obvious.
- One logical change per PR. Version bumps, refactors, and behavior changes
  don't ride along together.

## Releases

Releases are tag-driven, not manual:

1. The tag must match the software version — this is **enforced**, not
   convention. A `v*` tag push runs the tag-check workflow, and the
   Release workflow refuses to build or publish until
   `scripts/check_tag_version.py` passes; `__version__` and
   `pyproject.toml` are pinned together by
   [tests/test_version_consistency.py](tests/test_version_consistency.py).
   (This bit us once: `v1.11.53` was tagged against 1.11.77 code and had
   to be deleted.) The tag check fires only on tag pushes — it is
   deliberately *not* one of the six required checks, which gate PR
   merges into `main`.
2. Tag from a fully green `main`:
   ```bash
   git tag -a v<version> -m "PyIDM <version>"
   git push origin v<version>
   ```
3. The Release workflow builds the portable bundle from source, **smoke-tests
   the fresh bundle**, asserts the stable `PyIDM/` zip root, uploads the
   artifact, and publishes the GitHub release with the zip attached. Nothing
   is published unless the build passes its own smoke test.
4. Test-tag first if unsure: `workflow_dispatch` on the Release workflow runs
   build + smoke test **without** publishing.
5. After a release publishes, a `sync-repo-zip` job automatically commits the
   exact published zip back to `portable/PyIDM-portable.zip` (with a
   regenerated `.sha256`) via a PR that auto-merges once the six required
   checks pass — the repo copy always tracks the latest release, with no
   manual step. (Requires the repo's "Allow auto-merge" setting, which is
   enabled.) See [REPRODUCIBLE-BUILDS.md](REPRODUCIBLE-BUILDS.md) for the
   sha256 verification recipe (release asset ↔ repo copy ↔ rebuild).

## Dependency updates

Dependabot opens PRs that follow the exact same branch → PR → six-checks →
merge flow (`.github/dependabot.yml`):

- **pip** (weekly): dev-dependency minor+patch bumps are grouped into one
  PR; majors arrive individually.
- **github-actions** (weekly): patch-level bumps grouped; majors — e.g.
  `actions/checkout` v4 → v7 — arrive as individual PRs because they can
  break workflows and deserve their own scrutiny.
- **security fixes**: immediate, whenever a vulnerability alert fires.

Treat these like any other PR: review the changelog, let the six checks
run, merge or close. Heads-up: a batch of Dependabot PRs at once can
queue behind the free tier's ~20 concurrent CI jobs, so checks may take
longer than usual — that's congestion, not failure.

## Docs to keep in sync

Some doc claims only stay true while the infrastructure around them stays
put. Change any of these, and update the matching docs in the same PR:

- **The required-check set** (add, remove, or rename a check): grep the docs
  for `required checks` — the intro step, the check table, the 3.9-floor
  note, Releases, and Dependabot here, plus the sync-PR section of
  REPRODUCIBLE-BUILDS.md. Update the count and the table rows together.
- **Schedules** (probe cron, Dependabot groups): grep the docs for `weekly`
  — the Dependabot section here and the repro row of REPRODUCIBLE-BUILDS.md's
  enforcement table; repro.yml's header comment names the exact day/time.
- **The embed-zip `VER`**: docs must not name the patch —
  [tests/test_doc_embed_zip_version.py](tests/test_doc_embed_zip_version.py)
  fails CI if they do, and REPRODUCIBLE-BUILDS.md uses the parameterized
  `python-<VER>-embed-amd64.zip` form. Do move the *minor* `3.12` where it
  describes tooling: the `setup-python` inputs in release.yml (both jobs),
  ci.yml's matrix, the prerequisites section of REPRODUCIBLE-BUILDS.md, and
  the local-repro note here.
