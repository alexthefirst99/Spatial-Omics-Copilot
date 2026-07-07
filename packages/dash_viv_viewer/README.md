# VivViewer for Dash

VivViewer is a high-performance interactive image viewer for Dash. It is designed to handle massive, multi-resolution images (like OME-TIFF) with ease, allowing you to visualize pathology slides, segmentation masks, and research data directly in your web browser.

## Features

- **Gigapixel Visualization**: Smoothly pan and zoom through images of any size.
- **Layer Stacking**: Overlay two images (e.g., H&E and Segmentation) and control their blending.
- **Side-by-Side View**: Compare two images in a synchronized dual-pane layout.
- **Measurement & ROIs**: Draw rectangles, polygons, or points to mark areas of interest.
- **Instant Performance**: Optimized with CSS-based blending for zero-lag interaction.

## Image URL Requirements

The `image_url` prop expects a valid **URL string** pointing to a resource that the viewer can load. This can be:

1.  **Public OME-TIFFs**: A direct link to a `.ome.tif` or `.ome.tiff` file served over HTTP.
2.  **Zarr Stores**: A URL pointing to a Zarr directory (usually ending in `.zarr`).
3.  **Proxy Endpoints**: If your data is private (e.g., in S3), you should provide a URL to a local server route that proxies the image data.

```python
# General case: A publicly accessible URL
image_url = "https://my-data-server.com/images/sample.ome.tif"

# Connect it to the viewer
viewer = dash_viv_viewer.VivViewer(image_url=image_url)
```

### Serving Local Images
If your images are stored on the same machine as your Dash server, you must expose them via a URL. The easiest way is to add a route to your Flask server:

```python
import os
from dash import Dash
from dash_viv_viewer import VivViewer, serve_directory

app = Dash(__name__)

# Start a background server to host your local images.
# This automatically handles CORS, Range Requests, and URL decoding.
IMAGE_DIR = "/path/to/your/images"
base_url = serve_directory(IMAGE_DIR, port=5001)

# In your layout:
# Images are now available at: {base_url}/filename.ome.tif
image_url = f"{base_url}/my_slide.ome.tif"
viewer = VivViewer(image_url=image_url)
```

> [!IMPORTANT]
> **HTTP Range Requests**: Large OME-TIFF files require the server to support "Range Requests." This allows the viewer to download only the specific tiles you are looking at. Using the built-in `serve_directory` utility handles this automatically by setting `conditional=True`. If you use a different server that does not support ranges, the browser will try to download the entire file (many GBs), which will likely crash the tab.

---

## Data Preparation

The VivViewer works best with **pyramidal OME-TIFF** files. If you have a standard image (PNG, JPG, or a flat TIFF), you should convert it first using the built-in utility:

```python
from dash_viv_viewer import convert_to_ome_tiff

# Convert a standard PNG to a high-performance OME-TIFF pyramid
ome_path = convert_to_ome_tiff("input.png", output_path="output.ome.tif")
```

---

## Using the Viewer in your Dash App

### Basic Example
To display a single image, simply provide the URL to an OME-TIFF file.

```python
import dash_viv_viewer
from dash import Dash, html

app = Dash(__name__)

app.layout = html.Div([
    dash_viv_viewer.VivViewer(
        id='my-viewer',
        image_url='https://example.com/slide.ome.tif',
        height=700
    )
])

if __name__ == '__main__':
    app.run_server(debug=True)
```

### Advanced: Overlaying Two Images
You can provide two images to compare them. The viewer allows you to toggle which one is "on top" and adjust its opacity.

```python
dash_viv_viewer.VivViewer(
    id='comparison-viewer',
    image_url=[
        'https://example.com/he_slide.ome.tif',
        'https://example.com/segmentation_mask.ome.tif'
    ],
    active_layer=1,        # Start with the second image as the overlay
    opacity={0: 1.0, 1: 0.5} # Base at 100%, Overlay at 50%
)
```

---

## UI Guide

### 1. View Mode
*   **Single Layer**: Stack images on top of each other. Great for checking how a mask aligns with the original image.
*   **Side by Side**: Shows both images in separate panes. Moving one pane automatically moves the other.

### 2. Active Layer
When in **Single Layer** mode, use the "Active Layer" dropdown to choose which image you want to control. The selected layer will be moved to the top of the stack.

### 3. Overlay Opacity
Slide the bar to change the transparency of the active top layer. This allows you to "see through" a mask to the underlying tissue.

### 4. Drawing Tools (Left Toolbar)
*   **Point (⚲)**: Click once to mark a single coordinate.
*   **Rectangle (▭)**: Click and drag to define a rectangular region.
*   **Polygon (⬡)**: Click to add points, then click the **Checkmark (✓)** to finish the shape.
*   **Undo/Clear**: Use the back-arrow to remove the last shape or the trash can to clear all.

---

## Component Reference (Props)

- **`image_url`**: String or List of strings. A publicly accessible HTTP URL pointing to an OME-TIFF or Zarr resource.
- **`height`**: Height of the viewer in pixels (default: 600).
- **`viewMode`**: Either `'single'` or `'side-by-side'`.
- **`active_layer`**: The index (0 or 1) of the layer currently being controlled.
- **`opacity`**: A dictionary like `{0: 1.0, 1: 0.5}` controlling the transparency of each layer.
- **`rois`**: Output property. A list of shapes drawn by the user.
- **`drawMode`**: Input property. Set to `'rect'`, `'polygon'`, or `'point'` to activate a tool programmatically.

---

## Acknowledgments

This component is a **Dash port** of the excellent work done by the **[Viv](https://github.com/hms-dbmi/viv)** project. Viv is a library for multiscale visualization of high-resolution biological image data, developed by the **[HMS-DBMI](https://dbmi.hms.harvard.edu/)**.
