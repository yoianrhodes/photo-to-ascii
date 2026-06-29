#!/usr/bin/env python3
"""
build_ascii_shader.py — bake a custom character set into the OBS ASCII shader.

A GPU shader has no font, so it can't read characters you type at runtime. This
script rasterizes *whichever characters you choose* from a real font into the
shader's procedural glyph bitmaps and writes a ready-to-load `.shader` file.

Examples
  # Classic 10-step ramp (the shipped default)
  python3 build_ascii_shader.py --chars "@%#*+=-:. "

  # Just the characters you want, your own font, finer glyphs
  python3 build_ascii_shader.py --chars "01" --font /path/to/Font.ttf
  python3 build_ascii_shader.py --chars "MWNXKkxocl:. " --width 8 --height 10

Then in OBS: Filters ▸ + ▸ User-defined shader ▸ load the generated file.

Notes
  * Characters are automatically ordered dark→light by how much ink each one
    has, so type them in any order (use --keep-order to keep your order).
  * Include a space in --chars to leave the darkest cells blank.
  * Each glyph is a GW×GH on/off bitmap packed into the shader; keep
    GW*GH <= 72 (e.g. up to 8×9). Bigger = crisper glyphs, same runtime cost.
"""

import argparse
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Monospace fonts to try when --font isn't given (first that loads wins).
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Courier.ttc",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
]

DEFAULT_CHARS = "@%#*+=-:. "
TEMPLATE_PATH = None  # built inline below

MAX_CELLS = 72  # 3 × 24-bit chunks packed into a float3 in the shader


def load_font(path, px):
    if path:
        return ImageFont.truetype(path, px)
    for cand in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            continue
    raise SystemExit(
        "No usable monospace font found. Pass one with --font /path/to/Font.ttf"
    )


def rasterize(ch, font_path, gw, gh, supersample, threshold):
    """Return (bitmap GHxGW bool array, ink fraction 0..1) for one character.

    The glyph is rendered into its monospace em-cell (advance width × ascent+
    descent) at its true typographic position, then squashed to fit GW×GH. This
    keeps relative density and vertical placement (so '.' sits low, caps fill
    up) while filling the cell horizontally instead of wasting side-bearing.
    """
    ss = supersample
    px = gh * ss
    font = load_font(font_path, px)

    ascent, descent = font.getmetrics()
    cell_h = max(1, ascent + descent)
    advance = font.getlength("M") or px * 0.6          # monospace cell width
    cell_w = max(1, int(round(advance)))

    img = Image.new("L", (cell_w, cell_h), 0)
    draw = ImageDraw.Draw(img)
    # Horizontal middle, vertical baseline: positions the glyph in the em-cell.
    draw.text((cell_w / 2.0, ascent), ch, fill=255, font=font, anchor="ms")

    small = np.asarray(img.resize((gw, gh), Image.LANCZOS), dtype=np.float32) / 255.0
    bitmap = small >= threshold
    return bitmap, float(small.mean())


def pack_glyph(bitmap, gw, gh):
    """Pack a GHxGW bool bitmap into three <=24-bit chunks (float3 in shader).

    Bit index b = gy*GW + gx (row 0 = top, column 0 = left). Returns (cx,cy,cz).
    """
    chunks = [0, 0, 0]
    for gy in range(gh):
        for gx in range(gw):
            if bitmap[gy, gx]:
                b = gy * gw + gx
                chunks[b // 24] |= 1 << (b % 24)
    return chunks


def build_glyph_bits_fn(packed):
    """Emit the HLSL `glyph_bits` if-chain returning each glyph's packed float3."""
    lines = ["float3 glyph_bits(int gi)", "{"]
    for i, (cx, cy, cz) in enumerate(packed):
        cmp = "<=" if i == 0 else "=="
        cond = "gi <= 0" if i == 0 else f"gi == {i}"
        if i < len(packed) - 1:
            lines.append(f"    if ({cond}) return float3({cx}.0, {cy}.0, {cz}.0);")
        else:
            lines.append(f"    return float3({cx}.0, {cy}.0, {cz}.0);  // gi == {i}")
    lines.append("}")
    return "\n".join(lines)


def render_shader(chars_repr, ramp_len, gw, gh, glyph_bits_fn):
    return SHADER_TEMPLATE.format(
        chars_repr=chars_repr,
        ramp_len=ramp_len,
        gw=gw,
        gh=gh,
        default_count=ramp_len,
        glyph_bits_fn=glyph_bits_fn,
    )


def main():
    ap = argparse.ArgumentParser(
        description="Bake custom ASCII characters into the OBS ASCII shader.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--chars", default=DEFAULT_CHARS,
                    help='characters to use as the brightness ramp (default: "@%%#*+=-:. ")')
    ap.add_argument("--font", help="path to a .ttf/.otf font (default: a system monospace)")
    ap.add_argument("--width", type=int, default=8, help="glyph bitmap width in cells (default 8)")
    ap.add_argument("--height", type=int, default=8, help="glyph bitmap height in cells (default 8)")
    ap.add_argument("--supersample", type=int, default=8, help="rasterize oversampling (default 8)")
    ap.add_argument("--threshold", type=float, default=0.35, help="ink threshold 0..1 (default 0.35)")
    ap.add_argument("--keep-order", action="store_true",
                    help="keep the typed order instead of sorting dark→light by ink")
    ap.add_argument("--out", default="photo-to-ascii.shader", help="output shader path")
    args = ap.parse_args()

    gw, gh = args.width, args.height
    if gw < 2 or gh < 2:
        sys.exit("Glyph width/height must be >= 2.")
    if gw * gh > MAX_CELLS:
        sys.exit(f"Glyph is too large: {gw}x{gh}={gw*gh} cells > {MAX_CELLS}. "
                 f"Reduce --width/--height so width*height <= {MAX_CELLS}.")

    # De-duplicate while preserving first-seen order.
    seen, chars = set(), []
    for ch in args.chars:
        if ch not in seen:
            seen.add(ch)
            chars.append(ch)
    if len(chars) < 2:
        sys.exit("Need at least 2 distinct characters in --chars.")

    glyphs = []  # (char, ink, packed)
    for ch in chars:
        bitmap, ink = rasterize(ch, args.font, gw, gh, args.supersample, args.threshold)
        glyphs.append((ch, ink, pack_glyph(bitmap, gw, gh)))

    if not args.keep_order:
        glyphs.sort(key=lambda g: g[1])  # ascending ink: sparse (dark) → dense (light)

    packed = [g[2] for g in glyphs]
    ordered_chars = "".join(g[0] for g in glyphs)
    chars_repr = ordered_chars.replace("\\", "\\\\").replace("*/", "* /")

    shader = render_shader(
        chars_repr=chars_repr,
        ramp_len=len(glyphs),
        gw=gw,
        gh=gh,
        glyph_bits_fn=build_glyph_bits_fn(packed),
    )
    with open(args.out, "w") as f:
        f.write(shader)

    print(f"Wrote {args.out}")
    print(f"  Ramp ({len(glyphs)} chars, dark→light): {ordered_chars!r}")
    print(f"  Glyph resolution: {gw}x{gh}")
    print("  Load it in OBS via obs-shaderfilter (User-defined shader).")


# ── Shader template ───────────────────────────────────────────────────────────
# {{ }} are literal braces; {name} are substituted by render_shader().

SHADER_TEMPLATE = r'''// ============================================================================
//  Photo-to-ASCII  —  real-time ASCII video filter for OBS
//  Character ramp (dark -> light): {chars_repr}
//
//  GENERATED by obs/build_ascii_shader.py — re-run that script to change the
//  characters. Load on any source via the obs-shaderfilter plugin
//  (Filters > + > User-defined shader > load this file).
//
//  Runs in one GPU pass with no CPU readback, so it keeps full frame-rate on
//  live video and layers cheaply with other sources. Controls appear as
//  sliders / dropdowns in the filter panel.
// ============================================================================

#define RAMP_LEN {ramp_len}
#define GW {gw}
#define GH {gh}

// ── User controls ───────────────────────────────────────────────────────────

uniform float char_size<
    string label = "Character Size (px)";
    string widget_type = "slider";
    float minimum = 2.0;
    float maximum = 64.0;
    float step = 1.0;
> = 8.0;

uniform int char_count<
    string label = "Character Count";
    string widget_type = "slider";
    int minimum = 2;
    int maximum = RAMP_LEN;
    int step = 1;
> = {default_count};

uniform float contrast<
    string label = "Contrast";
    string widget_type = "slider";
    float minimum = 0.25;
    float maximum = 3.0;
    float step = 0.05;
> = 1.0;

uniform bool invert<
    string label = "Invert Brightness";
> = false;

uniform bool use_source_color<
    string label = "Use Source Colors";
> = true;

uniform float4 fg_color<
    string label = "Text Color";
    string widget_type = "color";
> = {{1.0, 1.0, 1.0, 1.0}};

uniform bool background_video<
    string label = "Show Source In Gaps";
> = false;

uniform float4 bg_color<
    string label = "Background Color";
    string widget_type = "color";
> = {{0.0, 0.0, 0.0, 1.0}};

uniform int blend_mode<
    string label = "Blend Mode";
    string widget_type = "select";
    int    option_0_value = 0;
    string option_0_label = "Normal";
    int    option_1_value = 1;
    string option_1_label = "Additive";
    int    option_2_value = 2;
    string option_2_label = "Screen";
    int    option_3_value = 3;
    string option_3_label = "Multiply";
    int    option_4_value = 4;
    string option_4_label = "Overlay";
    int    option_5_value = 5;
    string option_5_label = "Difference";
> = 0;

uniform float opacity<
    string label = "Opacity";
    string widget_type = "slider";
    float minimum = 0.0;
    float maximum = 1.0;
    float step = 0.01;
> = 1.0;

// ── Procedural glyph atlas (baked from your characters) ──────────────────────
// Each glyph is a GW×GH on/off bitmap packed into a float3 of 24-bit chunks.
// Bit index b = gy*GW + gx (row 0 = top, column 0 = left).

{glyph_bits_fn}

// Returns 1.0 if glyph `gi` is lit at local cell position `p` (p in [0,1)).
float glyph_pixel(int gi, float2 p)
{{
    int gx = clamp(int(floor(p.x * float(GW))), 0, GW - 1);
    int gy = clamp(int(floor(p.y * float(GH))), 0, GH - 1);
    int b = gy * GW + gx;
    float3 packed = glyph_bits(gi);
    float chunk = (b < 24) ? packed.x : ((b < 48) ? packed.y : packed.z);
    int bb = b - 24 * (b / 24);                 // b % 24
    return floor(fmod(chunk / exp2(float(bb)), 2.0));
}}

// ── Blend ────────────────────────────────────────────────────────────────────
// Combine glyph ink `f` with whatever is behind it `b`.

float3 apply_blend(int mode, float3 b, float3 f)
{{
    if (mode == 1) return saturate(b + f);                       // Additive
    if (mode == 2) return 1.0 - (1.0 - b) * (1.0 - f);           // Screen
    if (mode == 3) return b * f;                                 // Multiply
    if (mode == 4)                                               // Overlay
    {{
        float3 lo = 2.0 * b * f;
        float3 hi = 1.0 - 2.0 * (1.0 - b) * (1.0 - f);
        return lerp(lo, hi, step(0.5, b));
    }}
    if (mode == 5) return abs(b - f);                            // Difference
    return f;                                                    // Normal
}}

// ── Main ─────────────────────────────────────────────────────────────────────

float4 mainImage(VertData v_in) : TARGET
{{
    float4 src = image.Sample(textureSampler, v_in.uv);

    float cell = max(2.0, char_size);
    float2 grid = uv_size / cell;                 // virtual character grid

    // One luminance/colour sample per character cell (its centre) — this is the
    // "input resolution" the picture is read at.
    float2 cell_index  = floor(v_in.uv * grid);
    float2 cell_center = (cell_index + 0.5) / grid;
    float4 sampled     = image.Sample(textureSampler, cell_center);

    float lum = dot(sampled.rgb, float3(0.299, 0.587, 0.114));
    lum = saturate((lum - 0.5) * contrast + 0.5);
    if (invert) lum = 1.0 - lum;

    // Quantise brightness into `char_count` levels, then pick the glyph from the
    // baked ramp.
    int   n     = clamp(char_count, 2, RAMP_LEN);
    float level = min(floor(lum * float(n)), float(n - 1));      // 0 .. n-1
    float t     = level / float(n - 1);                          // 0 .. 1
    int   gi    = int(floor(t * float(RAMP_LEN - 1) + 0.5));     // 0 .. RAMP_LEN-1

    // Local position inside this cell, in [0,1).
    float2 local = frac(v_in.uv * grid);
    float  ink   = glyph_pixel(gi, local);

    // Compose: glyph ink over the chosen background.
    float3 behind_rgb = background_video ? src.rgb : bg_color.rgb;
    float  behind_a   = background_video ? src.a   : bg_color.a;

    float3 ink_color  = use_source_color ? sampled.rgb : fg_color.rgb;
    float3 lit_color  = apply_blend(blend_mode, behind_rgb, ink_color);

    float3 layer_rgb  = lerp(behind_rgb, lit_color, ink);
    float  layer_a    = max(behind_a, ink);

    // Master opacity mixes the whole result back toward the untouched source.
    float3 out_rgb = lerp(src.rgb, layer_rgb, opacity);
    float  out_a   = lerp(src.a,   layer_a,   opacity);

    return float4(out_rgb, out_a);
}}
'''


if __name__ == "__main__":
    main()
