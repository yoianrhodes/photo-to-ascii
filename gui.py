#!/usr/bin/env python3
"""
photo-to-ascii + data moshing GUI
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

import mosh as _mosh

RAMP_DETAILED = r'$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,"^`\'. '
RAMP_SIMPLE   = '@#S%?*+;:,. '
FONT_PATH     = '/System/Library/Fonts/Courier.ttc'


# ── ASCII rendering helpers ───────────────────────────────────────────────────

@lru_cache(maxsize=16)
def _load_font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


@lru_cache(maxsize=16)
def _char_dims(size: int):
    font = _load_font(size)
    ch_w = max(1, int(font.getlength('M')))
    asc, desc = font.getmetrics()
    return ch_w, max(1, asc + desc)


@lru_cache(maxsize=32)
def _build_sprites(font_size: int, ramp: str) -> np.ndarray:
    font = _load_font(font_size)
    ch_w, ch_h = _char_dims(font_size)
    sprites = np.zeros((len(ramp), ch_h, ch_w), dtype=np.float32)
    for i, ch in enumerate(ramp):
        img = Image.new('L', (ch_w, ch_h), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        sprites[i] = np.asarray(img) / 255.0
    return sprites


def image_to_ascii(src: Image.Image, num_chars: int, invert: bool,
                   color: bool, ramp: str,
                   brightness: float, contrast: float,
                   font_size: int) -> Image.Image:
    if brightness != 1.0:
        src = ImageEnhance.Brightness(src).enhance(brightness)
    if contrast != 1.0:
        src = ImageEnhance.Contrast(src).enhance(contrast)

    src = src.convert('RGB')
    orig_w, orig_h = src.size
    num_rows = max(1, int((orig_h / orig_w) * num_chars * 0.45))

    small  = src.resize((num_chars, num_rows), Image.LANCZOS)
    gray   = np.asarray(small.convert('L'), dtype=np.float32)

    if invert:
        gray = 255.0 - gray

    sprites  = _build_sprites(font_size, ramp)
    ch_w, ch_h = _char_dims(font_size)

    idx   = np.clip((gray / 255.0 * (len(ramp) - 1)).astype(np.int32), 0, len(ramp) - 1)
    tiles = sprites[idx]                                          # (rows, cols, ch_h, ch_w)

    if color:
        rgb     = np.asarray(small, dtype=np.float32)
        colored = tiles[:, :, :, :, np.newaxis] * rgb[:, :, np.newaxis, np.newaxis, :]
    else:
        mono    = tiles * 200.0
        colored = mono[:, :, :, :, np.newaxis].repeat(3, axis=4)

    out_arr = (colored.transpose(0, 2, 1, 3, 4)
                      .reshape(num_rows * ch_h, num_chars * ch_w, 3))
    canvas  = np.full_like(np.clip(out_arr, 0, 255).astype(np.uint8), 10)
    out_u8  = np.clip(out_arr, 0, 255).astype(np.uint8)
    mask    = out_u8.any(axis=2, keepdims=True)
    return Image.fromarray(np.where(mask, out_u8, canvas))


# ── App ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Photo → ASCII')
        self.configure(bg='#1e1e1e')
        self.minsize(900, 600)

        # Source / render state
        self._cam_running   = False
        self._cam_thread    = None
        self._current_src   = None
        self._latest_frame  = None
        self._frame_lock    = threading.Lock()
        self._rendering     = False
        self._after_id      = None
        self._tk_image      = None
        self._last_art      = None

        # Mosh state
        self._mosh_order    = [eid for eid, *_ in _mosh.EFFECTS]
        self._mosh_enabled  = {eid: tk.BooleanVar(value=False) for eid, *_ in _mosh.EFFECTS}
        self._mosh_params   = {eid: tk.DoubleVar(value=dflt)
                               for eid, _, _, (_, lo, hi, dflt, res) in _mosh.EFFECTS}
        self._mosh_state    = {}
        self._mosh_on       = tk.BooleanVar(value=False)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Main area: canvas left, mosh panel right (hidden by default)
        self._main = tk.Frame(self, bg='#1e1e1e')
        self._main.pack(side='top', fill='both', expand=True)

        # Canvas
        cf = tk.Frame(self._main, bg='#1e1e1e')
        cf.pack(side='left', fill='both', expand=True)
        self._canvas = tk.Canvas(cf, bg='#1e1e1e', highlightthickness=0, cursor='crosshair')
        vbar = tk.Scrollbar(cf, orient='vertical',   command=self._canvas.yview)
        hbar = tk.Scrollbar(cf, orient='horizontal', command=self._canvas.xview)
        self._canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side='right',  fill='y')
        hbar.pack(side='bottom', fill='x')
        self._canvas.pack(fill='both', expand=True)
        self._canvas_img_id = self._canvas.create_image(0, 0, anchor='nw')
        self._canvas.bind('<Configure>', lambda e: self._schedule_render())

        # Mosh panel (right side, hidden initially)
        self._mosh_panel = tk.Frame(self._main, bg='#1a1a2e', width=270)
        self._mosh_panel.pack_propagate(False)
        self._build_mosh_panel()

        # ── Bottom bar — two rows ─────────────────────────────────────────────
        bar = tk.Frame(self, bg='#2d2d2d')
        bar.pack(side='bottom', fill='x')

        # Row 1: source + sliders + action buttons
        row1 = tk.Frame(bar, bg='#2d2d2d')
        row1.pack(fill='x', padx=4, pady=(6, 2))

        # Source buttons
        src = tk.Frame(row1, bg='#2d2d2d')
        src.pack(side='left', padx=8)
        tk.Label(src, text='Source', bg='#2d2d2d', fg='#888',
                 font=('Helvetica', 9)).pack(anchor='w')
        brow = tk.Frame(src, bg='#2d2d2d')
        brow.pack()
        self._btn_open = tk.Button(brow, text='Open Image', command=self._open_image,
                                   bg='#0a84ff', fg='white', relief='flat', padx=8,
                                   activebackground='#0060cc', cursor='hand2')
        self._btn_open.pack(side='left', padx=(0, 4))
        self._btn_cam = tk.Button(brow, text='▶  Webcam', command=self._toggle_webcam,
                                  bg='#30d158', fg='white', relief='flat', padx=8,
                                  activebackground='#25a244', cursor='hand2')
        self._btn_cam.pack(side='left')

        ttk.Separator(row1, orient='vertical').pack(side='left', fill='y', pady=2, padx=6)

        # Sliders
        self._width_var    = tk.IntVar(value=150)
        self._font_var     = tk.IntVar(value=10)
        self._bright_var   = tk.DoubleVar(value=1.0)
        self._contrast_var = tk.DoubleVar(value=1.0)

        for label, var, lo, hi, res in [
            ('Width (chars)',  self._width_var,    40, 320,  1),
            ('Font size (px)', self._font_var,      4,  24,  1),
            ('Brightness',     self._bright_var,   0.2, 3.0, 0.05),
            ('Contrast',       self._contrast_var, 0.2, 3.0, 0.05),
        ]:
            self._make_slider(row1, label, var, lo, hi, res)

        # Action buttons (right side of row 1)
        acts = tk.Frame(row1, bg='#2d2d2d')
        acts.pack(side='right', padx=10)
        self._btn_mosh = tk.Button(acts, text='Mosh ▸', command=self._toggle_mosh_panel,
                                   bg='#ff9f0a', fg='black', relief='flat', padx=10,
                                   activebackground='#cc7a00', cursor='hand2')
        self._btn_mosh.pack(side='left', padx=4)
        tk.Button(acts, text='Export PNG', command=self._save_png,
                  bg='#5856d6', fg='white', relief='flat', padx=10,
                  activebackground='#3a38a8', cursor='hand2').pack(side='left', padx=4)

        ttk.Separator(bar, orient='horizontal').pack(fill='x', padx=4)

        # Row 2: options checkboxes + status
        row2 = tk.Frame(bar, bg='#262626')
        row2.pack(fill='x', padx=4, pady=(2, 6))

        self._color_var  = tk.BooleanVar(value=False)
        self._invert_var = tk.BooleanVar(value=False)
        self._simple_var = tk.BooleanVar(value=False)
        self._raw_var    = tk.BooleanVar(value=False)

        tk.Label(row2, text='Options:', bg='#262626', fg='#888',
                 font=('Helvetica', 9)).pack(side='left', padx=(10, 4))
        for label, var in [('Color',         self._color_var),
                            ('Invert',        self._invert_var),
                            ('Simple ramp',   self._simple_var),
                            ('Raw (no ASCII)', self._raw_var)]:
            tk.Checkbutton(row2, text=label, variable=var,
                           bg='#262626', fg='#cccccc', selectcolor='#444',
                           activebackground='#262626', activeforeground='white',
                           command=self._schedule_render).pack(side='left', padx=6)

        self._status_var = tk.StringVar(value='Open an image or start the webcam.')
        tk.Label(row2, textvariable=self._status_var,
                 bg='#262626', fg='#666', font=('Helvetica', 9),
                 anchor='e').pack(side='right', padx=10)

    def _make_slider(self, parent, label, var, lo, hi, res):
        grp = tk.Frame(parent, bg='#2d2d2d')
        grp.pack(side='left', padx=6)
        tk.Label(grp, text=label, bg='#2d2d2d', fg='#aaa',
                 font=('Helvetica', 9)).pack(anchor='w')
        tk.Scale(grp, variable=var, from_=lo, to=hi, resolution=res,
                 orient='horizontal', length=110,
                 bg='#2d2d2d', fg='white', troughcolor='#555',
                 highlightthickness=0, sliderlength=14,
                 command=lambda _: self._schedule_render()).pack()

    # ── Mosh panel ────────────────────────────────────────────────────────────

    def _build_mosh_panel(self):
        p = self._mosh_panel

        # Header
        hdr = tk.Frame(p, bg='#1a1a2e')
        hdr.pack(fill='x', padx=10, pady=(10, 4))
        tk.Label(hdr, text='Data Mosh', bg='#1a1a2e', fg='#ff9f0a',
                 font=('Helvetica', 13, 'bold')).pack(side='left')
        tk.Checkbutton(hdr, text='On', variable=self._mosh_on,
                       bg='#1a1a2e', fg='#cccccc', selectcolor='#333',
                       activebackground='#1a1a2e', activeforeground='white',
                       command=self._schedule_render).pack(side='right')

        ttk.Separator(p, orient='horizontal').pack(fill='x', padx=8, pady=4)

        tk.Label(p, text='Drag order with ▲ ▼  ·  toggle each effect',
                 bg='#1a1a2e', fg='#666', font=('Helvetica', 8)).pack(padx=10, anchor='w')

        # Scrollable effect list
        list_frame = tk.Frame(p, bg='#1a1a2e')
        list_frame.pack(fill='both', expand=True, padx=6, pady=4)

        self._effect_list = tk.Frame(list_frame, bg='#1a1a2e')
        self._effect_list.pack(fill='x')

        self._refresh_effect_rows()

        ttk.Separator(p, orient='horizontal').pack(fill='x', padx=8, pady=4)

        tk.Button(p, text='Clear mosh state', command=self._clear_mosh_state,
                  bg='#3a3a4a', fg='#cccccc', relief='flat', cursor='hand2').pack(pady=4)

    def _refresh_effect_rows(self):
        for w in self._effect_list.winfo_children():
            w.destroy()

        meta = {eid: (label, pspec) for eid, label, _, pspec in _mosh.EFFECTS}

        for i, eid in enumerate(self._mosh_order):
            label, (plabel, lo, hi, dflt, res) = meta[eid]
            row = tk.Frame(self._effect_list, bg='#22223a', pady=4)
            row.pack(fill='x', pady=2, padx=2)

            # Up / Down
            arrows = tk.Frame(row, bg='#22223a')
            arrows.pack(side='left', padx=(4, 2))
            tk.Button(arrows, text='▲', width=2, command=lambda idx=i: self._move_effect(idx, -1),
                      bg='#333355', fg='white', relief='flat', cursor='hand2',
                      font=('Helvetica', 8)).pack()
            tk.Button(arrows, text='▼', width=2, command=lambda idx=i: self._move_effect(idx, 1),
                      bg='#333355', fg='white', relief='flat', cursor='hand2',
                      font=('Helvetica', 8)).pack()

            # Checkbox + name
            info = tk.Frame(row, bg='#22223a')
            info.pack(side='left', fill='x', expand=True, padx=4)
            tk.Checkbutton(info, text=label,
                           variable=self._mosh_enabled[eid],
                           bg='#22223a', fg='#eeeeee', selectcolor='#444',
                           activebackground='#22223a', activeforeground='white',
                           font=('Helvetica', 10, 'bold'),
                           command=self._schedule_render).pack(anchor='w')

            # Param slider
            tk.Label(info, text=plabel, bg='#22223a', fg='#888',
                     font=('Helvetica', 8)).pack(anchor='w')
            tk.Scale(info, variable=self._mosh_params[eid],
                     from_=lo, to=hi, resolution=res,
                     orient='horizontal', length=160,
                     bg='#22223a', fg='white', troughcolor='#444',
                     highlightthickness=0, sliderlength=12,
                     command=lambda _: self._schedule_render()).pack(anchor='w')

    def _move_effect(self, idx, direction):
        order = self._mosh_order
        new_idx = idx + direction
        if 0 <= new_idx < len(order):
            order[idx], order[new_idx] = order[new_idx], order[idx]
            self._refresh_effect_rows()
            self._schedule_render()

    def _toggle_mosh_panel(self):
        if self._mosh_panel.winfo_ismapped():
            self._mosh_panel.pack_forget()
            self._btn_mosh.configure(text='Mosh ▸')
        else:
            self._mosh_panel.pack(side='right', fill='y', in_=self._main)
            self._btn_mosh.configure(text='Mosh ◂')

    def _clear_mosh_state(self):
        self._mosh_state.clear()
        self._schedule_render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _schedule_render(self, delay_ms=80):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(delay_ms, self._render)

    def _render(self):
        self._after_id = None
        src = self._current_src
        if src is None or self._rendering:
            return
        self._rendering = True
        try:
            # Apply mosh pipeline before ASCII conversion
            if self._mosh_on.get() and any(v.get() for v in self._mosh_enabled.values()):
                enabled = {eid: v.get() for eid, v in self._mosh_enabled.items()}
                params  = {eid: v.get() for eid, v in self._mosh_params.items()}
                src = _mosh.apply_pipeline(src, self._mosh_order, enabled,
                                           params, self._mosh_state)

            if self._raw_var.get():
                art = src.convert('RGB')
            else:
                ramp = RAMP_SIMPLE if self._simple_var.get() else RAMP_DETAILED
                art  = image_to_ascii(src,
                                      num_chars  = self._width_var.get(),
                                      invert     = self._invert_var.get(),
                                      color      = self._color_var.get(),
                                      ramp       = ramp,
                                      brightness = self._bright_var.get(),
                                      contrast   = self._contrast_var.get(),
                                      font_size  = self._font_var.get())

            self._last_art = art  # full-res kept for export

            # Scale to fit the canvas, preserving aspect ratio
            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            if cw > 1 and ch > 1:
                scale = min(cw / art.width, ch / art.height)
                display = art.resize(
                    (max(1, int(art.width * scale)), max(1, int(art.height * scale))),
                    Image.NEAREST)
            else:
                display = art

            tk_img = ImageTk.PhotoImage(display)
            self._tk_image = tk_img
            self._canvas.itemconfigure(self._canvas_img_id, image=tk_img)
            self._canvas.configure(scrollregion=(0, 0, display.width, display.height))

            mosh_tag = '  [MOSHED]' if self._mosh_on.get() else ''
            if self._raw_var.get():
                self._status_var.set(f'RAW  •  {art.width}×{art.height}px{mosh_tag}')
            else:
                cols = self._width_var.get()
                rows = max(1, int((src.height / src.width) * cols * 0.45))
                self._status_var.set(
                    f'{cols}×{rows} chars  •  {art.width}×{art.height}px'
                    f'  •  {self._font_var.get()}px{mosh_tag}')
        finally:
            self._rendering = False

    # ── Source ────────────────────────────────────────────────────────────────

    def _open_image(self):
        self._stop_webcam()
        path = filedialog.askopenfilename(
            filetypes=[('Images', '*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tiff'),
                       ('All', '*.*')])
        if not path:
            return
        try:
            self._current_src = Image.open(path).convert('RGB')
            self._mosh_state.clear()
            self._status_var.set(f'Loaded: {path.split("/")[-1]}')
            self._render()
        except Exception as e:
            messagebox.showerror('Error', str(e))

    def _toggle_webcam(self):
        if self._cam_running:
            self._stop_webcam()
        else:
            self._start_webcam()

    def _start_webcam(self):
        try:
            import cv2
        except ImportError:
            messagebox.showerror('Missing dependency', 'Run: pip3 install opencv-python')
            return
        self._cam_running = True
        self._mosh_state.clear()
        self._btn_cam.configure(text='⏹  Stop', bg='#ff453a', activebackground='#cc2a1f')
        self._btn_open.configure(state='disabled')
        self._cam_thread = threading.Thread(target=self._cam_loop, daemon=True)
        self._cam_thread.start()
        self.after(50, self._cam_poll)

    def _cam_loop(self):
        import cv2, time
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.after(0, lambda: messagebox.showerror(
                'Camera error',
                'Could not open camera.\n\nCheck System Settings → Privacy → Camera.'))
            self.after(0, self._stop_webcam)
            return
        while self._cam_running:
            ret, frame = cap.read()
            if not ret:
                break
            img = Image.fromarray(frame[:, :, ::-1]).convert('RGB')
            with self._frame_lock:
                self._latest_frame = img
            time.sleep(0.033)
        cap.release()

    def _cam_poll(self):
        if not self._cam_running:
            return
        if not self._rendering:
            with self._frame_lock:
                img = self._latest_frame
                self._latest_frame = None
            if img is not None:
                self._current_src = img
                self._render()
        self.after(50, self._cam_poll)

    def _stop_webcam(self):
        self._cam_running = False
        self._btn_cam.configure(text='▶  Webcam', bg='#30d158', activebackground='#25a244')
        self._btn_open.configure(state='normal')

    # ── Export ────────────────────────────────────────────────────────────────

    def _save_png(self):
        if self._last_art is None:
            messagebox.showinfo('Nothing to export', 'Render an image first.')
            return
        path = filedialog.asksaveasfilename(
            defaultextension='.png',
            filetypes=[('PNG image', '*.png'), ('All', '*.*')])
        if not path:
            return
        self._last_art.save(path)
        w, h = self._last_art.size
        self._status_var.set(f'Saved {w}×{h}px → {path.split("/")[-1]}')


if __name__ == '__main__':
    app = App()
    app.mainloop()
