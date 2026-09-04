#!/usr/bin/env python3
"""
Add one or more image overlays to an existing PDF.

Usage:
    python add_image_to_pdf.py input.pdf image.png [-o output.pdf] [-p PAGE] [-x X] [-y Y] [-s SCALE]

Arguments:
    input.pdf       Path to the existing PDF
    image.png       Path to the image file (PNG, JPG, etc.)

Options:
    -o, --output    Output PDF path (default: input_with_image.pdf)
    -p, --page      Page number to place image on, 1-based (default: 1)
    -x              X position in points from left edge (default: centered)
    -y              Y position in points from bottom edge (default: centered)
    -s, --scale     Scale factor relative to page width (default: 0.25 = 1/4 page width)

Examples:
    # Default: centered on page 1 at 1/4 page width
    python add_image_to_pdf.py document.pdf logo.png

    # Top-right corner of page 3, scaled to 1/3 page width
    python add_image_to_pdf.py document.pdf logo.png -p 3 -x 400 -y 700 -s 0.33

    # Specific position and custom output name
    python add_image_to_pdf.py doc.pdf stamp.png -o stamped.pdf -x 100 -y 200 -s 0.5

For placing several images at once — including several stacked on one page — use
add_images_to_pdf() from Python, or the point-and-click GUI (add_image_to_pdf_gui.py).
"""

import argparse
import os
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


@dataclass
class Placement:
    """One image placed on one page.

    Positions are in PDF points from the bottom-left corner; ``scale`` is the
    image width as a fraction of the page width. ``x`` / ``y`` may be ``None``
    to centre the image on that axis. When several placements share a page they
    are painted in list order, so earlier entries sit behind later ones.
    """

    image_path: str
    page: int = 1  # 1-based
    x: Optional[float] = None
    y: Optional[float] = None
    scale: float = 0.25


def _draw_image(c, image_path, page_width, page_height, x, y, scale):
    """Paint a single image onto an open reportlab canvas."""
    # Get image dimensions to preserve aspect ratio
    img = PILImage.open(image_path)
    img_w, img_h = img.size

    # Target width based on scale factor (fraction of page width)
    draw_w = page_width * scale
    draw_h = draw_w * (img_h / img_w)  # preserve aspect ratio

    # If no position given, center the image
    if x is None:
        x = (page_width - draw_w) / 2
    if y is None:
        y = (page_height - draw_h) / 2

    c.drawImage(
        ImageReader(image_path),
        x, y, draw_w, draw_h,
        preserveAspectRatio=True,
        mask="auto",  # honour transparency for PNGs
    )


def _resolve(value):
    """dict.get() on a pypdf DictionaryObject does not follow indirect
    references (unlike bracket access) — an /Annots or /Fields array is
    often stored that way, and e.g. ``len()`` on the unresolved
    ``IndirectObject`` raises. Use this after every such ``.get(...)``.
    """
    return value.get_object() if value is not None else None


def _walk_up(field_or_widget):
    """Yield ``field_or_widget`` and each ancestor via /Parent, closest first.

    A field with kids (radio buttons, or a field repeated across pages) keeps
    its /T and /FT on the top-level field; the per-page widgets are the kids
    and often carry neither, inheriting them from the parent instead.
    """
    obj = field_or_widget
    while obj is not None:
        yield obj
        obj = _resolve(obj.get("/Parent"))


def _qualified_field_name(field_or_widget) -> str:
    """Full dotted field name, e.g. ``"address.city"`` for a nested field."""
    parts = [str(o["/T"]) for o in _walk_up(field_or_widget) if o.get("/T")]
    return ".".join(reversed(parts))


def _field_type(field_or_widget):
    for o in _walk_up(field_or_widget):
        ft = _resolve(o.get("/FT"))
        if ft is not None:
            return str(ft)
    return None


def list_form_fields(input_pdf):
    """Every AcroForm field with a widget annotation on some page.

    Returns a list of dicts, one per top-level field name, sorted by first
    page then name::

        {"name": "signature", "field_type": "/Sig", "pages": [1],
         "rects": {1: [(llx, lly, urx, ury), ...]}}

    ``field_type`` is the raw ``/FT`` value (``/Tx``, ``/Btn``, ``/Ch``,
    ``/Sig``) or ``None`` if it can't be determined. ``pages`` are 1-based.
    A field can have more than one widget (radio buttons, a field repeated
    across pages), so ``rects`` may hold several boxes per page.
    """
    reader = PdfReader(input_pdf)
    by_name: dict[str, dict] = {}
    for page_index, page in enumerate(reader.pages):
        for ref in _resolve(page.get("/Annots")) or []:
            annot = ref.get_object()
            if _resolve(annot.get("/Subtype")) != "/Widget":
                continue
            name = _qualified_field_name(annot)
            if not name:
                continue
            entry = by_name.setdefault(name, {"field_type": None, "pages": set(), "rects": {}})
            page_num = page_index + 1
            entry["pages"].add(page_num)
            if entry["field_type"] is None:
                entry["field_type"] = _field_type(annot)
            rect = _resolve(annot.get("/Rect"))
            if rect is not None:
                x0, y0, x1, y1 = (float(v) for v in rect)
                box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                entry["rects"].setdefault(page_num, []).append(box)
    return [
        {
            "name": name,
            "field_type": info["field_type"],
            "pages": sorted(info["pages"]),
            "rects": info["rects"],
        }
        for name, info in sorted(by_name.items(), key=lambda kv: (min(kv[1]["pages"]), kv[0]))
    ]


def _remove_form_fields(writer, names):
    """Delete named top-level fields from a writer already built via
    ``clone_from``: their widgets from every page's /Annots, and their
    entries from the AcroForm /Fields array."""
    names = set(names)
    if not names:
        return

    for page in writer.pages:
        annots = _resolve(page.get("/Annots"))
        if not annots:
            continue
        keep = [ref for ref in annots if _qualified_field_name(ref.get_object()) not in names]
        if len(keep) == len(annots):
            continue
        if keep:
            page[NameObject("/Annots")] = ArrayObject(keep)
        else:
            del page[NameObject("/Annots")]

    acroform = _resolve(writer.root_object.get("/AcroForm"))
    if acroform is None:
        return
    fields = _resolve(acroform.get("/Fields"))
    if not fields:
        return
    acroform[NameObject("/Fields")] = ArrayObject(
        ref for ref in fields if _qualified_field_name(ref.get_object()) not in names
    )


def create_page_overlay(placements, page_width, page_height):
    """Build a one-page PDF holding every placement, painted back-to-front."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    for p in placements:
        _draw_image(c, p.image_path, page_width, page_height, p.x, p.y, p.scale)
    c.save()
    buf.seek(0)
    return buf


def add_images_to_pdf(input_pdf, placements: Iterable[Placement], output_pdf, remove_fields: Iterable[str] = ()):
    """Overlay several images onto a PDF and write the result.

    ``placements`` is grouped by page; for each affected page a single overlay
    is built with that page's images in list order (first = back, last = front)
    and merged onto the page. Pages with no placements are copied unchanged.

    ``remove_fields`` names top-level AcroForm fields (as returned by
    :func:`list_form_fields`) to delete outright — their widget annotations
    and their /Fields entry — before the overlays are merged in.

    Returns the sorted list of 1-based page numbers that received an image.
    Raises ``ValueError`` if a placement targets a page outside the document,
    or if both ``placements`` and ``remove_fields`` are empty.
    """
    placements = list(placements)
    remove_fields = list(remove_fields)
    if not placements and not remove_fields:
        raise ValueError("no placements given")

    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    by_page: dict[int, list[Placement]] = {}
    for p in placements:
        if p.page < 1 or p.page > total_pages:
            raise ValueError(
                f"page {p.page} out of range (PDF has {total_pages} pages)"
            )
        by_page.setdefault(p.page, []).append(p)

    writer = PdfWriter(clone_from=reader)
    _remove_form_fields(writer, remove_fields)

    for page_num, page_placements in by_page.items():
        target_page = writer.pages[page_num - 1]
        page_width = float(target_page.mediabox.width)
        page_height = float(target_page.mediabox.height)

        overlay_buf = create_page_overlay(page_placements, page_width, page_height)
        overlay_page = PdfReader(overlay_buf).pages[0]
        target_page.merge_page(overlay_page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    return sorted(by_page)


def add_image_to_pdf(input_pdf, image_path, output_pdf, page_num, x, y, scale):
    """Single-image wrapper, kept for the CLI and existing callers."""
    add_images_to_pdf(
        input_pdf,
        [Placement(image_path=image_path, page=page_num, x=x, y=y, scale=scale)],
        output_pdf,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Overlay an image on an existing PDF page."
    )
    parser.add_argument("input_pdf", help="Path to the input PDF")
    parser.add_argument("image", help="Path to the image file (PNG, JPG, etc.)")
    parser.add_argument("-o", "--output", default=None, help="Output PDF path")
    parser.add_argument("-p", "--page", type=int, default=1, help="Page number, 1-based (default: 1)")
    parser.add_argument("-x", type=float, default=None, help="X position in pt from left (default: centered)")
    parser.add_argument("-y", type=float, default=None, help="Y position in pt from bottom (default: centered)")
    parser.add_argument("-s", "--scale", type=float, default=0.25, help="Image width as fraction of page width (default: 0.25)")

    args = parser.parse_args()

    if args.output is None:
        stem = Path(args.input_pdf).stem
        args.output = f"{stem}_with_image.pdf"

    if not os.path.isfile(args.input_pdf):
        print(f"Error: PDF not found: {args.input_pdf}")
        sys.exit(1)
    if not os.path.isfile(args.image):
        print(f"Error: Image not found: {args.image}")
        sys.exit(1)

    try:
        add_image_to_pdf(args.input_pdf, args.image, args.output, args.page, args.x, args.y, args.scale)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    reader = PdfReader(args.input_pdf)
    target_page = reader.pages[args.page - 1]
    page_width = float(target_page.mediabox.width)
    page_height = float(target_page.mediabox.height)

    print(f"Done — saved to {args.output}")
    print(f"  Image placed on page {args.page} ({page_width:.0f} x {page_height:.0f} pt)")
    print(f"  Scale: {args.scale:.0%} of page width")
    if args.x is not None and args.y is not None:
        print(f"  Position: ({args.x:.1f}, {args.y:.1f}) pt from bottom-left")
    else:
        print("  Position: centered")


if __name__ == "__main__":
    main()
