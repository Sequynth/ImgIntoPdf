#!/usr/bin/env python3
"""
GUI for add_image_to_pdf.py — place an image on a PDF page by dragging it.

Usage:
    python add_image_to_pdf_gui.py [input.pdf] [image.png]

Controls:
    Drag image          move
    Drag corner handle  scale
    Mouse wheel         scale about image centre
    Arrow keys          nudge 1 pt (hold Shift for 10 pt)
    Page Up / Page Down change page
"""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageTk

from add_image_to_pdf import add_image_to_pdf

HANDLE = 10  # px, size of the corner resize handle
MIN_SCALE = 0.01
MAX_SCALE = 3.0


class PdfImagePlacer(tk.Tk):
    def __init__(self, pdf_path=None, image_path=None):
        super().__init__()
        self.title("Add Image to PDF")
        self.geometry("1000x800")

        self.pdf_path = None
        self.image_path = None
        self.doc = None
        self.page_index = 0

        # Placement state, in PDF points (x, y from bottom-left) and
        # scale as a fraction of page width — same units the CLI takes.
        self.x_pt = 0.0
        self.y_pt = 0.0
        self.scale = 0.25

        self.page_w = self.page_h = 0.0
        self.ppp = 1.0  # preview pixels per PDF point
        self.off_x = self.off_y = 0  # page origin within the canvas
        self._page_img = None  # rendered page, PIL
        self._overlay = None  # source image, PIL RGBA
        self._photo = None  # ImageTk ref, must outlive the draw call
        self._drag = None
        self._resize_job = None

        self._build_widgets()

        if pdf_path:
            self.load_pdf(pdf_path)
        if image_path:
            self.load_image(image_path)

    # ---------------------------------------------------------------- widgets

    def _build_widgets(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="Open PDF…", command=self.choose_pdf).pack(side=tk.LEFT)
        ttk.Button(bar, text="Open Image…", command=self.choose_image).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(bar, text="◀", width=3, command=lambda: self.goto_page(self.page_index - 1)).pack(side=tk.LEFT)
        self.page_label = ttk.Label(bar, text="— / —", width=10, anchor=tk.CENTER)
        self.page_label.pack(side=tk.LEFT)
        ttk.Button(bar, text="▶", width=3, command=lambda: self.goto_page(self.page_index + 1)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(bar, text="Scale").pack(side=tk.LEFT)
        self.scale_var = tk.DoubleVar(value=self.scale * 100)
        self.scale_slider = ttk.Scale(
            bar, from_=MIN_SCALE * 100, to=MAX_SCALE * 100,
            variable=self.scale_var, command=self._on_slider, length=180,
        )
        self.scale_slider.pack(side=tk.LEFT, padx=4)
        self.scale_label = ttk.Label(bar, text="25%", width=6)
        self.scale_label.pack(side=tk.LEFT)

        ttk.Button(bar, text="Center", command=self.center_image).pack(side=tk.LEFT, padx=12)
        ttk.Button(bar, text="Save As…", command=self.save).pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(self, bg="#555", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.status = ttk.Label(self, text="Open a PDF and an image to begin.", anchor=tk.W, padding=(8, 3))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_hover)

        self.bind("<Prior>", lambda e: self.goto_page(self.page_index - 1))
        self.bind("<Next>", lambda e: self.goto_page(self.page_index + 1))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, 1), ("Down", 0, -1)):
            self.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self._nudge(dx, dy))
            self.bind(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))

    # ------------------------------------------------------------------ files

    def choose_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if path:
            self.load_pdf(path)

    def choose_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff"), ("All files", "*.*")]
        )
        if path:
            self.load_image(path)

    def load_pdf(self, path):
        try:
            doc = pdfium.PdfDocument(path)
            len(doc)  # force a parse now rather than on first render
        except Exception as exc:
            messagebox.showerror("Cannot open PDF", str(exc))
            return
        if self.doc is not None:
            self.doc.close()
        self.doc = doc
        self.pdf_path = path
        self.page_index = 0
        self.title(f"Add Image to PDF — {Path(path).name}")
        self.goto_page(0, recenter=True)

    def load_image(self, path):
        try:
            self._overlay = PILImage.open(path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("Cannot open image", str(exc))
            return
        self.image_path = path
        if self.doc is not None:
            self.center_image()
        self.redraw()

    # ------------------------------------------------------------------ pages

    def goto_page(self, index, recenter=False):
        if self.doc is None:
            return
        index = max(0, min(index, len(self.doc) - 1))
        self.page_index = index
        page = self.doc[index]
        self.page_w, self.page_h = page.get_size()
        self.page_label.config(text=f"{index + 1} / {len(self.doc)}")
        self._render_page()
        if recenter and self._overlay is not None:
            self.center_image()
        self.redraw()

    def _render_page(self):
        """Rasterise the current page to fit the canvas."""
        if self.doc is None:
            return
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        margin = 20
        self.ppp = min((cw - margin) / self.page_w, (ch - margin) / self.page_h)
        bitmap = self.doc[self.page_index].render(scale=self.ppp)
        self._page_img = bitmap.to_pil().convert("RGB")
        self.off_x = (cw - self._page_img.width) // 2
        self.off_y = (ch - self._page_img.height) // 2

    # ------------------------------------------------------------- geometry

    def _draw_size_pt(self):
        """Width/height of the placed image, in PDF points."""
        w = self.page_w * self.scale
        h = w * (self._overlay.height / self._overlay.width)
        return w, h

    def _image_box_px(self):
        """Placed image as a canvas-pixel box (left, top, right, bottom)."""
        w_pt, h_pt = self._draw_size_pt()
        left = self.off_x + self.x_pt * self.ppp
        top = self.off_y + (self.page_h - self.y_pt - h_pt) * self.ppp
        return left, top, left + w_pt * self.ppp, top + h_pt * self.ppp

    def _handle_box_px(self):
        """Resize handle, centred on the bottom-right corner.

        Straddling the corner rather than sitting inside it keeps the interior
        grabbable for very thin images (a signature strip can be a few px tall).
        """
        _, _, right, bottom = self._image_box_px()
        h = HANDLE / 2
        return right - h, bottom - h, right + h, bottom + h

    def center_image(self):
        if self.doc is None or self._overlay is None:
            return
        w_pt, h_pt = self._draw_size_pt()
        self.x_pt = (self.page_w - w_pt) / 2
        self.y_pt = (self.page_h - h_pt) / 2
        self.redraw()

    def _clamp(self):
        """Keep at least a sliver of the image on the page."""
        w_pt, h_pt = self._draw_size_pt()
        self.x_pt = max(-w_pt * 0.9, min(self.x_pt, self.page_w - w_pt * 0.1))
        self.y_pt = max(-h_pt * 0.9, min(self.y_pt, self.page_h - h_pt * 0.1))

    def _nudge(self, dx, dy):
        if self.doc is None or self._overlay is None:
            return
        self.x_pt += dx
        self.y_pt += dy
        self._clamp()
        self.redraw()

    def _set_scale(self, value, anchor_center=False):
        value = max(MIN_SCALE, min(value, MAX_SCALE))
        if anchor_center:
            ow, oh = self._draw_size_pt()
            cx, cy = self.x_pt + ow / 2, self.y_pt + oh / 2
            self.scale = value
            nw, nh = self._draw_size_pt()
            self.x_pt, self.y_pt = cx - nw / 2, cy - nh / 2
        else:
            self.scale = value
        self.scale_var.set(self.scale * 100)
        self.scale_label.config(text=f"{self.scale:.0%}")
        self._clamp()
        self.redraw()

    # ------------------------------------------------------------------ draw

    def redraw(self):
        self.canvas.delete("all")
        if self._page_img is None:
            return

        frame = self._page_img.copy()
        if self._overlay is not None:
            w_pt, h_pt = self._draw_size_pt()
            w_px = max(1, round(w_pt * self.ppp))
            h_px = max(1, round(h_pt * self.ppp))
            scaled = self._overlay.resize((w_px, h_px), PILImage.LANCZOS)
            left, top, _, _ = self._image_box_px()
            frame.paste(scaled, (round(left - self.off_x), round(top - self.off_y)), scaled)

        self._photo = ImageTk.PhotoImage(frame)
        self.canvas.create_image(self.off_x, self.off_y, image=self._photo, anchor=tk.NW)

        if self._overlay is not None:
            left, top, right, bottom = self._image_box_px()
            self.canvas.create_rectangle(left, top, right, bottom, outline="#00a2ff", dash=(4, 3))
            self.canvas.create_rectangle(
                *self._handle_box_px(), fill="#00a2ff", outline="white", tags="handle",
            )
            w_pt, h_pt = self._draw_size_pt()
            self.status.config(
                text=f"Page {self.page_index + 1} · {self.page_w:.0f}×{self.page_h:.0f} pt · "
                     f"image at ({self.x_pt:.1f}, {self.y_pt:.1f}) pt, "
                     f"{w_pt:.1f}×{h_pt:.1f} pt, scale {self.scale:.1%}"
            )
        else:
            self.status.config(text="Open an image to place it on the page.")

    def _on_canvas_resize(self, _event):
        # Re-rasterise once the drag settles rather than on every pixel.
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._do_resize)

    def _do_resize(self):
        self._resize_job = None
        if self.doc is not None:
            self._render_page()
            self.redraw()

    # -------------------------------------------------------------- pointer

    def _hit(self, x, y):
        if self._overlay is None or self._page_img is None:
            return None
        hl, ht, hr, hb = self._handle_box_px()
        if hl <= x <= hr and ht <= y <= hb:
            return "resize"
        left, top, right, bottom = self._image_box_px()
        # Thin images can be only a few px tall on screen; give them a grab margin.
        pad = max(0, (HANDLE - (bottom - top)) / 2)
        if left <= x <= right and top - pad <= y <= bottom + pad:
            return "move"
        return None

    def _on_hover(self, event):
        hit = self._hit(event.x, event.y)
        self.canvas.config(cursor={"resize": "sizing", "move": "fleur"}.get(hit, ""))

    def _on_press(self, event):
        hit = self._hit(event.x, event.y)
        if hit is None:
            self._drag = None
            return
        self.canvas.focus_set()
        self._drag = (hit, event.x, event.y, self.x_pt, self.y_pt, self.scale)

    def _on_motion(self, event):
        if self._drag is None:
            return
        mode, x0, y0, px, py, pscale = self._drag
        if mode == "move":
            self.x_pt = px + (event.x - x0) / self.ppp
            self.y_pt = py - (event.y - y0) / self.ppp  # canvas y grows downward
            self._clamp()
            self.redraw()
        else:
            # Resize from the bottom-right handle: the top-left corner stays put,
            # so the anchor is (x_pt, y_pt + h_pt) and y_pt follows the new height.
            w_pt, h_pt = self._draw_size_pt()
            top_pt = self.y_pt + h_pt
            new_w_pt = (event.x - self.off_x) / self.ppp - self.x_pt
            new_scale = max(MIN_SCALE, min(new_w_pt / self.page_w, MAX_SCALE))
            self.scale = new_scale
            _, new_h_pt = self._draw_size_pt()
            self.y_pt = top_pt - new_h_pt
            self.scale_var.set(self.scale * 100)
            self.scale_label.config(text=f"{self.scale:.0%}")
            self._clamp()
            self.redraw()

    def _on_wheel(self, event):
        if self._overlay is None or self.doc is None:
            return
        self._set_scale(self.scale * (1.1 if event.delta > 0 else 1 / 1.1), anchor_center=True)

    def _on_slider(self, value):
        if self._overlay is None or self.doc is None:
            return
        self._set_scale(float(value) / 100, anchor_center=True)

    # ------------------------------------------------------------------ save

    def save(self):
        if self.doc is None or self._overlay is None:
            messagebox.showwarning("Nothing to save", "Open both a PDF and an image first.")
            return

        default = f"{Path(self.pdf_path).stem}_with_image.pdf"
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default,
            initialdir=str(Path(self.pdf_path).parent), filetypes=[("PDF", "*.pdf")],
        )
        if not out:
            return
        if Path(out) == Path(self.pdf_path):
            messagebox.showerror("Cannot overwrite", "Choose a different file from the source PDF.")
            return

        try:
            add_image_to_pdf(
                self.pdf_path, self.image_path, out,
                self.page_index + 1, self.x_pt, self.y_pt, self.scale,
            )
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.status.config(text=f"Saved to {out}")


def main():
    args = sys.argv[1:]
    pdf = next((a for a in args if a.lower().endswith(".pdf")), None)
    img = next((a for a in args if not a.lower().endswith(".pdf")), None)
    PdfImagePlacer(pdf, img).mainloop()


if __name__ == "__main__":
    main()
