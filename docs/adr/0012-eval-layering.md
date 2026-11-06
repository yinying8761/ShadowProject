# Eval layering: invariants are tests, quality runs are not

`agent-eval-v2` brings a new eval harness: a pure-function runner, baselines and
reports. The work also puts deterministic invariant cases inside the pytest suite,
while quality/baseline cases keep running through `python -m eval.runner`. The repo
previously recorded the decision 「评测不依赖 pytest」, so this looks like a reversal.
It is a refinement: the real question was never *whether* pytest is involved, but
*which layer* a case belongs to, and the old rule answered it by accident, from a
time when the harness had only one kind of case.

The two kinds of case fail for different reasons, and that is the whole of it. An
invariant case fails because the code is wrong. A quality case fails because a model
said something worse than last time — or because the network was slow, or the
account ran out of credit. Treating those as one signal is what makes an eval
channel worthless: a red build that might mean "the model was mid-deployment" is a
red build people learn to ignore.

## Decision

- **Two layers, split by what a failure means.**
  - **Invariant layer → pytest.** Deterministic, fake-model, zero-token, seconds. A
    red here always means *the code is wrong*: a group turn must never exceed M role
    messages, message sequence must be monotonic, an in-flight round is never
    cancelled. These are genuine tests and belong in the suite, next to the code
    whose contract they pin.
  - **Quality / baseline layer → `python -m eval.runner`, never collected by pytest
    by default.** Real model, network, money, minutes. Its output is a score, a cost,
    a latency, a diff against a baseline — not a boolean. A run produces an artifact
    a human reads.
- **Two boundary rules keep the split honest.**
  1. **Default execution never spends money and never touches the network.** A `live`
     case must be opt-in (`--live`); a case that reaches the internet while marked
     offline is a defect, not a convenience. Without this rule the "fast, free"
     layer stops being either, and the split becomes a naming ceremony.
  2. **A red quality run must never be read as "the code is broken".** Model/quality
     regressions and code regressions are different signals and must not share a
     channel. Merge them and people skip the evals; the evals then rot, because
     nobody can tell a real regression from noise.
- **The prior 「评测不依赖 pytest」 decision is refined, not reversed**: invariant
  cases *are* tests, quality cases stay outside the suite. Stated explicitly so the
  next reader does not conclude "anything may be a test" and start adding a
  scored, network-bound case as an assertion.

## Considered Options

- **Everything in pytest, one command.** Rejected. It collapses scores into
  pass/fail: 0.6 and 0.8 both "pass", so a quality regression is invisible by
  construction. It also makes the suite cost money and minutes, and turns a model
  regression into a red build that reads like a code defect — exactly the confusion
  boundary rule #2 forbids.
- **Nothing in pytest, keep the old "no pytest" rule literally.** Rejected. The
  invariant cases are deterministic, free and fast; they are tests in every sense
  that matters. Writing a second runner just for them would be a second harness to
  maintain, with its own discovery, its own assertions and its own way of being
  wrong, for no benefit.
- **Two layers with the two boundary rules above.** Chosen.

## Consequences

- Invariant cases stay in `backend/eval/` + `backend/tests/`, free and offline; they
  are the only eval layer CI may run. Anything that cannot meet that bar is not an
  invariant case.
- Quality/baseline runs live behind `eval.runner` and its flags (`--live`,
  `--only`, `--baseline`); their reports and baselines are artifacts, not assertions.
- Every case must declare its layer correctly. The `live` flag is the enforcement
  point, so a wrong flag silently breaks boundary rule #1 — the failure mode is a
  test suite that quietly starts calling the internet. If the repo has cases that
  call the network while marked non-live, that is a defect under this ADR.
- A failing quality run must be reported to a human, not turned into an exit-code-1
  for the test suite. The runner may exit non-zero for *its own* run; it must not be
  wired into pytest collection in a way that reddens the build.
- Adding a new eval case now requires answering "which layer, and does its failure
  mean the code is wrong?". If that question has no clean answer — if the case is
  sometimes about code and sometimes about the model — the case is not ready yet.
- The runner's pure functions (`score_case`, `render_report`, `diff_baselines`,
  `select_cases`) are covered by pytest, because they *are* code whose failure means
  the code is wrong. The CLI around them stays I/O.
