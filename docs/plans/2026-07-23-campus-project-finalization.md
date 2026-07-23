# WorkPilot Campus Project Finalization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Freeze feature growth and turn the verified WorkPilot codebase into a concise, repeatable, interview-ready campus recruitment project.

**Architecture:** Preserve the current Runtime and persistence behavior. Add a thin cross-platform demo runner around the existing Stub CLI, restructure documentation around progressive disclosure, and keep all enterprise gaps explicit.

**Tech Stack:** Python 3.11+, Typer CLI, pytest, Markdown, React/Vite.

---

### Task 1: Freeze the verified infrastructure baseline

**Files:**
- Stage: existing HA-01～HA-02C source, tests, migrations, and documentation

**Step 1:** Run `git diff --check` and confirm no temporary PostgreSQL files exist.

**Step 2:** Review `git status --short` and verify every path belongs to the completed persistence/checkpoint/lease work.

**Step 3:** Commit the verified baseline as `feat: add durable fenced run execution`.

### Task 2: Add a cross-platform deterministic demo

**Files:**
- Create: `scripts/demo.py`
- Create: `tests/test_demo_script.py`

**Step 1:** Add an end-to-end test that invokes the script with a temporary output directory and requires `DEMO_OK`, a passed Run, and the core Artifact set.

**Step 2:** Run the test and verify it fails because the script does not exist.

**Step 3:** Implement a standard-library-only runner that invokes the existing Stub CLI, rejects an existing output directory, validates JSON/Markdown artifacts, and prints content-minimized statistics.

**Step 4:** Run `pytest tests/test_demo_script.py -q` and verify it passes.

### Task 3: Rebuild the project homepage

**Files:**
- Modify: `README.md`

**Step 1:** Lead with the evidence-gated positioning and a “why this is not a generic LLM demo” comparison.

**Step 2:** Add one architecture flow, a three-command demo, verified engineering highlights, repository map, truthful limitations, and focused documentation links.

**Step 3:** Validate every local link and command path.

### Task 4: Add interview and demonstration material

**Files:**
- Create: `docs/07-求职展示/01-校招项目介绍.md`
- Create: `docs/07-求职展示/02-演示与验收.md`
- Modify: `docs/00-文档阅读指南.md`
- Modify: `docs/06-项目状态/01-当前实现状态.md`
- Modify: `docs/02-项目开发计划/09-下一阶段改进路线图.md`

**Step 1:** Document the 30-second and 3-minute narratives, resume bullets, deep-dive questions, and claims that must not be made.

**Step 2:** Document a five-minute offline demo flow and recovery/fencing evidence that can be shown without live fault injection.

**Step 3:** Mark the codebase as feature-frozen for campus delivery and keep HA-02D as a future direction.

### Task 5: Final verification and delivery commit

**Files:**
- Modify: status and changelog documents only if verification counts change

**Step 1:** Run the demo test, full no-database pytest suite, `compileall`, frontend production build, and `git diff --check`.

**Step 2:** Run the demo command once and inspect its actual console summary and artifacts.

**Step 3:** Commit finalization changes as `docs: finalize campus project presentation`.
