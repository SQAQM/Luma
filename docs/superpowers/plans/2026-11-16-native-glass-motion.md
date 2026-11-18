# Native glass and motion implementation

The user approved implementing Lenso-like animation, texture and effects while retaining Luma's identity, following DESIGN_REFERENCE_LENSO.md. Continue native C# / XAML WinUI 3, rectangular controls, two-character navigation, image priority, paginated data and bounded caches. Use synthetic fixtures only.

## Design

Adapt the pinned Lenso motion coefficients and material ownership into native Composition and XAML. Related commands share an acrylic surface and subtle elevation; their inner controls remain quiet rectangular parts. Standalone controls retain their own thin rim and readable state feedback. Preserve Luma's cool neutral palette and blue selection. Hover, selection, pressed and keyboard focus remain distinct.

The viewer should float its chrome over a stable image canvas. Showing or hiding ordinary controls should not resize or refit the image; explicit information-panel changes may allocate separate space. Keep all input, focus, animation-frame/media and adjacent-image behavior intact.

Use compositor transitions on a small, fixed set of surfaces. No per-image effects, permanent animation tickers, layout animation during typing or extra menus. Respect reduced motion and high contrast. Keep source attribution for adapted motion code.

## Tasks

- [x] Implement bounded pointer/touch/keyboard press motion and meaningful Core regressions.
- [x] Refine shared material tokens, rectangular control templates, quiet navigation and grouped glass controls.
- [x] Add policy-aware native surface elevation and show/hide transitions with lifetime cleanup.
- [x] Rework the viewer's normal/fullscreen chrome into stable overlays and verify native geometry/focus.
- [x] Inspect synthetic light/dark, large-text and reduced-motion states; obtain context-free PNG design review and independent source review.
- [ ] Freeze reviewed source, build/audit installer and portable, update only owned program files, publish and remove this iteration's temporary/build output and previous packages.

Validation remains proportional: focused Core/native regressions, affected media/thumbnail cases, source audit and package checks. A full legacy environment-dependent discovery run is not required for a UI-only change. Make the preview reviewable before final packaging, and reuse the existing toolchain.
