import json
import tifffile as tiff
from PIL import Image
import os
import numpy as np
import shutil
import cv2

# --- Optimized Plotting w/ OpenCV (Vectorized) ---

def overlay_with_custom_colors(img_path, json_path, save_json=True):
    """
    Overlay cell-type polygon outlines onto a full-resolution image.
    Uses OpenCV (cv2) for high-performance vectorized drawing.
    Input/Output uses tifffile/PIL to handle large images safely.
    """
    
    # 1. IO: Read Large Image Safely (Tifffile/PIL)
    #    Avoid cv2.imread for large WSIs.
    try:
        with tiff.TiffFile(img_path) as tif:
            img = tif.pages[0].asarray()
    except Exception as e:
        print(f"Tifffile read failed ({e}), trying PIL...")
        # Fallback for some formats
        img_pil = Image.open(img_path)
        img = np.array(img_pil)

    # Ensure RGB(A)
    if img.ndim == 2:  # Grayscale
        img = np.stack([img]*3, axis=-1)
    elif img.shape[2] == 3: # RGB -> RGBA
        # Add alpha channel for composition (or just keep RGB if we don't need transparency on base)
        # Actually, for drawing ON TOP of base, we can draw directly on a copy.
        # But to match previous logic (Base + Overlay), let's keep it simple.
        # We will draw directly on the image array (in-place or copy).
        pass

    # Create a writable copy for drawing (OpenCV modifies in-place)
    # Convert to standard contiguous array if needed
    # Image is usually read as RGB. OpenCV expects BGR for display/saving if using cv2.imwrite.
    # But we are using tifffile.imwrite (expects RGB).
    # OpenCV drawing functions don't care about color space, just values.
    # So we can draw RGB colors on an RGB array.
    draw_img = img.copy()
    if draw_img.shape[2] == 3:
        # Add Alpha for overlay? 
        # Original code created an Overlay layer then composited.
        # Here we just draw lines on the image. It's faster and simpler.
        # If we need strictly "Overlay" separate layer logic, we can do that too.
        # But usually users just want to see the lines.
        # Let's stick to modifying the image directly.
        pass

    h, w = draw_img.shape[:2]

    # Define folder early
    folder = os.path.basename(os.path.dirname(img_path))
    os.makedirs(f"./{folder}", exist_ok=True)

    # 2. Parsing: JSON -> Numpy Arrays (Fast Path)
    print(f"Parsing JSON {json_path}...")
    with open(json_path) as f:
        data = json.load(f)
    
    cells = data.get("cells", [])
    
    # Group contours by type for batched drawing
    # contours_by_type = { type_id: [ [pts], [pts] ... ] }
    contours_by_type = {}
    present_cell_types = set()

    # Mapping Logic (Same as before)
    TYPE_NUCLEI_DICT_PANNUKE = {
        1: "Neoplastic", 2: "Immune", 3: "Stromal", 4: "Epithelial", 5: "Fibroblast",
        6: "Endothelial", 7: "Cardiomyocyte", 8: "Cardiac Fibroblast", 9: "Smooth Muscle",
        10: "Adipose", 11: "Oligodendrocyte", 12: "Astrocyte", 13: "Neuron",
        14: "Vascular Smooth Muscle", 15: "Alveolar pneumocytes", 16: "Chondrocytes",
        17: "Hepatocyte", 18: "Glia", 19: "Pericentral hepatocytes",
        20: "Proliferating keratinocytes", 21: "Spinous keratinocytes",
        22: "Connective", 23: "Lamina propria",
    }
    NAME_TO_ID = {v.lower(): k for k, v in TYPE_NUCLEI_DICT_PANNUKE.items()}
    
    COLOR_DICT_CELLS = {
        1: [255, 0, 0],        2: [34, 221, 77],      3: [35, 92, 236],      4: [255, 209, 102],
        5: [255, 159, 68],     6: [200, 50, 50],      7: [60, 40, 120],      8: [35, 192, 236],
        9: [254, 255, 100],    10: [153, 102, 255],   11: [255, 159, 168],   12: [255, 59, 68],
        13: [92, 200, 186],    14: [255, 0, 100],     15: [34, 221, 177],    16: [35, 92, 136],
        17: [254, 55, 0],      18: [120, 68, 229],    19: [68, 133, 229],    20: [120, 229, 68],
        21: [0, 180, 229],     22: [120, 0, 68],      23: [229, 180, 68],    24: [229, 68, 180],
        25: [68, 229, 120],
    }

    # Iterate once
    for cell in cells:
        raw_type = cell.get("type")
        raw_poly = cell.get("contour") # [[x,y], [x,y]...]
        
        if not raw_poly or len(raw_poly) < 3:
            continue
            
        # Resolved Type ID
        if isinstance(raw_type, (int, float)):
             type_id = int(raw_type)
        else:
             type_id = NAME_TO_ID.get(str(raw_type).lower(), 0)
        
        present_cell_types.add(type_id)
        
        # Convert to numpy array of int32 for OpenCV
        # cv2.polylines expects List[np.array((N, 1, 2), dtype=int32)] or similar
        pts = np.array(raw_poly, dtype=np.int32).reshape((-1, 1, 2))
        
        if type_id not in contours_by_type:
            contours_by_type[type_id] = []
        contours_by_type[type_id].append(pts)

    # 3. Drawing: Batch Processing (Vectorized)
    print(f"Drawing {len(cells)} cells...")
    
    # Thickness
    thickness = 3
    
    sorted_types = sorted(list(contours_by_type.keys()))
    
    for type_id in sorted_types:
        contours = contours_by_type[type_id]
        color = COLOR_DICT_CELLS.get(type_id, [255, 255, 255]) # RGB
        
        # Draw all contours of this type at once
        # cv2.polylines(img, pts, isClosed, color, thickness)
        cv2.polylines(draw_img, contours, isClosed=True, color=color, thickness=thickness)

    # 4. Output: Save Safely
    save_path = f"./{folder}/overlay_transparent.tif"
    
    # If the original code outputted RGBA (with alpha), we might want to ensure 'draw_img' is saved correctly.
    # Tifffile handles numpy arrays efficiently.
    # Generate Pyramid Levels (using cv2 for numpy array)
    levels = []
    curr = draw_img
    h_curr, w_curr = curr.shape[:2]
    
    while w_curr > 256 or h_curr > 256:
        w_curr //= 2
        h_curr //= 2
        curr = cv2.resize(curr, (w_curr, h_curr), interpolation=cv2.INTER_LINEAR)
        levels.append(curr)

    # Write Pyramidal TIFF
    with tiff.TiffWriter(save_path, bigtiff=True) as tif:
        options = dict(tile=(256, 256), compression='zlib', photometric='rgb')
        metadata={'axes': 'YXS'}
        
        # Write Full Resolution
        tif.write(
            draw_img,
            subifds=len(levels),
            metadata=metadata,
            **options
        )
        
        # Write Sub-Resolutions
        for level_img in levels:
            tif.write(
                level_img,
                metadata=metadata,
                **options
            )
            
    print(f"✅ Pyramidal Overlay saved at {save_path} with {len(levels)} levels")

    # Save JSON
    if save_json:
        json_out_path = f"./{folder}/present_cell_types.json"
        with open(json_out_path, "w") as f:
            json.dump(sorted(list(present_cell_types)), f, indent=4)
        print(f"🟢 JSON saved at {json_out_path}")

    return save_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Overlay cell polygons on an image (Optimized).")
    parser.add_argument("--image", required=True, help="Path to base image (.tif)")
    parser.add_argument("--json", required=True, help="Path to cell JSON ({sample}_cells.json)")
    parser.add_argument("--output", required=True, help="Output path for overlay image")
    
    args = parser.parse_args()
    
    # Run Optimized Function
    # Note: Logic changed to accept json_path directly instead of 'gdf'
    result_path = overlay_with_custom_colors(args.image, args.json)
    
    # Move if needed
    if args.output and os.path.abspath(result_path) != os.path.abspath(args.output):
        shutil.move(result_path, args.output)
        print(f"Saved to {args.output}")