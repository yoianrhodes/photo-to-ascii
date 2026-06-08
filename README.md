# photo-to-ascii

Convert any image to ASCII art in your terminal — or stream a live datamosh webcam feed.

## Features

- Single image → ASCII output (print or save to file)
- Live webcam stream rendered as ASCII in-place (no flicker)
- ANSI true-color mode
- Invertible brightness, detailed or simple character ramp
- Real-time datamosh effects pipeline (P-frame bleed, frame echo, block corrupt, and more)

## Requirements

```bash
pip install Pillow
pip install opencv-python   # webcam mode only
```

## Usage

```bash
# Convert an image
python3 ascii.py photo.jpg

# Save to file
python3 ascii.py photo.jpg --out art.txt

# Color output, inverted, wider
python3 ascii.py photo.jpg --color --invert --width 200

# Live webcam feed
python3 ascii.py --webcam --color

# All options
python3 ascii.py --help
```

## Datamosh GUI

`gui.py` opens a Tkinter window with live webcam preview and toggleable datamosh effects:

```bash
python3 gui.py
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--width INT` | 120 | Output width in characters |
| `--out FILE` | — | Save to file instead of printing |
| `--invert` | off | Invert brightness |
| `--color` | off | ANSI true-color output |
| `--simple` | off | Blockier character ramp |
| `--webcam` | off | Stream live from webcam |
| `--cam INT` | 0 | Camera index |
| `--fps INT` | 15 | Target FPS for webcam |
