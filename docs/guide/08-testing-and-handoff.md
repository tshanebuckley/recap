# Testing and handoff

[← 7. Keep and share work](07-keep-and-share.md) · [Guide](README.md) · [Glossary](glossary.md)

## How to test a story

The test plan is a shared sheet with three tabs:

| Tab | For | Who |
|---|---|---|
| **User stories** | the workflow on these pages, US-01 to US-28 | everyone; P1 stories on every lab computer |
| **Technical checks** | CI, installs, edge cases (Fiji closed mid-count, OneDrive, low disk), database upgrades | the developers |
| **Defects** | anything that failed | whoever found it |

1. Pick a story with Status *Not run*; set your name in *Tester* and the date.
2. Follow its page here (the *Try it* column is the short version).
3. Compare with *Done when*. Set Status: **Pass**, **Fail**, **Blocked** (something else must be
   fixed first) or **N/A** (does not apply to this computer).
4. For a Fail, add a row on **Defects**: the story ID, your system (Windows / Mac, version),
   what you did, what happened, what you expected, and the run log
   (`results/run_<date>.log`) or a screenshot.

Defects can also be reported as GitHub issues: **New issue > Defect** asks for the same fields.
Do not attach unpublished images or counts to an issue.

Severity: **Blocker** (cannot continue the workflow), **Major** (wrong numbers, lost work,
a crash), **Minor** (works, but confusing or slow), **Cosmetic**.

**What to always write down:** the counts for the example image on your system, install time
and disk use, how long tuning and training took, and every message that was unclear.

## US-26 Hand recap to the neuroscience undergrad (Windows)

*As the PI, I hand recap to the neuroscience undergrad.*

Ready when every P1 story passes on both Windows PCs and there is no open Blocker or Major
defect. Hand over with this guide and the lab's counting protocol (which nuclei count, how
border nuclei are treated, who counts which images).

## US-27 Hand recap to the CS undergrad (Mac)

*As the PI, I hand recap to the CS undergrad.*

Ready when every P1 story passes on the Mac and there is no open Blocker or Major defect. Hand
over with this guide plus [`CONTRIBUTING.md`](../../CONTRIBUTING.md),
[`docs/architecture.md`](../architecture.md) and [`docs/gui.md`](../gui.md).

## For developers: keeping stories, code and pages together

- A new feature gets a library function, a command, a button, a test, a story in the test
  plan and a section on the matching page here, in that order.
- Story IDs never change; a removed story is marked N/A rather than renumbered.
- Before a release: `pixi run lint`, `pixi run test` (and `RECAP_SLOW_TESTS=1 pixi run test` for
  training), run the example workspace (472 / 248), update `CHANGELOG.md` and the version, tag
  `vX.Y.Z` (CI builds and attaches the offline bundles).
