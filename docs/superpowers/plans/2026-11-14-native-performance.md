# Native performance implementation plan

**Goal:** Reduce repeated work during model paging and photo navigation, with reproducible synthetic measurements.

**Architecture:** Reuse revision-keyed bounded model-query summaries and the existing overlapping viewer thumbnail objects. Keep the current native WinUI controls, verified image pipeline and SQL cursors. Remove production-only overhead from diagnostic sampling and repeated caption writes.

**Constraints:** No real Data, photos, prompts, model weights or credentials; fixtures/builds stay inside this repository. Preserve file identities, asynchronous generations, cursor invalidation, memory limits and write protections. Use the existing toolchain and keep only current installer/portable artifacts after publishing.

- [x] Establish failing operation-count regressions and baseline timings for repeated model pages, visible filmstrip navigation, caption refresh and non-diagnostic idle sampling.
- [x] Add bounded, transaction-revision-aware model facets/count reuse and verify mutations, scope changes, caller isolation and concurrent snapshots.
- [x] Reconcile only the viewer's current bounded thumbnail window; preserve overlapping identities without dropping generation or decode checks.
- [x] Gate idle sampling by diagnostics and skip unchanged caption colors while retaining theme/contrast/fullscreen behavior.
- [x] Run targeted backend/Core/native checks and compare warm/cold operation counts and timings; review the changes independently.
- [ ] Build/audit both delivery formats, preserve real Data during program update, publish reviewed source/release, then remove owned build/test outputs and prior packages.

Measurements identify their scope: a synthetic model SQL benchmark is not a model-file scan; thumbnail request reductions are not million-image throughput or cold-disk claims.

Follow-up companion consolidation: reproduced the five-file layout; implemented persisted two-keeper / three-extra review, exact undo, same-name owner protection, malformed-input handling and UI export simplification. Native 100/150 text checks and independent source / blind PNG reviews completed.
