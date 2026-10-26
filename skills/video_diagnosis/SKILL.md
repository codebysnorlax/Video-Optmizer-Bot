---
name: video_diagnosis
description: Use this skill to diagnose mobile playback lag, high file size bloat, stuttering, dropped frames, codec incompatibilities, and audio playback issues. Triggers on 'lag', 'stutter', 'drops frames', 'freeze', 'why so big', 'too much size', 'reduce size', 'cannot play', 'choppy', 'battery drain', 'overheating', 'codec issue'.
---

# Video & Mobile Playback Diagnosis Skill

Guides root-cause analysis of playback issues and file bloat using `ffprobe` metadata and mobile device constraints (especially Qualcomm Snapdragon 600-series, budget phones, and Android players).

---

## 1. Playback Lag & Choppiness Diagnosis

### Symptoms
- Video stutters, drops frames, or freezes while audio continues.
- Phone heats up or battery drains rapidly during playback.

### Root Causes & Rules

1. **Resolution & Framerate Exceeds Mobile Hardware Decoder:**
   - **4K UHD (3840x2160) or 2K (2560x1440) @ 60fps:** Mid-range chips (like Snapdragon 636) and older smartphones lack the throughput to decode 4K 60fps HEVC or H.264 smoothly in real-time.
   - **Rule:** Downscale to 1080p FHD (1920x1080) or 720p HD. Cap framerate at 60fps (or 30fps for maximum smoothness on older chips).
   - **Command Recipe:**
     ```bash
     ffmpeg -i "$INPUT" -vf "scale=-2:1080" -c:v libx264 -crf 22 -preset veryfast -pix_fmt yuv420p -c:a copy -movflags +faststart "$OUTPUT"
     ```

2. **Heavy Codec Without Dedicated Hardware Block:**
   - **HEVC (H.265) 10-bit / AV1 / VP9** played on devices with software-only decoding blocks.
   - **Rule:** Transcode to universal **H.264 (AVC) 8-bit YUV420p** (Profile: High, Level 4.1).

3. **Missing Stream Faststart Atom:**
   - On Telegram / web streaming, if `moov` atom is at the end of the file, the player must download the entire 500MB before playback can begin.
   - **Rule:** Always apply `-movflags +faststart`.

---

## 2. File Size Bloat Diagnosis

### Symptoms
- Short clip (e.g., 30s - 1min) has disproportionate size (> 300MB).
- Overall bitrate exceeds 25,000 kbps (25 Mbps).

### Root Causes & Rules

1. **Uncompressed or Intra-only Camera Output:**
   - Smartphones and screen recorders often record at unconstrained bitrates (50-100 Mbps) to minimize recording CPU usage.
   - **Rule:** Apply two-pass or CRF encoding (`-crf 20` to `23` for near-lossless clarity, `-crf 26` to `28` for compact storage).
   - **Expected Savings:** 75% to 92% reduction with zero noticeable visual loss on smartphone screens.

2. **Bloated Audio Streams:**
   - Uncompressed PCM audio (`pcm_s16le`, 1411 kbps) inside MP4/MKV.
   - **Rule:** Re-encode audio to AAC stereo at 128k (`-c:a aac -b:a 128k -ac 2`).

---

## 3. Audio & Container Compatibility

- **Issue:** Audio is silent on mobile Telegram or default gallery.
- **Cause:** Multi-channel 5.1/7.1 AC3, DTS, or Opus in MP4.
- **Rule:** Convert audio to universal stereo AAC:
  ```bash
  ffmpeg -i "$INPUT" -c:v copy -c:a aac -b:a 128k -ac 2 -movflags +faststart "$OUTPUT"
  ```
