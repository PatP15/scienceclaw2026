Source files of the 4-slide investor deck (Claude Slides artifact: https://claude.ai/artifact/8PiJBrwSGu7TDNgc11KCGA).

Each slide follows the few-words rule: the headline, at most 3 short bullets, and one figure that
carries the slide. Speaker notes (`<aside>`) mirror `docs/pitch/slides.md`.

Every `<img>` has a `data-file` attribute that names the repo file it shows. The `/_blob/...` ids
are uploaded copies of earlier versions of those files.

**Before republishing, re-upload every image.** The figures were all regenerated under the
few-words rule, so the existing blobs are stale:

- cover → `data/figures/closed_loop.png`
- pipeline → `data/figures/evidence_pipeline.png`
- partners → `data/figures/tech_highlights.png` (new; its `src` is still the repo path)
- traction → `data/figures/benchmark_summary.png`
