# PROGRESS – Moliyachi qo'llanma video   (updated 2026-10-14)
Phase: final (v3)
Current version: v3 → renders/final/moliyachi_qollanma_v3.mp4 (1080×1920, 60 fps, ~133 s)
Done:
- v1: motion guide (no VO) — src/index.html + main.js
- v3: timed to the user's voiceover (assets/vo/vo_v1.wav, Gemini TTS), app-first (Mini App screens), Telegram only where needed
  - VO→text alignment without ASR: scripts/align.py (pause-based DP) → plan/words.json → src/words.js
  - background: animated satin/silk gradient (src/bg3.js), app navy/royal-blue palette
  - SFX synthesised in code (scripts/audio2.py), no music; VO cleaned with ffmpeg EQ/de-ess/compressor; −14 LUFS
Key files: src/v3.html, core3.js (helpers + Telegram phone), bg3.js, app3.js (Mini App engine), scenes3.js, render.js
Rebuild: node src/render.js --page v3.html --cues plan/cues_v2.json && python3 scripts/audio2.py && loudnorm → mix_v2.wav; render 4 segments (--video) → concat → mux
Next step: user feedback
