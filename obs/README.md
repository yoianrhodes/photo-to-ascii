# Photo-to-ASCII — OBS real-time ASCII video filter

A GPU shader that turns any OBS video source into live ASCII art. It runs as a
filter, in a single GPU pass with no CPU readback, so it keeps full frame-rate
on live video and layers cheaply with your other sources.

`photo-to-ascii.shader` is a shader for the free **obs-shaderfilter** plugin —
there is nothing to compile.

## Install

1. Install **obs-shaderfilter** (Exeldro) if you don't have it:
   <https://obsproject.com/forum/resources/obs-shaderfilter.1736/>
   (or via the OBS plugin manager). Restart OBS.
2. Right-click the source you want to convert ▸ **Filters**.
3. Under *Effect Filters* click **+** ▸ **User-defined shader**.
4. Tick **Load shader text from file** and point it at
   `obs/photo-to-ascii.shader` from this repo.
5. The controls appear in the filter panel. Done — it's live.

> Tip: the filter converts whatever the source outputs, so it works on cameras,
> capture cards, media files, browser sources, **and** whole scenes
> (apply it to a *Scene* source or a nested Group to ASCII-ify a composite).

## Controls

| Control | What it does |
|---|---|
| **Character Size (px)** | Pixels per character cell = the **input resolution** the image is read at. Bigger → chunkier, fewer, larger characters. Roughly `columns = source_width / size`. |
| **Character Count** | How many distinct glyphs (1–9) map the dark→bright ramp. Fewer = harsher, more poster-ized; 9 = smoothest gradient. |
| **Contrast** | Stretches the brightness→glyph mapping. |
| **Invert Brightness** | Swaps which end of the ramp gets the dense glyphs (e.g. for light-on-dark vs dark subjects). |
| **Use Source Colors** | On: each glyph is tinted by the video. Off: every glyph uses **Text Color**. |
| **Text Color** | Glyph color when *Use Source Colors* is off. |
| **Show Source In Gaps** | On: glyphs are drawn **over the original footage**. Off: gaps are filled with **Background Color**. |
| **Background Color** | Gap fill when *Show Source In Gaps* is off. **Set its alpha to 0** to make the gaps transparent so the OBS layers *below this source* show through. |
| **Blend Mode** | How glyph ink combines with what's behind it: Normal, Additive, Screen, Multiply, Overlay, Difference. |
| **Opacity** | Master mix. `0` = untouched source, `1` = full ASCII. Animate/automate this to fade the effect in and out. |

## Layering & blending recipes

The filter is built to sit in a stack of video signals. Common setups:

- **Pure ASCII on black** (the classic look): *Show Source In Gaps* off,
  Background Color opaque black, Blend = Normal, Opacity 1.
- **ASCII overlay on your footage**: *Show Source In Gaps* **on**. Try Blend =
  *Screen* or *Additive* for glowing text that reads over the picture.
- **ASCII reveals the layers below it**: *Show Source In Gaps* off and set
  **Background Color alpha to 0**. The glyph gaps become transparent, so
  whatever sources sit under this one in the scene show through the holes.
- **Crossfade the effect**: keyframe/automate **Opacity** (e.g. with Move
  Transition) from 0 → 1.
- OBS' own per-source **Blending Mode** (right-click source ▸ Blending Mode)
  still applies on top of all this for compositing against the rest of the scene.

## Performance notes

- One texture sample per character cell + one for the source = trivially fast;
  cost is essentially independent of *Character Count*.
- Larger **Character Size** = fewer cells = even cheaper.
- Everything is procedural (the 5×5 glyph bitmaps are encoded in the shader, a
  technique adapted from movAX13h's classic ASCII-art shader), so there is no
  font/atlas texture to load.

## Related

This repo also ships a CPU/terminal ASCII converter and webcam streamer
(`ascii.py`) and a data-moshing GUI (`gui.py`); the OBS shader is the
real-time, GPU path of the same idea.
