# REAgentTools — Benchmark Report

**Status:** Live A/B measured 2026-07-23 via `Content/Python/benchmark_reagent_tools_ab.py`  
**Proof JSON:** `Saved/REAgentTools/benchmark_ab_live.json` (also mirrored in this repo as `Docs/benchmark_ab_live.json`)

## What is verified vs estimated

| Metric | Status | How |
|--------|--------|-----|
| **MCP call count** | ✅ **Verified** | Counted live `call_tool` round-trips (Epic path vs composite) |
| **Wire size** (request + response bytes) | ✅ **Verified** | Measured from HTTP MCP client on each call; summed per task |
| **Payload tokens** (`wire_bytes / 4`) | ✅ **Derived from verified wire** | Same bytes, converted with the usual ~4 bytes/token heuristic |
| **Cursor Usage / cache-read tokens** | ❌ **Not verified** | Cursor does not expose billed tokens to this script. Those numbers (if shown below) are **estimates only** — real stats = [cursor.com/dashboard/usage](https://cursor.com/dashboard/usage) |

**Do not cite the agent-token column as measured.** Cite **calls + verified wire bytes / payload tokens** when sharing results.

## Headline (call cut)

| Task | Epic low-level calls | REAgentTools | Call cut |
|------|---------------------:|-------------:|----------|
| Spawn + label + transform + verify | **6** | **1** (`spawn_configure_attach_and_verify`) | **6 → 1** |
| Move 5 actors (find + set + get each) | **15** | **1** (`batch_transform_actors`) | **15 → 1** |

The familiar “~15 → ~5” story matches the **batch-move** family; live A/B for five actors is **15 → 1**.

## Verified wire + payload

| Task | Path | Calls | Wire bytes ✅ | Payload tokens ✅ (`bytes/4`) |
|------|------|------:|-------------:|------------------------------:|
| Spawn+verify | Epic | 6 | 2,846 | 710 |
| Spawn+verify | Composite | 1 | 1,055 | 264 |
| Batch move ×5 | Epic | 15 | 5,625 | 1,405 |
| Batch move ×5 | Composite | 1 | 1,552 | 388 |

### Wire / payload ratios (composite ÷ epic)

| Task | Wire / payload |
|------|---------------:|
| Spawn+verify | **0.37×** (~63% less) |
| Batch move ×5 | **0.28×** (~72% less) |

## Estimated agent Usage (NOT verified — optional context only)

Modeled from `Content/RE/MCP_CONTEXT_ISOLATION.md`: each tool turn ≈ re-read ~150k fat-chat prefix + payloads so far. Useful intuition for why call count dominates Cursor dashboard rows; **replace with dashboard numbers for any real claim.**

| Task | Path | Agent tokens est. (fat) |
|------|------|------------------------:|
| Spawn+verify | Epic | ~902k |
| Spawn+verify | Composite | ~150k |
| Batch move ×5 | Epic | ~2.26M |
| Batch move ×5 | Composite | ~150k |

**Takeaway (verified part):** Composites cut **calls** and **wire size**. The big Cursor bill win is almost certainly fewer turns — confirm on the Usage dashboard with two fresh chats if you need hard numbers.

## Caveats

- MCP A/B instrumentation on this machine, not a Cursor dashboard export.
- Editor was on `Lvl_Hub`; synthetic StaticMeshActors deleted after.
- ~16s/call wall-clock this session = editor/MCP latency, not tokens.
- Spawn composite requires `folder_path` (revalidated empty string).

## How to re-run

```powershell
# Unreal editor open, MCP on :8000
python Content/Python/benchmark_reagent_tools_ab.py
```

## Older theoretical table

Superseded by the live JSON for claims; kept as intuition.

| Task | Epic | Composite | Expected |
|------|-----:|----------:|----------|
| Spawn + props + verify | 5–7 | 1 | ~6→1 |
| Move 10 actors | 10–20 | 1 | ~15→1 |
| Find + edit 5 assets + save | 11+ | 1 | ~11→1 |
