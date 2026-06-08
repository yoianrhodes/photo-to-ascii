"""
Data moshing effects pipeline.

Each effect: fn(arr: uint8 ndarray, state: dict, param: float) -> uint8 ndarray
State persists across frames (needed for temporal effects).
"""

import numpy as np


# ── Individual effects ────────────────────────────────────────────────────────

def p_bleed(arr, state, strength=0.7):
    """P-frame bleed: pixels persist where inter-frame motion is below threshold."""
    f = arr.astype(np.float32)
    if 'prev' not in state or state['prev'].shape != f.shape:
        state['prev'] = f.copy()
        return arr
    prev = state['prev']
    diff = np.abs(f - prev).max(axis=2)          # (H, W)
    threshold = (1.0 - float(strength)) * 128.0
    mask = (diff < threshold)[:, :, np.newaxis]
    result = np.where(mask, prev, f)
    state['prev'] = result.copy()
    return np.clip(result, 0, 255).astype(np.uint8)


def frame_echo(arr, state, decay=0.5):
    """Frame echo: exponential-decay temporal ghosting."""
    f = arr.astype(np.float32)
    if 'acc' not in state or state['acc'].shape != f.shape:
        state['acc'] = f.copy()
        return arr
    d = float(decay)
    state['acc'] = state['acc'] * d + f * (1.0 - d)
    return np.clip(state['acc'], 0, 255).astype(np.uint8)


def i_freeze(arr, state, hold=15):
    """I-frame freeze: hold a keyframe for N frames to create a stutter."""
    h = max(1, int(hold))
    if 'frozen' not in state or state['frozen'].shape != arr.shape:
        state['frozen'] = arr.copy()
        state['count'] = 0
        return arr
    count = state.get('count', 0)
    if count >= h:
        # Capture new keyframe and display it this cycle
        state['frozen'] = arr.copy()
        state['count'] = 0
        return arr
    state['count'] = count + 1
    return state['frozen']


def block_corrupt(arr, state, intensity=0.3):
    """Block corruption: scramble random 16×16 DCT-like blocks."""
    if intensity <= 0:
        return arr
    result = arr.copy()
    H, W = arr.shape[:2]
    bs = 16
    rows, cols = max(1, H // bs), max(1, W // bs)
    n = max(1, int(float(intensity) * rows * cols * 0.6))
    rng = np.random.default_rng()
    for _ in range(n):
        by = int(rng.integers(0, rows)) * bs
        bx = int(rng.integers(0, cols)) * bs
        block = result[by:by+bs, bx:bx+bs]
        op = int(rng.integers(0, 4))
        if op == 0:
            result[by:by+bs, bx:bx+bs] = np.roll(block, int(rng.integers(1, bs)), axis=0)
        elif op == 1:
            result[by:by+bs, bx:bx+bs] = np.roll(block, int(rng.integers(1, bs)), axis=1)
        elif op == 2:
            result[by:by+bs, bx:bx+bs] = block[::-1]
        else:
            result[by:by+bs, bx:bx+bs] = block[:, ::-1]
    return result


def pixel_shift(arr, state, amount=10):
    """Pixel shift: animated sinusoidal row displacement."""
    if amount <= 0:
        return arr
    state['phase'] = state.get('phase', 0.0) + 0.15
    H, W = arr.shape[:2]
    shifts = (float(amount) * np.sin(
        np.arange(H, dtype=np.float32) * 0.08 + state['phase']
    )).astype(np.int32)
    col_idx = (np.arange(W, dtype=np.int32)[np.newaxis, :] - shifts[:, np.newaxis]) % W
    return arr[np.arange(H, dtype=np.int32)[:, np.newaxis], col_idx]


def channel_shift(arr, state, offset=10):
    """Channel shift: offset R and B channels in opposite horizontal directions."""
    if offset <= 0:
        return arr
    o = int(offset)
    result = arr.copy()
    result[:, :, 0] = np.roll(arr[:, :, 0],  o, axis=1)
    result[:, :, 2] = np.roll(arr[:, :, 2], -o, axis=1)
    return result


# ── Registry ──────────────────────────────────────────────────────────────────
# (id, display label, function, (param_label, lo, hi, default, resolution))

EFFECTS = [
    ('p_bleed',       'P-frame Bleed',   p_bleed,       ('Strength',  0.0, 1.0,  0.7,  0.05)),
    ('frame_echo',    'Frame Echo',      frame_echo,    ('Decay',     0.0, 0.99, 0.5,  0.01)),
    ('i_freeze',      'I-frame Freeze',  i_freeze,      ('Hold (f)',  1,   60,   15,   1   )),
    ('block_corrupt', 'Block Corrupt',   block_corrupt, ('Intensity', 0.0, 1.0,  0.3,  0.05)),
    ('pixel_shift',   'Pixel Shift',     pixel_shift,   ('Amount',    0,   80,   10,   1   )),
    ('channel_shift', 'Channel Shift',   channel_shift, ('Offset',    0,   60,   10,   1   )),
]

_FN_MAP = {eid: fn for eid, _, fn, _ in EFFECTS}


def apply_pipeline(img, order, enabled, params, state):
    """
    Apply enabled mosh effects to a PIL image in the given order.
    Returns a new PIL Image.
    """
    from PIL import Image as _Image
    arr = np.asarray(img.convert('RGB')).copy()
    for eid in order:
        if not enabled.get(eid, False):
            continue
        fn = _FN_MAP[eid]
        st = state.setdefault(eid, {})
        param_val = params.get(eid)
        arr = fn(arr, st, param_val) if param_val is not None else fn(arr, st)
    return _Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
