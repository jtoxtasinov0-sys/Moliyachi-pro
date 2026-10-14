# PROGRESS – Moliyachi Pro motion guide   (updated 2026-10-14)
Phase: final
Current version: v1 → renders/final/moliyachi_qollanma_v1.mp4 (1080×1920, 60 fps, ~86.5 s)
Done: - app code studied (workers, models, work entries, approval, pay = qty × unit_price snapshot)
      - 12 scenes in src/main.js (deterministic renderAt(t)), style: dark + violet neon ribbon + gold accent
      - SFX + music synthesised in code (scripts/audio.py) from plan/cues.json, loudnorm -14 LUFS
      - QOLLANMA.md (written guide), plan/vo_script.md (optional ElevenLabs VO)
Next step: user feedback; optional VO track → re-time scenes to real MP3
Key decisions: no VO yet (on-screen text guide); Pinterest refs unreachable from sandbox → style taken from the attached screenshot
Key files: src/index.html, src/main.js, src/render.js, scripts/audio.py, scripts/preview.mjs, scripts/stills.sh
Rebuild: node src/render.js --cues plan/cues.json && python3 scripts/audio.py && (two-pass loudnorm) && render segments && mux
