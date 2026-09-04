#!/usr/bin/env python3
"""
GUI for add_image_to_pdf.py — place one or more images on a PDF, page by page.

Usage:
    python add_image_to_pdf_gui.py [input.pdf] [image ...]

Any argument ending in .pdf is the PDF; every other argument is an image added
to page 1.

Controls:
    Add Images…          add one or more images to the page in view
    Layers sidebar       every image on the current page, front-most at the top
    ▲ ▼ ⤒ ⤓              move the selected layer up / down / to front / to back
    ✕ Delete / Del key   remove the selected layer
    Drag image           move the selected layer (click an image to select it)
    Drag corner handle   scale
    Mouse wheel          scale about image centre
    Arrow keys           nudge 1 pt (hold Shift for 10 pt) — canvas must be focused
    Page Up / Page Down  change page

    Form Fields sidebar  every AcroForm field in the document, grouped by name
    Show fields          toggle outline boxes for the current page's fields
    ✕ Delete / Del key   mark the selected field(s) for deletion on Save
    ↺ Restore            unmark the selected field(s)
"""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageTk

from add_image_to_pdf import Placement, add_images_to_pdf, list_form_fields

HANDLE = 10  # px, size of the corner resize handle
MIN_SCALE = 0.01
MAX_SCALE = 3.0
SIDEBAR_W = 230  # px
THUMB = 40  # px, layer-list thumbnail
CASCADE = 18.0  # pt, offset for each further image added to a page
FIELD_COLOR = "#ffa500"  # orange, kept field outline
FIELD_DELETE_COLOR = "#e04040"  # red, field marked for deletion


class Layer:
    """One image placed on one page, in the same units the CLI takes."""

    __slots__ = ("path", "page_index", "tree_id", "scale", "x_pt", "y_pt", "pil", "_scaled")

    def __init__(self, path, page_index, tree_id, scale=0.25):
        self.path = path
        self.page_index = page_index
        self.tree_id = tree_id
        self.scale = scale
        self.x_pt = 0.0
        self.y_pt = 0.0
        self.pil = None  # source image, PIL RGBA
        self._scaled = None  # (w_px, h_px, PIL) cache for the preview


class PdfImagePlacer(tk.Tk):
    def __init__(self, pdf_path=None, image_paths=None):
        super().__init__()
        self.title("Add Image to PDF")
        self.geometry("1180x800")

        self.pdf_path = None
        self.doc = None
        self.page_index = 0

        self.layers = []  # list[Layer], per-page order is z-order (first = back)
        self.selected = None  # Layer or None
        self._id_seq = 0
        self._syncing = False  # guard against sidebar/canvas selection feedback

        self.form_fields = []  # list of dicts from list_form_fields()
        self.fields_to_delete = set()  # field names marked for deletion on save
        self.show_fields_var = tk.BooleanVar(value=True)

        self._pil_cache = {}  # path -> PIL RGBA (or None if it failed to load)
        self._thumb_cache = {}  # path -> ImageTk.PhotoImage

        self.page_w = self.page_h = 0.0
        self.ppp = 1.0  # preview pixels per PDF point
        self.off_x = self.off_y = 0  # page origin within the canvas
        self._page_img = None  # rendered page, PIL
        self._photo = None  # ImageTk ref, must outlive the draw call
        self._drag = None
        self._resize_job = None

        self._build_widgets()
        self._update_controls_state()
        self._update_page_label()

        if pdf_path:
            self.load_pdf(pdf_path)
        if image_paths:
            self.add_images(list(image_paths))

    # ---------------------------------------------------------------- widgets

    def _build_widgets(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="Open PDF…", command=self.choose_pdf).pack(side=tk.LEFT)
        ttk.Button(bar, text="Add Images…", command=self.choose_images).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(bar, text="◀", width=3, command=lambda: self.goto_page(self.page_index - 1)).pack(side=tk.LEFT)
        self.page_label = ttk.Label(bar, text="— / —", width=14, anchor=tk.CENTER)
        self.page_label.pack(side=tk.LEFT)
        ttk.Button(bar, text="▶", width=3, command=lambda: self.goto_page(self.page_index + 1)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(bar, text="Scale").pack(side=tk.LEFT)
        self.scale_var = tk.DoubleVar(value=25.0)
        self.scale_slider = ttk.Scale(
            bar, from_=MIN_SCALE * 100, to=MAX_SCALE * 100,
            variable=self.scale_var, command=self._on_slider, length=180,
        )
        self.scale_slider.pack(side=tk.LEFT, padx=4)
        self.scale_label = ttk.Label(bar, text="25%", width=6)
        self.scale_label.pack(side=tk.LEFT)

        self.center_btn = ttk.Button(bar, text="Center", command=self.center_selected)
        self.center_btn.pack(side=tk.LEFT, padx=12)

        ttk.Checkbutton(
            bar, text="Show fields", variable=self.show_fields_var, command=self.redraw,
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(bar, text="Save As…", command=self.save).pack(side=tk.RIGHT)

        self.status = ttk.Label(self, text="Open a PDF and add images to begin.", anchor=tk.W, padding=(8, 3))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        body = ttk.Frame(self)

        sidebar = ttk.Frame(body, padding=(6, 6), width=SIDEBAR_W)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar.pack_propagate(False)

        panes = ttk.Panedwindow(sidebar, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)

        layers_pane = ttk.Frame(panes)
        fields_pane = ttk.Frame(panes)
        panes.add(layers_pane, weight=2)
        panes.add(fields_pane, weight=1)

        # -- Layers ---------------------------------------------------------

        ttk.Label(layers_pane, text="Layers   (top = front)").pack(anchor=tk.W)

        tree_wrap = ttk.Frame(layers_pane)
        tree_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        ttk.Style(self).configure("Layers.Treeview", rowheight=THUMB + 8)
        self.tree = ttk.Treeview(
            tree_wrap, show="tree", selectmode="browse", style="Layers.Treeview",
        )
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        btns = ttk.Frame(layers_pane)
        btns.pack(fill=tk.X)
        self.raise_btn = ttk.Button(btns, text="▲", width=3, command=lambda: self._reorder("raise"))
        self.lower_btn = ttk.Button(btns, text="▼", width=3, command=lambda: self._reorder("lower"))
        self.front_btn = ttk.Button(btns, text="⤒", width=3, command=lambda: self._reorder("front"))
        self.back_btn = ttk.Button(btns, text="⤓", width=3, command=lambda: self._reorder("back"))
        for b in (self.raise_btn, self.lower_btn, self.front_btn, self.back_btn):
            b.pack(side=tk.LEFT)
        self.del_btn = ttk.Button(btns, text="✕ Delete", command=self.delete_selected)
        self.del_btn.pack(side=tk.RIGHT)

        self._layer_controls = [
            self.scale_slider, self.center_btn,
            self.raise_btn, self.lower_btn, self.front_btn, self.back_btn, self.del_btn,
        ]

        self.tree.bind("<Delete>", self.delete_selected)
        self.tree.bind("<BackSpace>", self.delete_selected)

        # -- Form Fields ------------------------------------------------------

        ttk.Label(fields_pane, text="Form Fields").pack(anchor=tk.W)

        fields_wrap = ttk.Frame(fields_pane)
        fields_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.fields_tree = ttk.Treeview(
            fields_wrap, show="tree", selectmode="extended",
        )
        self.fields_tree.tag_configure("marked", foreground=FIELD_DELETE_COLOR)
        fvsb = ttk.Scrollbar(fields_wrap, orient="vertical", command=self.fields_tree.yview)
        self.fields_tree.configure(yscrollcommand=fvsb.set)
        self.fields_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fvsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.fields_tree.bind("<<TreeviewSelect>>", lambda e: self._update_field_controls_state())
        self.fields_tree.bind("<Delete>", self._mark_selected_fields_deleted)
        self.fields_tree.bind("<BackSpace>", self._mark_selected_fields_deleted)

        field_btns = ttk.Frame(fields_pane)
        field_btns.pack(fill=tk.X)
        self.field_del_btn = ttk.Button(field_btns, text="✕ Delete", command=self._mark_selected_fields_deleted)
        self.field_del_btn.pack(side=tk.LEFT)
        self.field_restore_btn = ttk.Button(field_btns, text="↺ Restore", command=self._restore_selected_fields)
        self.field_restore_btn.pack(side=tk.LEFT, padx=(4, 0))

        self._field_controls = [self.field_del_btn, self.field_restore_btn]
        self._update_field_controls_state()

        self.canvas = tk.Canvas(body, bg="#555", highlightthickness=0, takefocus=True)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_hover)

        # Page navigation is global; nudging is scoped to the canvas so the
        # layer list keeps its own Up/Down behaviour when it has focus.
        self.bind("<Prior>", lambda e: self.goto_page(self.page_index - 1))
        self.bind("<Next>", lambda e: self.goto_page(self.page_index + 1))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, 1), ("Down", 0, -1)):
            self.canvas.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self._nudge(dx, dy))
            self.canvas.bind(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))
        # Scoped to the canvas (not the whole window) so the Form Fields list
        # keeps its own Delete/BackSpace behaviour when it has focus.
        self.canvas.bind("<Delete>", self.delete_selected)
        self.canvas.bind("<BackSpace>", self.delete_selected)

    # ------------------------------------------------------------------ files

    def choose_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if path:
            self.load_pdf(path)

    def choose_images(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff"), ("All files", "*.*")]
        )
        if paths:
            self.add_images(list(paths))

    def load_pdf(self, path):
        if self.layers and not messagebox.askokcancel(
            "Discard placed images?",
            f"Discard {len(self.layers)} placed image(s) and open a new PDF?",
        ):
            return
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
        self.layers = []
        self.selected = None
        self.page_index = 0
        try:
            self.form_fields = list_form_fields(path)
        except Exception:
            self.form_fields = []  # non-fatal: proceed without field listing/deletion
        self.fields_to_delete = set()
        self._refresh_fields_tree()
        self.title(f"Add Image to PDF — {Path(path).name}")
        self.goto_page(0)

    def _load_pil(self, path):
        if path not in self._pil_cache:
            try:
                self._pil_cache[path] = PILImage.open(path).convert("RGBA")
            except Exception as exc:
                messagebox.showerror("Cannot open image", f"{path}\n\n{exc}")
                self._pil_cache[path] = None
        return self._pil_cache[path]

    def _thumb(self, path):
        if path not in self._thumb_cache:
            im = self._load_pil(path).copy()
            im.thumbnail((THUMB, THUMB), PILImage.LANCZOS)
            self._thumb_cache[path] = ImageTk.PhotoImage(im)
        return self._thumb_cache[path]

    def add_images(self, paths):
        if self.doc is None:
            messagebox.showwarning("Open a PDF first", "Open a PDF before adding images.")
            return
        added = None
        for path in paths:
            pil = self._load_pil(path)
            if pil is None:
                continue
            self._id_seq += 1
            lyr = Layer(path, self.page_index, f"L{self._id_seq}")
            lyr.pil = pil
            self.layers.append(lyr)
            self._place_new(lyr)
            added = lyr
        if added is not None:
            self._refresh_sidebar()
            self.select(added)

    # ------------------------------------------------------------------ pages

    def goto_page(self, index):
        if self.doc is None:
            return
        index = max(0, min(index, len(self.doc) - 1))
        self.page_index = index
        page = self.doc[index]
        self.page_w, self.page_h = page.get_size()
        self._render_page()
        self._refresh_sidebar()
        same = self._page_layers()
        self.select(same[-1] if same else None)

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

    def _page_layers(self, page_index=None):
        pi = self.page_index if page_index is None else page_index
        return [l for l in self.layers if l.page_index == pi]

    def _layer_size_pt(self, lyr):
        """Width/height of the placed image, in PDF points."""
        w = self.page_w * lyr.scale
        h = w * (lyr.pil.height / lyr.pil.width)
        return w, h

    def _box_px(self, lyr):
        """Placed image as a canvas-pixel box (left, top, right, bottom)."""
        w_pt, h_pt = self._layer_size_pt(lyr)
        left = self.off_x + lyr.x_pt * self.ppp
        top = self.off_y + (self.page_h - lyr.y_pt - h_pt) * self.ppp
        return left, top, left + w_pt * self.ppp, top + h_pt * self.ppp

    def _handle_px(self, lyr):
        """Resize handle, centred on the bottom-right corner.

        Straddling the corner rather than sitting inside it keeps the interior
        grabbable for very thin images (a signature strip can be a few px tall).
        """
        _, _, right, bottom = self._box_px(lyr)
        h = HANDLE / 2
        return right - h, bottom - h, right + h, bottom + h

    def _place_new(self, lyr):
        """Position a freshly added layer: centred on an empty page, else
        cascaded from the current front-most image on that page."""
        others = [l for l in self._page_layers(lyr.page_index) if l is not lyr]
        w_pt, h_pt = self._layer_size_pt(lyr)
        if not others:
            lyr.x_pt = (self.page_w - w_pt) / 2
            lyr.y_pt = (self.page_h - h_pt) / 2
        else:
            anchor = others[-1]
            lyr.x_pt = anchor.x_pt + CASCADE
            lyr.y_pt = anchor.y_pt - CASCADE
        self._clamp(lyr)

    def center_selected(self):
        lyr = self.selected
        if lyr is None or self.doc is None:
            return
        w_pt, h_pt = self._layer_size_pt(lyr)
        lyr.x_pt = (self.page_w - w_pt) / 2
        lyr.y_pt = (self.page_h - h_pt) / 2
        self.redraw()

    def _clamp(self, lyr):
        """Keep at least a sliver of the image on the page."""
        w_pt, h_pt = self._layer_size_pt(lyr)
        lyr.x_pt = max(-w_pt * 0.9, min(lyr.x_pt, self.page_w - w_pt * 0.1))
        lyr.y_pt = max(-h_pt * 0.9, min(lyr.y_pt, self.page_h - h_pt * 0.1))

    def _nudge(self, dx, dy):
        lyr = self.selected
        if lyr is None:
            return
        lyr.x_pt += dx
        lyr.y_pt += dy
        self._clamp(lyr)
        self.redraw()

    def _set_scale(self, value, anchor_center=False):
        lyr = self.selected
        if lyr is None:
            return
        value = max(MIN_SCALE, min(value, MAX_SCALE))
        if anchor_center:
            ow, oh = self._layer_size_pt(lyr)
            cx, cy = lyr.x_pt + ow / 2, lyr.y_pt + oh / 2
            lyr.scale = value
            nw, nh = self._layer_size_pt(lyr)
            lyr.x_pt, lyr.y_pt = cx - nw / 2, cy - nh / 2
        else:
            lyr.scale = value
        self.scale_var.set(lyr.scale * 100)
        self.scale_label.config(text=f"{lyr.scale:.0%}")
        self._clamp(lyr)
        self.redraw()

    # ------------------------------------------------------------------ sidebar

    def _layer_by_tree_id(self, tid):
        return next((l for l in self.layers if l.tree_id == tid), None)

    def _refresh_sidebar(self):
        self._syncing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for lyr in reversed(self._page_layers()):  # front-most on top
                self.tree.insert(
                    "", "end", iid=lyr.tree_id,
                    text="  " + Path(lyr.path).name, image=self._thumb(lyr.path),
                )
            if self.selected is not None and self.tree.exists(self.selected.tree_id):
                self.tree.selection_set(self.selected.tree_id)
        finally:
            self._syncing = False
        self._update_page_label()
        self._update_controls_state()

    def _on_tree_select(self, _event):
        if self._syncing:
            return
        sel = self.tree.selection()
        if not sel:
            return
        lyr = self._layer_by_tree_id(sel[0])
        if lyr is None or lyr is self.selected:
            return
        self.selected = lyr
        self._update_controls_state()
        self.scale_var.set(lyr.scale * 100)
        self.scale_label.config(text=f"{lyr.scale:.0%}")
        self.redraw()

    def select(self, layer):
        """Make ``layer`` (or nothing) the active layer, syncing the sidebar."""
        self.selected = layer
        self._syncing = True
        try:
            if layer is not None and self.tree.exists(layer.tree_id):
                self.tree.selection_set(layer.tree_id)
                self.tree.see(layer.tree_id)
            else:
                for i in self.tree.selection():
                    self.tree.selection_remove(i)
        finally:
            self._syncing = False
        self._update_controls_state()
        s = layer.scale if layer is not None else 0.25
        self.scale_var.set(s * 100)
        self.scale_label.config(text=f"{s:.0%}")
        self.redraw()

    def _reorder(self, mode):
        lyr = self.selected
        if lyr is None:
            return
        same = self._page_layers(lyr.page_index)
        pos = same.index(lyr)
        if mode == "raise" and pos < len(same) - 1:
            same[pos], same[pos + 1] = same[pos + 1], same[pos]
        elif mode == "lower" and pos > 0:
            same[pos], same[pos - 1] = same[pos - 1], same[pos]
        elif mode == "front":
            same.append(same.pop(pos))
        elif mode == "back":
            same.insert(0, same.pop(pos))
        else:
            return
        it = iter(same)
        self.layers = [next(it) if l.page_index == lyr.page_index else l for l in self.layers]
        self._refresh_sidebar()
        self.redraw()

    def delete_selected(self, _event=None):
        lyr = self.selected
        if lyr is None:
            return
        same = self._page_layers(lyr.page_index)
        pos = same.index(lyr)
        self.layers.remove(lyr)
        lyr._scaled = None
        remaining = self._page_layers(lyr.page_index)
        self._refresh_sidebar()
        self.select(remaining[min(pos, len(remaining) - 1)] if remaining else None)

    def _update_controls_state(self):
        state = ("!disabled",) if self.selected is not None else ("disabled",)
        for w in self._layer_controls:
            w.state(state)

    def _update_page_label(self):
        if self.doc is None:
            self.page_label.config(text="— / —")
            return
        n = len(self._page_layers())
        txt = f"{self.page_index + 1} / {len(self.doc)}"
        if n:
            txt += f"  ·  {n} img"
        self.page_label.config(text=txt)

    # ------------------------------------------------------------- form fields

    def _refresh_fields_tree(self):
        self.fields_tree.delete(*self.fields_tree.get_children())
        for field in self.form_fields:
            pages = ",".join(str(p) for p in field["pages"])
            marked = field["name"] in self.fields_to_delete
            self.fields_tree.insert(
                "", "end", iid=field["name"],
                text=f"  {field['name']}   (p. {pages})",
                tags=("marked",) if marked else (),
            )
        self._update_field_controls_state()

    def _update_field_controls_state(self):
        state = ("!disabled",) if self.fields_tree.selection() else ("disabled",)
        for w in self._field_controls:
            w.state(state)

    def _mark_selected_fields_deleted(self, _event=None):
        sel = self.fields_tree.selection()
        if not sel:
            return
        self.fields_to_delete.update(sel)
        self._refresh_fields_tree()
        self.redraw()

    def _restore_selected_fields(self, _event=None):
        sel = self.fields_tree.selection()
        if not sel:
            return
        self.fields_to_delete.difference_update(sel)
        self._refresh_fields_tree()
        self.redraw()

    def _fields_on_page(self, page_index):
        """(field, rect) pairs for every widget on ``page_index`` (0-based)."""
        page_num = page_index + 1
        return [
            (field, rect)
            for field in self.form_fields
            for rect in field["rects"].get(page_num, [])
        ]

    def _field_rect_px(self, rect):
        llx, lly, urx, ury = rect
        left = self.off_x + llx * self.ppp
        right = self.off_x + urx * self.ppp
        top = self.off_y + (self.page_h - ury) * self.ppp
        bottom = self.off_y + (self.page_h - lly) * self.ppp
        return left, top, right, bottom

    # ------------------------------------------------------------------ draw

    def _scaled_preview(self, lyr, w_px, h_px):
        c = lyr._scaled
        if c is None or c[0] != w_px or c[1] != h_px:
            img = lyr.pil.resize((max(1, w_px), max(1, h_px)), PILImage.LANCZOS)
            lyr._scaled = (w_px, h_px, img)
        return lyr._scaled[2]

    def _compose_frame(self):
        """The current page rasterised with every layer painted in z-order."""
        frame = self._page_img.copy()
        for lyr in self._page_layers():
            w_pt, h_pt = self._layer_size_pt(lyr)
            w_px = max(1, round(w_pt * self.ppp))
            h_px = max(1, round(h_pt * self.ppp))
            scaled = self._scaled_preview(lyr, w_px, h_px)
            left, top, _, _ = self._box_px(lyr)
            frame.paste(scaled, (round(left - self.off_x), round(top - self.off_y)), scaled)
        return frame

    def redraw(self):
        self.canvas.delete("all")
        if self._page_img is None:
            return

        self._photo = ImageTk.PhotoImage(self._compose_frame())
        self.canvas.create_image(self.off_x, self.off_y, image=self._photo, anchor=tk.NW)

        if self.show_fields_var.get():
            for field, rect in self._fields_on_page(self.page_index):
                left, top, right, bottom = self._field_rect_px(rect)
                color = FIELD_DELETE_COLOR if field["name"] in self.fields_to_delete else FIELD_COLOR
                self.canvas.create_rectangle(left, top, right, bottom, outline=color, dash=(3, 2))
                self.canvas.create_text(
                    left + 2, top - 1, text=field["name"], anchor=tk.SW,
                    fill=color, font=("TkDefaultFont", 7),
                )

        sel = self.selected
        if sel is not None and sel.page_index == self.page_index:
            left, top, right, bottom = self._box_px(sel)
            self.canvas.create_rectangle(left, top, right, bottom, outline="#00a2ff", dash=(4, 3))
            self.canvas.create_rectangle(
                *self._handle_px(sel), fill="#00a2ff", outline="white", tags="handle",
            )

        self._update_status()

    def _update_status(self):
        if self.doc is None:
            self.status.config(text="Open a PDF and add images to begin.")
            return
        n = len(self._page_layers())
        sel = self.selected
        marked = f" · {len(self.fields_to_delete)} field(s) marked for deletion" if self.fields_to_delete else ""
        if sel is not None and sel.page_index == self.page_index:
            w_pt, h_pt = self._layer_size_pt(sel)
            self.status.config(
                text=f"Page {self.page_index + 1} · {self.page_w:.0f}×{self.page_h:.0f} pt · "
                     f"{n} layer(s) · {Path(sel.path).name} at "
                     f"({sel.x_pt:.1f}, {sel.y_pt:.1f}) pt, {w_pt:.1f}×{h_pt:.1f} pt, "
                     f"scale {sel.scale:.1%}{marked}"
            )
        else:
            self.status.config(
                text=f"Page {self.page_index + 1} · {n} layer(s) on this page · "
                     f"add images or pick a layer to edit{marked}"
            )

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
        """(layer, mode) under the cursor. The selected layer's handle and body
        win even when another layer covers them; otherwise the top-most layer."""
        if self._page_img is None:
            return None, None

        sel = self.selected
        if sel is not None and sel.page_index == self.page_index:
            hl, ht, hr, hb = self._handle_px(sel)
            if hl <= x <= hr and ht <= y <= hb:
                return sel, "resize"
            left, top, right, bottom = self._box_px(sel)
            pad = max(0, (HANDLE - (bottom - top)) / 2)
            if left <= x <= right and top - pad <= y <= bottom + pad:
                return sel, "move"

        for lyr in reversed(self._page_layers()):
            left, top, right, bottom = self._box_px(lyr)
            if left <= x <= right and top <= y <= bottom:
                return lyr, "move"
        return None, None

    def _on_hover(self, event):
        _, mode = self._hit(event.x, event.y)
        self.canvas.config(cursor={"resize": "sizing", "move": "fleur"}.get(mode, ""))

    def _on_press(self, event):
        self.canvas.focus_set()
        lyr, mode = self._hit(event.x, event.y)
        if lyr is None:
            self._drag = None
            return
        if lyr is not self.selected:
            self.select(lyr)
            mode = "move"
        self._drag = (mode, event.x, event.y, lyr.x_pt, lyr.y_pt)

    def _on_motion(self, event):
        if self._drag is None:
            return
        lyr = self.selected
        if lyr is None:
            return
        mode, x0, y0, px, py = self._drag
        if mode == "move":
            lyr.x_pt = px + (event.x - x0) / self.ppp
            lyr.y_pt = py - (event.y - y0) / self.ppp  # canvas y grows downward
            self._clamp(lyr)
            self.redraw()
        else:
            # Resize from the bottom-right handle: the top-left corner stays put,
            # so the anchor is (x_pt, y_pt + h_pt) and y_pt follows the new height.
            _, h_pt = self._layer_size_pt(lyr)
            top_pt = lyr.y_pt + h_pt
            new_w_pt = (event.x - self.off_x) / self.ppp - lyr.x_pt
            lyr.scale = max(MIN_SCALE, min(new_w_pt / self.page_w, MAX_SCALE))
            _, new_h_pt = self._layer_size_pt(lyr)
            lyr.y_pt = top_pt - new_h_pt
            self.scale_var.set(lyr.scale * 100)
            self.scale_label.config(text=f"{lyr.scale:.0%}")
            self._clamp(lyr)
            self.redraw()

    def _on_wheel(self, event):
        if self.selected is None:
            return
        self._set_scale(self.selected.scale * (1.1 if event.delta > 0 else 1 / 1.1), anchor_center=True)

    def _on_slider(self, value):
        if self.selected is None or self.doc is None:
            return
        self._set_scale(float(value) / 100, anchor_center=True)

    # ------------------------------------------------------------------ save

    def save(self):
        if self.doc is None:
            messagebox.showwarning("Nothing to save", "Open a PDF first.")
            return
        if not self.layers and not self.fields_to_delete:
            messagebox.showwarning("Nothing to save", "No images placed and no fields marked for deletion.")
            return

        default = f"{Path(self.pdf_path).stem}_with_images.pdf"
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default,
            initialdir=str(Path(self.pdf_path).parent), filetypes=[("PDF", "*.pdf")],
        )
        if not out:
            return
        if Path(out) == Path(self.pdf_path):
            messagebox.showerror("Cannot overwrite", "Choose a different file from the source PDF.")
            return

        placements = [
            Placement(image_path=l.path, page=l.page_index + 1, x=l.x_pt, y=l.y_pt, scale=l.scale)
            for l in self.layers
        ]
        try:
            pages = add_images_to_pdf(self.pdf_path, placements, out, remove_fields=self.fields_to_delete)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        summary = f"Saved {len(placements)} image(s) across {len(pages)} page(s)"
        if self.fields_to_delete:
            summary += f", removed {len(self.fields_to_delete)} field(s)"
        self.status.config(text=f"{summary} to {out}")


def main():
    args = sys.argv[1:]
    pdf = next((a for a in args if a.lower().endswith(".pdf")), None)
    images = [a for a in args if not a.lower().endswith(".pdf")]
    PdfImagePlacer(pdf, images).mainloop()


if __name__ == "__main__":
    main()
