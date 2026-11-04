# Luma Engine protocol v1 (stage3)

Private redirected stdin/stdout, no socket/server/Shell. Four little-endian uint32 bytes length, then strict UTF-8 JSON object. 1–67108864 bytes; max depth64, no duplicate keys, lone surrogates, NaN/Infinity or integers outside signed64. EOF between frames is clean; partial header/body is protocol_error. Never print payloads/paths/prompts/tokens to stderr.

Request exactly: version=1, requestId ASCII [A-Za-z0-9_-]{1,64}, generation int0..Int64.MaxValue, identity string<=512 UTF-8 bytes, method ASCII identifier<=64, payload object. Reply echoes version/requestId/generation/identity, kind=result|error, payload object/array/value. Error payload only fixed code and fixed localized client message mapping. Out-of-order results use requestId plus generation/identity; late cancelled contexts never publish into current controls.

Hello must precede dispatch: engine.hello {protocol:1}; response lists capabilities/budgets/preview=true/writesEnabled=false. query limit1..120, neighbors before/after0..4 (<=9total), detail lazy. Main methods: catalog.query/stats/roots/detail/neighbors/thumbnail; job.get/cancel; request.cancel; engine.status/shutdown. Only explicit preview diagnostics.echo/wait are available. Shutdown/EOF closes admissions, cancels transient requests, waits calls/service.close, flushes writer then releases old native-instance.lock byte0. No forced worker termination or KillOnJobClose.

Two active requests, four admitted ordinary requests total, two queued output frames. Request cancellation and shutdown bypass normal admission; single stdout writer serializes complete frames. Large metadata is never truncated; overbudget results return response_too_large. The64MiB ceiling deliberately covers current retained512Ki-character Unicode prompts/raw metadata; tests must round-trip worst retained Unicode and escape expansion in both languages. This is a budget, not a production throughput claim.

Assets return an opaque resourceId and relativePath restricted to thumbnails/ under engine data root; no original path as a UI executable/URL, no JSON/Base64 pixels. C# resolves only validated bounded cache-relative resources, rejects traversal/rooted/reparse paths.

Unknown writes: engine.status accepts operationId and returns outcome=not_enabled in stage3; future persistent plan/job IDs resolve uncertain results before any retry. No dangerous command automatic replay and no exactly-once claim. Actual scan/move/delete/model/network writes stay absent from capabilities.

Stage3 only accepts test-owned preview-marker directories. Production catalog/schema/migration is not enabled. Old Qt lock interaction is tested only on synthetic data.


## Stage4 read extension

Still preview marker only. Explicit --seed-demo is a separate preparation process that refuses an existing database/demo-images directory and writes only new synthetic assets/index under the owned marked directory. It is not an import RPC.

query summaries omit prompt/negative/raw fields and add identity, SHA256 of persistent token/revision plus row file metadata; selected detail returns full prompts, prompt_tags and independent content_tags. detail/thumbnail optionally accept identity; new gallery always supplies it. Stale identity/cursor yields fixed errors. Artist exact filtering reuses the existing SQL cursor/count path, no schema change.

Thumbnail cache keys include catalog identity, filesystem identity and reliable change time. Persistent SHA256 seal detects/rebuilds replaced cache content; reply includes sha256 and encoded bytes (<=4MiB). Client validates its opened handle stays under thumbnails/, reads that handle with write/delete sharing denied and verifies length/hash before decode. This supplements original-file ABA verification. Original hardlinks remain unsupported by this temporary read adapter; external open is available.

catalog.facets returns <=100 model/format/artist/prompt-term choices and <=128 enabled content definitions; roots <=200. These are bounded suggestions; editable exact names remain available. Model/format/artist/prompt-term filters are separate from semantic content labels. Two image views retain at most120 summaries, one full detail,128 cursor anchors; image decode budget64MiB/160 entries (current page<=120), one thumbnail RPC reserves another engine lane for interactive work. Diagnostic pressure is explicit --diagnostics --thumbnail-pressure only, never a normal startup task.
# Stage5 pixel-resource extension (asset version2)

`catalog.preview.v2 {imageId,identity,mode:"standard"|"original"}` is a read-only method. It requires the exact catalog identity, returns no source file bytes in JSON, and preserves protocol framing version1. Its response has `version:2`, `resourceId`, relative `thumbnails/viewer-v2/...png` path, `identity`, SHA256, encoded bytes, pixel width/height, oriented sourceWidth/sourceHeight, `kind` (`standard`, `original`, `compatibility`), `frame:0`, and `animated`.

Container limit128MiB, source50M pixels, original24M pixels/16384 per edge, standard edge2400, compatibility edge1200, encoded resource32MiB. Unsupported originals fall back within the source/container limits; images exceeding those limits fail closed. GIF/animated formats and TIFF show frame0; animation playback, TIFF page controls, tiled giant-image rendering and color-managed proofing remain later work. Assets remove EXIF/prompt/ICC dictionaries; this is not a color-management guarantee. Read handles deny write/delete and protect ancestors. Hardlinks are allowed only in this dedicated read path; duplicate-cleanup invariants are unchanged.

`catalog.neighbors` continues to return at most9 summaries and now adds the same persistent `identity` field as query. Version1 thumbnail paths retain their prior response shape and budget. Client revalidates v2 resource identity, actual open handle boundary, encoded length/digest and WIC dimensions before display. Cache hits still call the engine to validate current source/file-change identity. Client decoded LRU has4 entries/128MiB; it warms at most two neighbor resources after current pixels, without allocating neighbor pixels. Native codec work is not forcibly terminated; stale/cancelled results are discarded and worker admission remains bounded.

## Stage6 management capability (0.8.0-stage6.1)
`engine.hello` advertises `managementWrites=true`, `destructiveWrites=false` and
`manage.read`, `manage.write`, `manage.status`; legacy `writesEnabled=false` remains.
The client requires the capability before allowing these calls.
`manage.read {section,args}` bounds collections to 60 models/associated images,
128 semantic categories, 100 jobs and diagnosis groups. Job projections exclude payloads.
`manage.write {operationId,action,args}` requires a 32 lowercase hex operation ID.
Actions: catalog.import, metadata.refresh, image.patch (favorite/rating only), settings.save,
tags.save, labels.apply, vision.install/classify, models.add/remove/scan/sync/rebuild/lookup/reclassify,
registry.save, credential.set/clear/test, job.cancel. No original-file movement/deletion exists.
Metadata/manual selection is 1–120, model sync/lookup at most60. Manual labels use a server
snapshot and atomic identity checks; content categories remain separate from prompt words.
Model association `match=verified|candidate` maps candidate to the existing name-only filter;
name candidates never establish ancestry. Model weights are not inspected on registration.
Credentials are write-only; presence is local and connection testing is a separate explicit action.
`manage.status {operationId}` returns unknown/pending/completed/failed/expired.
A separate FULL-synchronous control database records pending before service invocation; only128
full results are retained with durable expired tombstones. Replayed pending/failed/expired IDs
cannot rerun; results and payload fingerprints never include plaintext credentials.
A crash between control journal and catalog commit is uncertain, not exactly-once execution.
Clients retain only ID/action locally before submission and never automatically replay an
uncertain request. Only explicit status reads may resolve it. Known no-effect errors include
invalid_request, busy, stale_identity, not_found, unsupported, not_ready, invalid_token and
model_hash_missing; transport/unclassified failures remain uncertain until status is checked.
Vision runtime diagnostics import packaged libraries only, with no download, weights or inference.
Normal preview profiles are owned separately from Qt; explicit synthetic demos use fresh stores.
