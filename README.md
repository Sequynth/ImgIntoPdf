# Add Image to PDF

Overlay an image (signature, stamp, logo, …) onto a page of an existing PDF.
The image is drawn on top of the page content, its aspect ratio is preserved,
and PNG transparency is honoured. All other pages are copied through unchanged.

Two entry points share the same placement logic:

| Script | Use it for |
| --- | --- |
| [add_image_to_pdf.py](add_image_to_pdf.py) | Command line — scripted or one-off placement by coordinates. |
| [add_image_to_pdf_gui.py](add_image_to_pdf_gui.py) | Point-and-click — drag the image to position it, drag a handle to resize, then *Save As…* |

## Requirements

- **Python 3.8+**
- The packages in [requirements.txt](requirements.txt):
  - [`pypdf`](https://pypi.org/project/pypdf/) — read the source PDF and merge the overlay
  - [`reportlab`](https://pypi.org/project/reportlab/) — render the image into a PDF overlay
  - [`Pillow`](https://pypi.org/project/Pillow/) — read image dimensions / transparency
  - [`pypdfium2`](https://pypi.org/project/pypdfium2/) — rasterise pages for the GUI preview
- The GUI also needs **Tkinter**. It ships with the python.org installers on
  Windows and macOS. On Linux install it separately, e.g. `sudo apt install python3-tk`.

## Installation

```bash
# from the project directory
python -m venv .venv

# activate the virtual environment
#   Windows (PowerShell):
.venv\Scripts\Activate.ps1
#   Windows (cmd):
.venv\Scripts\activate.bat
#   macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage — command line

```bash
python add_image_to_pdf.py INPUT.pdf IMAGE [options]
```

### Positional arguments

| Argument | Description |
| --- | --- |
| `INPUT.pdf` | Path to the existing PDF. |
| `IMAGE` | Path to the image (PNG, JPG, GIF, BMP, TIFF, …). |

### Options

| Option | Default | Description |
| --- | --- | --- |
| `-o`, `--output` | `<input>_with_image.pdf` | Output PDF path. |
| `-p`, `--page` | `1` | Page to place the image on (1-based). |
| `-x` | centered | X position in points from the **left** edge. |
| `-y` | centered | Y position in points from the **bottom** edge. |
| `-s`, `--scale` | `0.25` | Image width as a fraction of the page width (`0.25` = quarter of the page width). Height follows from the aspect ratio. |

Positions and sizes are in PDF **points** (1 pt = 1/72 inch). The origin is the
bottom-left corner of the page. A US-Letter page is 612 × 792 pt; A4 is
595 × 842 pt. If only one of `-x` / `-y` is given, the other axis is centered.

### Examples

```bash
# Centered on page 1, quarter of the page width
python add_image_to_pdf.py document.pdf logo.png

# Page 3, positioned near the top-right, one third of the page width
python add_image_to_pdf.py document.pdf logo.png -p 3 -x 400 -y 700 -s 0.33

# Custom position, half width, explicit output name
python add_image_to_pdf.py doc.pdf stamp.png -o stamped.pdf -x 100 -y 200 -s 0.5
```

## Usage — GUI

```bash
python add_image_to_pdf_gui.py [INPUT.pdf] [IMAGE]
```

Both arguments are optional; anything ending in `.pdf` is treated as the PDF and
anything else as the image. You can also open both from within the window.

### Controls

| Action | Effect |
| --- | --- |
| Drag the image | Move it. |
| Drag the corner handle | Resize (keeps the aspect ratio). |
| Mouse wheel | Scale about the image centre. |
| Scale slider | Set the width as a percentage of the page width. |
| Arrow keys | Nudge by 1 pt (hold **Shift** for 10 pt). |
| Page Up / Page Down, or ◀ / ▶ | Change page. |
| **Center** | Re-centre the image on the current page. |
| **Save As…** | Write the result to a new PDF. |

The status bar shows the current page size, the image position in points, and
the scale — the same values the command-line tool accepts, so you can position
in the GUI and reproduce it on the command line.

Saving to the source PDF is blocked; choose a different file.

## Notes

- The image sits on top of the existing page content; it is not merged into the
  text layer and does not become selectable text.
- Only the target page is modified. Page count, order, and all other pages are
  preserved.
- Transparent PNG areas stay transparent (`mask="auto"`).
