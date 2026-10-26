---
name: creative_fx
description: Use this skill for creative video modifications — reverse video, change aspect ratio (9:16 vertical, 16:9 widescreen, 1:1 square, 4:5 portrait), crop, pad, blur borders, change playback speed, black and white / grayscale, sepia, color filters, and flip/rotate. Triggers on 'reverse', 'play backwards', 'aspect ratio', 'change ratio', '9:16', '16:9', 'crop', 'vertical video', 'tiktok ratio', 'reels ratio', 'pad', 'slow motion', 'speed up', 'black and white', 'b&w', 'grayscale', 'greyscale', 'monochrome', 'sepia', 'vintage', 'saturation', 'brightness', 'contrast', 'mirror', 'flip', 'rotate'.
---

# Creative Effects & Transformations Skill

Provides validated FFmpeg recipes and mobile host memory guardrails for aspect ratio conversions, video reversing, color grading, and creative effects.

---

## 1. Memory Safety Guardrails (CRITICAL FOR MOBILE / TERMUX HOSTS)

### The Video Reversal RAM Trap (`-vf reverse`)
- **FFmpeg Mechanism:** The `reverse` filter uncompresses and buffers EVERY single frame into system RAM before outputting the reversed sequence in one pass.
- **RAM Calculation:**
  - `1080p (1920x1080) @ 60fps YUV420p` ≈ 186 MB of raw RAM per second of video.
  - A 10-second 1080p 60fps video requires **~1.8 GB of free RAM**!
  - On a Nokia 6.1 Plus / Termux host with only 1.6 GB free RAM, reversing a video > 10s at 1080p **WILL TRIGGER ANDROID LOW MEMORY KILLER (LMK) AND CRASH THE BOT**.

### Enforcement Rules:
1. **Duration & Resolution Thresholds for `reverse`:**
   - **If duration > 30 seconds:** Reject full uncompressed reverse or require trimming first.
   - **If resolution > 720p (e.g. 1080p or 4K):** Downscale to 720p (or 480p) before applying reverse, or use two-pass segmenting.
   - **Audio:** Always reverse audio with `-af areverse` in tandem.
2. **Safe Reversal Command:**
   ```bash
   # Safe downscaled reverse for mobile host:
   ffmpeg -i "$INPUT" -vf "scale=-2:720,reverse" -af "areverse" -c:v libx264 -crf 22 -preset veryfast -pix_fmt yuv420p -movflags +faststart "$OUTPUT"
   ```

---

## 2. Aspect Ratio Transformations

### A. Convert to 9:16 (TikTok, Instagram Reels, YouTube Shorts)

#### Method 1: Blurred Background Padding (Recommended - No Content Cut)
Keeps the entire original 16:9 video centered with an aesthetically blurred background filling the 9:16 frame:
```bash
ffmpeg -i "$INPUT" -filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2" -c:v libx264 -crf 22 -preset veryfast -c:a copy -movflags +faststart "$OUTPUT"
```

#### Method 2: Black Bars / Pillarbox Padding (Fastest, zero distortion)
Pads original video with black borders to 9:16:
```bash
ffmpeg -i "$INPUT" -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" -c:v libx264 -crf 22 -preset veryfast -c:a copy -movflags +faststart "$OUTPUT"
```

#### Method 3: Center Crop (Fills screen completely, crops sides)
```bash
ffmpeg -i "$INPUT" -vf "crop=ih*9/16:ih" -c:v libx264 -crf 22 -preset veryfast -c:a copy -movflags +faststart "$OUTPUT"
```

---

### B. Convert to 16:9 (Standard Widescreen / TV)

#### Method 1: Pillarbox to 16:9 (Black bars on sides for vertical video)
```bash
ffmpeg -i "$INPUT" -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black" -c:v libx264 -crf 22 -preset veryfast -c:a copy -movflags +faststart "$OUTPUT"
```

---

### C. Convert to 1:1 Square (Instagram Post)

#### Center Crop to 1:1 Square:
```bash
ffmpeg -i "$INPUT" -vf "crop=min(iw\,ih):min(iw\,ih)" -c:v libx264 -crf 22 -preset veryfast -c:a copy -movflags +faststart "$OUTPUT"
```

---

## 3. Speed Adjustments (Slow Motion & Speed Up)

### Double Speed (2x Fast Forward):
```bash
ffmpeg -i "$INPUT" -filter_complex "[0:v]setpts=0.5*PTS[v];[0:a]atempo=2.0[a]" -map "[v]" -map "[a]" -c:v libx264 -crf 22 -preset veryfast -movflags +faststart "$OUTPUT"
```

### Half Speed (0.5x Smooth Slow Motion):
```bash
ffmpeg -i "$INPUT" -filter_complex "[0:v]setpts=2.0*PTS[v];[0:a]atempo=0.5[a]" -map "[v]" -map "[a]" -c:v libx264 -crf 22 -preset veryfast -movflags +faststart "$OUTPUT"
```

---

## 4. Color, Grayscale & Aesthetic Filters

### A. Black and White / Grayscale (Desaturate)
Converts video to clean monochromatic black and white while preserving crisp audio:
```bash
ffmpeg -i "$INPUT" -vf "hue=s=0" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### B. Sepia / Vintage Film Tone
Applies a warm vintage cinematic tone:
```bash
ffmpeg -i "$INPUT" -vf "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### C. Vibrant / Boost Saturation (+50%)
```bash
ffmpeg -i "$INPUT" -vf "hue=s=1.5" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### D. Enhance Brightness & Contrast
```bash
ffmpeg -i "$INPUT" -vf "eq=brightness=0.06:contrast=1.15" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

---

## 5. Orientation, Flip & Mirroring

### A. Horizontal Flip (Mirror Camera Selfie)
```bash
ffmpeg -i "$INPUT" -vf "hflip" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### B. Vertical Flip (Invert Upside-Down)
```bash
ffmpeg -i "$INPUT" -vf "vflip" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### C. Rotate 90 Degrees Clockwise
```bash
ffmpeg -i "$INPUT" -vf "transpose=1" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

### D. Rotate 90 Degrees Counter-Clockwise
```bash
ffmpeg -i "$INPUT" -vf "transpose=2" -c:v libx264 -crf 22 -preset fast -c:a copy -movflags +faststart "$OUTPUT"
```

---

## 6. Impossible & Unrealistic Requests (Strict Guardrails)

### A. 4K / UHD Upscaling Refusal
- **Never Fake Upscale:** FFmpeg cannot synthesize optical detail that was never recorded by the physical camera sensor. Upscaling 1080p, 720p, or SD video to 4K (`scale=3840:2160`) simply inflates file size to hundreds of megabytes with zero gain in sharpness, and crashes mobile hardware decoders.
- **Action:** Reject the request in plain English. State that upscaling 1080p to 4K is an unrealistic demand that will not improve visual detail, and offer to optimize or apply filters at native resolution.

### B. Audio Recovery from Silent Video
- If a source video has no audio track (`has_audio: False` or `audio_codec: None`), explain that no audio stream exists to extract or recover unless the user uploads a new audio file.

### C. Generative Inpainting & Subject Removal
- Reject requests asking to remove background objects, change actors, or alter camera angles, as FFmpeg is a media processing tool rather than a generative diffusion model.


