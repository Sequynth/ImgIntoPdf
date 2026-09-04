# Add Image to PDF

Overlay images (signatures, stamps, logos, …) onto the pages of an existing PDF.
Each image is drawn on top of the page content, its aspect ratio is preserved,
and PNG transparency is honoured. Pages you don't touch are copied through
unchanged.

Two entry points share the same placement logic:

| Script | Use it for |
| --- | --- |
| [add_image_to_pdf.py](add_image_to_pdf.py) | Command line — scripted or one-off placement of a single image by coordinates. |
| [add_image_to_pdf_gui.py](add_image_to_pdf_gui.py) | Point-and-click — add several images, drag each to position it, order and delete them from the Layers sidebar, then *Save As…* |

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
python add_image_to_pdf_gui.py [INPUT.pdf] [IMAGE ...]
```

Every argument is optional; anything ending in `.pdf` is the PDF and every other
argument is an image added to page 1. You can also open the PDF and add images
from within the window.

### Workflow

1. **Open PDF…**, then page to the sheet you want with ◀ / ▶ or Page Up / Down.
2. **Add Images…** — pick one or more files. Each is dropped on the page in
   view: the first centred, each further one offset so they don't hide each
   other.
3. Drag each image to place it, drag its corner handle (or the wheel / slider)
   to size it.
4. Use the **Layers** sidebar to stack images on a page correctly and to remove
   any you don't want.
5. **Save As…** writes every placement into a new PDF.

### Layers sidebar

Lists every image on the **current page**, front-most at the top. Selecting a
row selects that image on the canvas (and vice versa).

| Button | Effect |
| --- | --- |
| ▲ / ▼ | Move the selected layer one step toward the front / back. |
| ⤒ / ⤓ | Send the selected layer to the front / back. |
| ✕ Delete (or the `Delete` key) | Remove the selected layer. |

### Form Fields sidebar

Lists every AcroForm field found in the document (name and the page(s) it
appears on), independent of which page is in view. **Show fields** (in the
toolbar) draws each field on the current page as a dashed outline — orange
for a kept field, red for one marked for deletion — so you can see what a
field covers, e.g. before stamping an image over it.

| Button | Effect |
| --- | --- |
| ✕ Delete (or the `Delete` key, with the list focused) | Mark the selected field(s) for deletion. |
| ↺ Restore | Unmark the selected field(s). |

Marked fields are only removed when you **Save As…** — nothing is written
until then. Deleting a field removes its widget annotation(s) and its entry
in the AcroForm; other fields are untouched. This is useful for dropping a
field once its value has been stamped over with an image (e.g. a signature),
so the space doesn't stay a fillable/re-editable field in the output.

### Controls

| Action | Effect |
| --- | --- |
| Click an image | Select it. |
| Drag the image | Move the selected layer. |
| Drag the corner handle | Resize (keeps the aspect ratio). |
| Mouse wheel | Scale about the image centre. |
| Scale slider | Set the width as a percentage of the page width. |
| Arrow keys | Nudge by 1 pt (hold **Shift** for 10 pt) — click the canvas first so it has focus. |
| Page Up / Page Down, or ◀ / ▶ | Change page. |
| **Center** | Re-centre the selected image on the current page. |
| **Save As…** | Write the result to a new PDF. |

The status bar shows the current page size, the selected image's position in
points, and its scale — the same values the command-line tool accepts.

Saving to the source PDF is blocked; choose a different file. Opening a new PDF
while images are placed asks first, then clears them.

## Notes

- Images sit on top of the existing page content; they are not merged into the
  text layer and do not become selectable text.
- Only pages that receive an image are modified. Page count, order, and every
  other page are preserved.
- Images on the same page are drawn in the sidebar's order — the bottom row
  first, the top row last (on top).
- Transparent PNG areas stay transparent (`mask="auto"`).
- **Interactive form fields are preserved.** The whole document is cloned
  (`PdfWriter(clone_from=…)`), so the AcroForm, its fields, and the widget
  annotations survive — including on pages that receive an image. The image is
  drawn *beneath* the field widgets, so it can't be used to hide a field.
  Dynamic **XFA** forms are not tested and may not survive; an existing digital
  signature is invalidated by any edit.
- Fields marked for deletion in the GUI's **Form Fields** sidebar (see above)
  are the one deliberate exception: they're removed outright, on top of the
  same `clone_from` save path — every other field survives untouched.

## Tests

```bash
pip install -r requirements.txt
pytest
```

Covers `add_images_to_pdf()` and `list_form_fields()` — the functions behind
*Save As…* and the Form Fields sidebar: images land on the requested pages,
stacked images all embed, untouched pages pass through, interactive form
fields survive (or are removed when named in `remove_fields`), and
out-of-range pages raise. The Tkinter GUI itself is exercised by hand.
