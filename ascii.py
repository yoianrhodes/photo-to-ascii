#!/usr/bin/env python3
"""
photo-to-ascii: convert an image to ASCII art, or stream a live webcam feed.

Usage:
  python3 ascii.py <image> [options]   # single image
  python3 ascii.py --webcam [options]  # live webcam feed (q to quit)

Options:
  --width  INT    character width of output (default: 120)
  --out    FILE   save to file instead of printing (image mode only)
  --invert        invert brightness
  --color         ANSI color output (terminal only)
  --simple        simpler/blockier character ramp
  --webcam        stream live from webcam
  --cam    INT    camera index (default: 0)
  --fps    INT    target frames per second for webcam (default: 15)
"""

import argparse
import os
import re
import sys
from PIL import Image

RAMP_DETAILED = r'$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,"^`\'. '
RAMP_SIMPLE   = '@#S%?*+;:,. '


def frame_to_ascii(img, width, invert, color, ramp):
    orig_w, orig_h = img.size
    height = max(1, int((orig_h / orig_w) * width * 0.45))
    img = img.resize((width, height), Image.LANCZOS)

    if color:
        rgb = img.convert('RGB')
    gray = img.convert('L')

    lines = []
    ramp_len = len(ramp) - 1
    for y in range(height):
        row = []
        for x in range(width):
            brightness = gray.getpixel((x, y))
            if invert:
                brightness = 255 - brightness
            ch = ramp[int(brightness / 255 * ramp_len)]
            if color:
                r, g, b = rgb.getpixel((x, y))
                ch = f'\033[38;2;{r};{g};{b}m{ch}\033[0m'
            row.append(ch)
        lines.append(''.join(row))
    return lines


def run_webcam(width, invert, color, ramp, cam_index, fps):
    try:
        import cv2
    except ImportError:
        sys.exit('Error: opencv-python is required for webcam mode. Run: pip3 install opencv-python')

    import time
    import shutil

    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        sys.exit(f'Error: could not open camera {cam_index}')

    frame_delay = 1.0 / fps
    # Move cursor to top-left and hide it once; redraw in place each frame
    sys.stdout.write('\033[?25l')  # hide cursor
    sys.stdout.write('\033[2J')    # clear screen
    sys.stdout.flush()

    try:
        while True:
            t0 = time.time()
            ret, frame = cap.read()
            if not ret:
                break

            # cv2 gives BGR numpy array — convert to PIL
            frame_rgb = frame[:, :, ::-1]  # BGR → RGB
            img = Image.fromarray(frame_rgb)

            # Fit width to terminal if wider than terminal
            term_w = shutil.get_terminal_size((80, 24)).columns
            w = min(width, term_w)

            lines = frame_to_ascii(img, w, invert, color, ramp)

            # Jump to top-left and overwrite — no flicker clear
            sys.stdout.write('\033[H')
            sys.stdout.write('\n'.join(lines))
            sys.stdout.write('\033[K\n\033[K')  # clear any leftover lines
            sys.stdout.flush()

            elapsed = time.time() - t0
            wait = frame_delay - elapsed
            if wait > 0:
                time.sleep(wait)

            # Non-blocking key check via cv2 (1 ms)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # q or Esc
                break
    finally:
        cap.release()
        sys.stdout.write('\033[?25h')  # restore cursor
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()
        print('Webcam closed.')


def main():
    parser = argparse.ArgumentParser(description='Convert a photo to ASCII art, or stream a live webcam.')
    parser.add_argument('image', nargs='?', help='path to image file')
    parser.add_argument('--width', type=int, default=120, help='output width in characters (default 120)')
    parser.add_argument('--out', help='save output to a file (image mode only)')
    parser.add_argument('--invert', action='store_true', help='invert brightness')
    parser.add_argument('--color', action='store_true', help='ANSI color output')
    parser.add_argument('--simple', action='store_true', help='simpler character ramp')
    parser.add_argument('--webcam', action='store_true', help='stream live from webcam')
    parser.add_argument('--cam', type=int, default=0, help='camera index (default 0)')
    parser.add_argument('--fps', type=int, default=15, help='target FPS for webcam (default 15)')
    args = parser.parse_args()

    ramp = RAMP_SIMPLE if args.simple else RAMP_DETAILED

    if args.webcam:
        print('Starting webcam… press q or Esc to quit.')
        run_webcam(args.width, args.invert, args.color, ramp, args.cam, args.fps)
        return

    if not args.image:
        parser.print_help()
        sys.exit(1)

    if not os.path.isfile(args.image):
        sys.exit(f'Error: file not found: {args.image}')

    img = Image.open(args.image)
    lines = frame_to_ascii(img, args.width, args.invert, args.color, ramp)
    art = '\n'.join(lines)

    if args.out:
        clean = re.sub(r'\033\[[^m]*m', '', art)
        with open(args.out, 'w') as f:
            f.write(clean + '\n')
        print(f'Saved to {args.out}')
    else:
        print(art)


if __name__ == '__main__':
    main()
