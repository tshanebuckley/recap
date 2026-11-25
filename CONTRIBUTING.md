# Contributing to recap

recap: Reconstruction of Cellular Architecture and Phenotypes.

## Setup

```bash
pixi install            # default environment (StarDist), recap installed in editable mode
pixi run test           # pytest: 66 tests, under a minute, no Fiji or StarDist model needed
pixi run lint           # ruff check + format check (what CI runs)
pixi run format         # apply formatting and safe lint fixes
```

With direnv (Linux, Mac, WSL2), run `direnv allow` once: the default environment then activates whenever you
`cd` into the repo (`.envrc` re-evaluates when `pixi.lock` changes), and `recap`, `pytest` and
`ruff` work directly.

Commit `pixi.lock` so everyone (and CI) gets the same packages. After the first commit of the
lock file, set `locked: true` and `cache: true` in `.github/workflows/ci.yml`.

## Where things go

See `docs/architecture.md`. In short: commands in `cli.py` only parse arguments and print;
all work happens in library modules, which log and raise `RecapError`s instead of printing or
exiting.

## Common changes

**A new setting**
1. Add a field with a description and range to the model in `src/recap/config.py`.
2. Add it, with a comment, to `TEMPLATE` in the same file (`recap template` writes it).
3. Use it; add a test (including an invalid value if it has a range).
4. Mention it in `README.md` if users will change it; add a `CHANGELOG.md` line.

**A new output column**
1. Write it in the code.
2. Add it to `docs/output_columns.md`. `tests/test_docs.py` fails until you do.

**A new command**
1. Put the logic in a library module (testable without the CLI).
2. Add a thin Typer command in `cli.py`: first docstring line short (shown in `recap --help`),
   then a paragraph with details. Check `recap --help` and `recap COMMAND --help`.
3. If people need it in the app, add the button in `src/recap/gui/` calling the same library
   function (never its own copy of the logic), and add a row to the table in `docs/gui.md`.
4. Test the library function and the command (`tests/helpers.py: run_cli`, `make_ws` for a
   workspace) and the button (`tests/test_panels.py`).

**Changing results** (segmentation, filters, measurements)
Re-run the example workspace (`recap run examples`, expect 472 / 248) and note the count before
and after in the pull request; validated settings depend on these numbers.

## Tests

- `tests/conftest.py` makes synthetic images with known content (4 nuclei, 1 VRC+).
- Fiji is never started in tests: `tests/fake_fiji.py` speaks the same protocol as
  `assets/fiji_count.groovy` (change both together); the napari panels are tested with Qt
  off-screen and a stand-in viewer. StarDist training is marked slow (`RECAP_SLOW_TESTS=1`).
- Keep tests fast; anything needing StarDist weights or real data belongs in a separately
  marked test, not the default run.

## Changing the database

The workspace database tables are SQLModel classes in `src/recap/db.py`; Alembic migrations in
`src/recap/migrations/versions/` bring older databases up to date. To change a table:

1. Edit the class in `db.py`.
2. `pixi run db-revision "add notes to masks"` writes the migration; read it and fix anything
   Alembic guessed wrong (renames look like a drop plus an add).
3. `pixi run test`: `tests/test_db.py` fails if the classes and the migrations disagree, and
   tests upgrading a database from recap 4.0.0.
4. Bump the version. Every version change backs the database up before upgrading it, so a bad
   migration can be undone by copying the backup back.

SQLite cannot alter most columns in place; migrations run in Alembic's batch mode (copy the
table, change it, swap it in), which `env.py` turns on.

## Releases and offline bundles

Pushing a tag `vX.Y.Z` runs `.github/workflows/bundle.yml`: each system builds its bundle
(`packaging/build_bundle.py`, with pixi-pack), installs it as a test, and attaches it to the
GitHub Release. Bundles are not kept as run artifacts unless you start the workflow by hand and
tick "keep artifacts" (they are larger than the free plan's Actions storage).

## The desktop app

Start with [`docs/gui.md`](docs/gui.md): how to run it, the code layout, rules (the GUI calls the
library, Qt-free logic stays testable without a screen) and a list of next steps.

## Linux, Windows and macOS

CI runs the tests on all three, so code has to be portable:

- Open text files with `encoding="utf-8"` (`read_text`, `write_text`, `open`): Windows otherwise
  uses a legacy code page and fails on characters such as "−" and "≥".
- Use `pathlib` for paths; never split on `"/"` or build paths with string concatenation. In
  tests that write a path into YAML, use `path.as_posix()` (backslashes are YAML escapes).
- No symlinks, `chmod`, shell scripts or POSIX-only folders in tests unless the test is
  skipped elsewhere (`@pytest.mark.skipif(sys.platform == "win32", ...)`).
- OS differences belong in one place each: Fiji search folders and launcher names in
  `fiji.py`, Windows-only packages as `target.win-64` sections in `pixi.toml`.

## Versions and releases

Semantic versioning, set in `src/recap/__init__.py` (and `pixi.toml`):
major = a change to the public interface (`docs/architecture.md`) or to existing output
columns; minor = new features, settings or columns; patch = fixes. Every release gets a
`CHANGELOG.md` entry. Tag releases `vX.Y.Z`.

## Pull requests

One change per pull request, with tests and a changelog line. CI must pass (lint, and tests on
Linux, Windows and macOS).
