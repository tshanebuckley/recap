## What and why

<!-- One change per pull request. Link the issue or story it addresses (e.g. "Fixes #12", "US-17"). -->

## Checklist

- [ ] `pixi run lint` and `pixi run test` pass (and `RECAP_SLOW_TESTS=1 pixi run test` if training changed)
- [ ] New or changed behaviour has a test (`tests/`; the app: `tests/test_panels.py`)
- [ ] A line in `CHANGELOG.md`
- [ ] User-facing change: the matching section of `docs/guide/` (and `docs/gui.md` for a new button) is updated
- [ ] New feature: library function first, then the command, then the button (CONTRIBUTING.md)
- [ ] Counts may change (segmentation, filters, measurement): example workspace before / after: ___ / ___ (expect 472 / 248)
- [ ] Database table changed: migration added (`pixi run db-revision`) and checked
- [ ] No images, counts, results or `recap-data/` committed

## Tested on

<!-- Linux / Windows / Mac, and how (tests only, the example workspace, real images). -->
