# Native usability implementation plan

**Goal:** Make routine model searching and photo navigation continuous with mouse or keyboard.

**Architecture:** Retain the existing WinUI 3 controls, bounded SQL pages, native selection and glass styles. Fix state/focus ownership at the affected view boundaries. No new runtime dependency or frontend framework.

**Spec:** `docs/REQUIREMENTS.md`, native keyboard/selection rules in `AGENTS.md`, and the user's standing preferences for compact controls, consistent fields, minimal text, private development fixtures and a current installer/portable delivery.

## Constraints

- Tests use newly created synthetic profiles inside the repository; no real Data, pictures, prompts, weights or credentials.
- Keep tools stable while loading; do not steal focus when the user moves elsewhere.
- Preserve page/filter state and bound all image/query work.
- No additional redundant actions or explanatory interface paragraphs.
- Reuse the existing toolchain; keep only current delivery packages after verification.

## Work

- [x] Reproduce model search focus/caret interruption during debounce/refresh; verify asynchronous edits are retained, including moving focus away.
- [x] Retain search input ownership and make directory/search field sizing usable at 100%/150% text size.
- [x] Reproduce photo return and stage keyboard focus problems using synthetic images; restore focus to the correct photo without pulling focus out of unrelated controls.
- [x] Inspect actual narrow/wide screenshots, popup frames, button focus and text/copy labels; apply only relevant consistency fixes.
- [x] Run targeted native integration checks at normal and enlarged text sizes; have a fresh reviewer inspect the branch and a context-free design reviewer inspect synthetic frames.
- [x] Build/audit installer and portable artifacts, update program files without touching Data, publish reviewed source and release, then remove owned intermediate outputs.

## Review focus

- Slow search response followed by another edit must not erase that edit or relocate the caret.
- A person tabbing away while results load must keep their new focus.
- Closing a photo must return to that photo in its source view; keyboard viewing must remain usable.
- Large font/narrow width must leave navigation, model scope, sorting and essential actions reachable.
- Test hooks and generated fixture content must stay out of normal UI and delivered data.

## Evidence

Baseline model-card legacy script cannot reach its removed path-entry action; this is an obsolete harness, not evidence that directory picking is broken. Retain that distinction in the verification record. Use targeted current probes for the actual interactions above.
