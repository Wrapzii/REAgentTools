# REAgentTools — Benchmark Report

**Status:** Theoretical round-trip reduction only. **No measured token savings** — editor MCP tests were not executed during plugin build.

## Methodology (planned)

1. Same task via low-level Epic tools (count `call_tool` round-trips).
2. Same task via REAgentTools composite (count round-trips).
3. Compare response payload sizes (JSON bytes).
4. Record in `Saved/REAgentTools/benchmark_results.jsonl` when measured.

## Theoretical round-trip table

| Task | Epic low-level calls | REAgentTools calls | Expected reduction |
|------|---------------------|-------------------|-------------------|
| Spawn actor + set 3 props + verify | 5–7 (find/spawn/get/set/get/bounds) | 1 (`spawn_configure_attach_and_verify`) | ~6→1 |
| Move 10 actors | 10–20 (find + set_transform each) | 1 (`batch_transform_actors`) | ~15→1 |
| Find assets + edit 5 + save | 11+ (find×5, set×5, save×5) | 1 (`bulk_edit_asset_properties_and_save`) | ~11→1 |
| Create BP + defaults + compile + save | 4–6 | 1 (`create_or_update_blueprint`) | ~5→1 |
| Create MI + assign to mesh | 4–5 | 1 (`create_assign_material_instance`) | ~4→1 |
| Editor context snapshot | 3–4 (level, selection, dirty) | 1 (`get_editor_context`) | ~3→1 |
| Multi-step batch (spawn+props+save) | 3+ | 1 (`execute_editor_batch`) | ~3→1 |

## Payload size (design target)

- WorkflowResult compact JSON: typically **0.5–4 KB** vs full actor/property trees **10–50+ KB**
- Soft limit: 51,200 bytes (`ResponseSoftLimitBytes`) with truncation warning

## Token impact (qualitative)

Each MCP round-trip triggers a full model context pass. Composites primarily save **call count**, not single-call tokens. Best case: 6 calls → 1 call ≈ **5 fewer context passes** per task (per `unreal-mcp-budget.mdc` guidance).

## Next measurement

Run smoke tasks from [TEST_REPORT.md](TEST_REPORT.md) with `Saved/mcp_probe/mcp_call_log.jsonl` instrumentation and append results here.
