# Kiro Hackathon Playbook

A concise, reusable checklist for future Kiro / Kiro Crew hackathon projects, derived from the detailed findings in `docs/hackathon-retrospective.md` (built while shipping ChangeGuard AI). This file is meant to be copied into a new project's `docs/` folder and used as a starting checklist, not read as prose.

Where a Kiro Crew *capability* is stated below as a limitation, it reflects behavior empirically confirmed via a live probe in the ChangeGuard AI project against the installed Kiro Crew 0.2.0 runtime — re-verify against whatever version you have installed, since Crew's behavior may change between versions.

## 0. Before writing any design doc

- [ ] Lock scope: write the explicit in-scope feature/rule list *and* an explicit out-of-scope list before designing anything. A tightly-scoped MVP is worth protecting — it's cheap to write down and expensive to recover once scope creep starts.
- [ ] If your project will use Kiro Crew for orchestration, run a 15-minute empirical probe **before** writing the orchestration section of your design doc:
  - Plan and execute one disposable YAML DAG node (e.g. `shell: "echo hi > /tmp/probe.txt"`) against a real running `kirocrew gateway` (or `--test-mode` if available).
  - Confirm whether the node's command text runs as a literal subprocess or as an LLM/agent chat turn.
  - Confirm whether the YAML node's `agent:` field actually binds to a different Kiro CLI agent, or is only descriptive text.
  - Confirm what happens to a DAG node whose underlying command deliberately exits non-zero — does Crew's own task status reflect that failure?
  - Clean up the probe artifact/task afterward.

## 1. Assume these Kiro Crew properties by default (re-verify per version, don't rediscover from scratch)

- [ ] DAG nodes are likely executed as LLM/agent chat turns, not deterministic subprocesses — word every node's prompt as an explicit, non-negotiable instruction.
- [ ] A YAML node's `agent:` field may be cosmetic only — the real agent binding may be a single, run-scoped agent set once per run. If your workflow needs multiple specialized agents, plan for one narrow "runner" agent that itself invokes each specialized agent via `kiro-cli chat --agent <name>`.
- [ ] A completed task's result text is likely not automatically shown to a dependent task's prompt — plan on the filesystem as your data-plane between DAG nodes, with fixed, explicitly-named artifact paths.
- [ ] The DAG schema likely has no conditional/branching primitive — any workflow needing a gated or conditionally-reached stage should be split into separate files, decided by an out-of-Crew script inspecting filesystem state.
- [ ] A human-approval gate is likely only settable via a specific REST sequence (plan → locate → PATCH → verify → execute), never via the YAML file itself and never via a combined submit-and-run endpoint.
- [ ] A DAG task's own pass/fail status likely tracks whether the agent's chat turn completed, not the exit code of any nested command it ran — never treat Crew's task status as a safety-relevant enforcement boundary.
- [ ] `kiro-cli` chat stdout is likely not a reliable machine-readable transport — it may mix narration, echoed tool output, and progress text. Never parse it as an authoritative signal for anything safety-relevant.

## 2. Design principles to bake in from the start

- [ ] Separate evidence/fact-extraction (deterministic code) from judgment (the LLM agent). Deterministic code should never return a verdict — only facts or a structural "insufficient evidence" signal.
- [ ] For anything where an LLM-mediated task's success must be *proven* rather than assumed: have the deterministic script that performs the actual work write a structured result artifact — atomically, on validated success only, to a confined, per-invocation path — and have the caller validate that artifact directly. Never infer success from Crew's task status or from an agent's chat stdout.
- [ ] If multiple agents need the same permission boundaries (e.g. "never run `terraform apply`", "no generic file-write tool", "refuse unsupported IDs"), maintain a single shared source/template fragment for that boilerplate and copy it into each agent's prompt, rather than authoring each prompt's permission-boundary section independently from scratch. (Treat this as a copy-paste source-of-truth pattern unless your Kiro CLI version has verified runtime prompt-include support — don't assume includes work without checking.)
- [ ] Add known Kiro Crew checkpoint/runtime filenames (e.g. a root-level progress-checkpoint file Crew may write when a run uses your repo as its working directory) to `.gitignore` on day one, not during final cleanup.

## 3. Test strategy from day one

- [ ] Gate any live-agent test (one that would invoke a real `kiro-cli chat` call) behind an environment variable or `shutil.which(...)` check, in the very first test file you write for it — not as a later polish task.
- [ ] Provide two test commands: a fast/deterministic default and an explicit opt-in "live" variant that clearly warns about credit consumption and slowness.
- [ ] Prefer fixture-based, hand-constructed test cases over anything requiring a live agent call, and reserve live-agent tests for the specific judgment behavior that cannot be tested any other way.

## 4. Live verification discipline

- [ ] Verify each safety-relevant scenario (e.g. safe / approve / reject, or your project's equivalent) live, in full, exactly once as a baseline.
- [ ] After a subsequent fix, re-verify live only the specific stage or scenario that fix actually touches. Trust the already-verified baseline and your deterministic test suite for everything else.
- [ ] Before each live pass, write down (even briefly) what you expect to observe and what would falsify it — this avoids re-deriving the same setup steps and checkpoints from scratch each time.

## 5. Build the judge-facing surface early, not last

- [ ] Draft the Makefile, README, and demo script alongside implementation, not after it's "done." Building these early surfaces real usability issues (e.g. an ambiguous CLI flag match, a missing prerequisite check) while they're still cheap to fix.
- [ ] Makefile should include at minimum: a `setup`/preflight target that only checks for required binaries (never auto-installs), a fast default `test` target, an explicit opt-in slow/live test target, and a `help` target that doubles as a mini quick-start.
- [ ] README should state plainly, near the top: what's deterministic vs. AI-judged, where the human-approval gate is, and what your final verdict does and does not certify.

## 6. Final submission hygiene (do this once, near the end)

- [ ] `git status --short` — confirm no unexpected stray files.
- [ ] Check for and gitignore/remove any Kiro Crew checkpoint files accidentally captured by a live run using your repo as its working directory.
- [ ] Scan judge-facing docs (README, demo script, submission draft/checklist) for internal task IDs, live approval IDs, temporary filesystem paths, or debugging transcripts — these belong in internal spec/task docs, not in submission-facing material.
- [ ] Confirm no secrets are committed; confirm any credential-shaped strings in fixtures/demo config are clearly fake and documented as such.
- [ ] Run your fast deterministic test suite one final time; do not run the slow/live suite as part of final submission prep unless you specifically need to re-verify live behavior.
- [ ] Verify your core "never touch the real thing" invariant one last time (e.g. `git diff` against your safety-critical baseline file is empty).

## Estimated payoff

Treat any time/credit savings estimate as directional, not measured — actual savings will depend on your project's size and how many live-agent scenarios you need to verify. The main levers, in rough order of expected impact, are: probing Crew semantics before designing around assumed behavior, gating live tests from the first test file, and not re-verifying already-proven scenarios after unrelated fixes.
