# REAgentTools — Benchmark Report

**Status:** Live measured 2026-07-23 via `Content/Python/benchmark_reagent_tools_ab.py`  
**Proof:** `Saved/REAgentTools/benchmark_ab_live.json`

## Headline (what you remembered)

Your memory of **~15 → ~5** matches the **batch-move** family. Live A/B on this machine:

| Task | Epic low-level calls | REAgentTools | Call cut |
|------|---------------------:|-------------:|----------|
| Spawn + label + transform + verify | **6** | **1** (`spawn_configure_attach_and_verify`) | **6 → 1** |
| Move 5 actors (find + set + get each) | **15** | **1** (`batch_transform_actors`) | **15 → 1** |

So the “15 → 5” story was the right *direction*; for a clean 5-actor batch the composite is even better (**15 → 1**).

## Token metrics (not just calls)

Two layers:

1. **Wire / payload tokens** — measured: `(request_bytes + response_bytes) / 4` summed over the task.
2. **Agent Usage-row estimate** — modeled from `Content/RE/MCP_CONTEXT_ISOLATION.md`: each tool turn re-reads a fat chat prefix (~150k tokens) plus payloads so far. This is what Cursor’s dashboard usually counts (mostly **cache reads**), not one giant prompt.

| Task | Path | Calls | Payload tokens (meas.) | Agent tokens est. (fat chat) |
|------|------|------:|-----------------------:|-----------------------------:|
| Spawn+verify | Epic | 6 | 710 | **902,524** |
| Spawn+verify | Composite | 1 | 264 | **150,264** |
| Batch move ×5 | Epic | 15 | 1,405 | **2,261,185** |
| Batch move ×5 | Composite | 1 | 388 | **150,388** |

### Ratios (composite ÷ epic)

| Task | Payload tokens | Agent tokens (fat) |
|------|---------------:|-------------------:|
| Spawn+verify | **0.37×** (~63% less wire) | **0.17×** (~6× less) |
| Batch move ×5 | **0.28×** (~72% less wire) | **0.07×** (~15× less) |

**Takeaway:** Composites save some payload, but the big win is **fewer agent turns**. One call ≈ one 150k prefix re-read; fifteen calls ≈ fifteen.

## Caveats (honest)

- This is **MCP A/B instrumentation**, not a Cursor dashboard export from two agent chats.
- Agent token numbers use a **150k fat-prefix model** (research default). A tiny fresh chat (~50k) scales the same ratios down linearly — still the same call-cut story.
- Editor was on `Lvl_Hub`; tasks are synthetic StaticMeshActors (deleted after).
- Wire times were ~16s/call on this session (editor/MCP latency) — that is wall-clock, not tokens.

## How to re-run

```powershell
# Unreal editor open, MCP on :8000
python Content/Python/benchmark_reagent_tools_ab.py
```

## Older theoretical table

Still useful as intuition; superseded by the live JSON above for claims.

| Task | Epic | Composite | Expected |
|------|-----:|----------:|----------|
| Spawn + props + verify | 5–7 | 1 | ~6→1 |
| Move 10 actors | 10–20 | 1 | ~15→1 |
| Find + edit 5 assets + save | 11+ | 1 | ~11→1 |
