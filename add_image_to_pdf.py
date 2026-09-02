#!/usr/bin/env python3
"""
Add an image overlay to an existing PDF.

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
"""

import argparse
import os
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def create_image_overlay(image_path, page_width, page_height, x, y, scale):
    """Create a single-page PDF containing just the image, sized to the target page."""
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

    # Build overlay PDF in memory
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    c.drawImage(
        ImageReader(image_path),
        x, y, draw_w, draw_h,
        preserveAspectRatio=True,
        mask="auto",  # honour transparency for PNGs
    )
    c.save()
    buf.seek(0)
    return buf


def add_image_to_pdf(input_pdf, image_path, output_pdf, page_num, x, y, scale):
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (PDF has {total_pages} pages).")
        sys.exit(1)

    target_page = reader.pages[page_num - 1]
    page_width = float(target_page.mediabox.width)
    page_height = float(target_page.mediabox.height)

    overlay_buf = create_image_overlay(image_path, page_width, page_height, x, y, scale)
    overlay_page = PdfReader(overlay_buf).pages[0]

    # Merge overlay on top of the target page
    target_page.merge_page(overlay_page)

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Done — saved to {output_pdf}")
    print(f"  Image placed on page {page_num} ({page_width:.0f} x {page_height:.0f} pt)")
    print(f"  Scale: {scale:.0%} of page width")
    if x is not None and y is not None:
        print(f"  Position: ({x:.1f}, {y:.1f}) pt from bottom-left")
    else:
        print(f"  Position: centered")


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

    add_image_to_pdf(args.input_pdf, args.image, args.output, args.page, args.x, args.y, args.scale)


if __name__ == "__main__":
    main()
