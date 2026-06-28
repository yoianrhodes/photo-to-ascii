// ============================================================================
//  Photo-to-ASCII  —  real-time ASCII video filter for OBS
// ----------------------------------------------------------------------------
//  Drop this on ANY OBS source as a filter using the free "obs-shaderfilter"
//  plugin (Filters ▸ + ▸ User-defined shader ▸ Load shader file).
//
//  The whole conversion runs on the GPU, one pass, no CPU readback — so it
//  keeps up with live video at full frame-rate and stacks cheaply with other
//  sources.  All controls below appear automatically as sliders / dropdowns in
//  the filter panel.
//
//  Controls
//    • Character Size      px per character cell  → the INPUT RESOLUTION the
//                          image is sampled at (bigger = chunkier / fewer chars)
//    • Character Count     how many distinct glyphs map the brightness ramp
//    • Contrast / Invert   shape the brightness → glyph mapping
//    • Use Source Colors   colour each glyph from the video, or use Text Color
//    • Show Source In Gaps overlay glyphs on the footage, vs. on a flat colour
//    • Background Color    gap fill when not showing source (alpha = transparent,
//                          so glyph gaps reveal the OBS layers below)
//    • Blend Mode          how the glyph ink combines with what's behind it
//    • Opacity             master mix — 0 = original video, 1 = full effect
//
//  Glyph atlas is procedural (5×5 bitmask per character, after movAX13h's
//  classic ASCII-art shader) so there is no external font/texture to ship.
// ============================================================================

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
    int maximum = 9;
    int step = 1;
> = 8;

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
> = {1.0, 1.0, 1.0, 1.0};

uniform bool background_video<
    string label = "Show Source In Gaps";
> = false;

uniform float4 bg_color<
    string label = "Background Color";
    string widget_type = "color";
> = {0.0, 0.0, 0.0, 1.0};

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

// ── Procedural glyph atlas ───────────────────────────────────────────────────
// Each constant encodes a 5×5 on/off bitmap (bit = x + 5*y). Ordered from least
// ink (space) to most ink (#). All values stay below 2^24 so they are exactly
// representable in fp32 across D3D/GL backends.

float glyph_value(int gi)
{
    if (gi <= 0) return 0.0;          // ' ' (space)
    if (gi == 1) return 4096.0;       // '.'
    if (gi == 2) return 65600.0;      // ':'
    if (gi == 3) return 163153.0;     // '*'
    if (gi == 4) return 15255086.0;   // 'o'
    if (gi == 5) return 13121101.0;   // '&'
    if (gi == 6) return 15252014.0;   // '8'
    if (gi == 7) return 13195790.0;   // '@'
    return 11512810.0;                // '#'  (gi == 8, densest)
}

// Returns 1.0 if the bitmap pixel for glyph `n` is lit at local position `p`
// (p in [-0.5, 0.5]; y is flipped so glyphs render upright in OBS' top-down UV).
float glyph_pixel(float n, float2 p)
{
    p = floor(p * float2(4.0, -4.0) + 2.5);
    if (p.x < 0.0 || p.x > 4.0 || p.y < 0.0 || p.y > 4.0)
        return 0.0;
    float bit = p.x + 5.0 * p.y;            // 0..24
    return floor(fmod(n / exp2(bit), 2.0));
}

// ── Blend ────────────────────────────────────────────────────────────────────
// Combine glyph ink `f` with whatever is behind it `b`.

float3 apply_blend(int mode, float3 b, float3 f)
{
    if (mode == 1) return saturate(b + f);                       // Additive
    if (mode == 2) return 1.0 - (1.0 - b) * (1.0 - f);           // Screen
    if (mode == 3) return b * f;                                 // Multiply
    if (mode == 4)                                               // Overlay
    {
        float3 lo = 2.0 * b * f;
        float3 hi = 1.0 - 2.0 * (1.0 - b) * (1.0 - f);
        return lerp(lo, hi, step(0.5, b));
    }
    if (mode == 5) return abs(b - f);                            // Difference
    return f;                                                    // Normal
}

// ── Main ─────────────────────────────────────────────────────────────────────

float4 mainImage(VertData v_in) : TARGET
{
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

    // Quantise brightness into `char_count` levels, then pick the matching glyph
    // from the 9-entry ramp.
    int   n     = clamp(char_count, 1, 9);
    float level = min(floor(lum * float(n)), float(n - 1));      // 0 .. n-1
    float t     = (n > 1) ? level / float(n - 1) : 0.0;          // 0 .. 1
    int   gi    = int(floor(t * 8.0 + 0.5));                     // 0 .. 8

    // Local position inside this cell, mapped to the glyph's [-0.5,0.5] space.
    float2 local = frac(v_in.uv * grid) - 0.5;
    float  ink   = glyph_pixel(glyph_value(gi), local);

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
}
