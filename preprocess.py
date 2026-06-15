#!/usr/bin/env python
# coding: utf-8
import os

def preprocess_image(img_path, is_overlay=None, output_dir=None):
    """
    Since migrating to dash_viv_viewer and pyramidal OME-TIFFs,
    downsampling (>10,000px) and georeferencing are no longer required.
    This function simply acts as a pass-through.
    """
    if img_path is None:
        raise ValueError("img_path must be provided.")
        
    print(f"Skipping legacy preprocess downsampling. Returning original: {img_path}")
    return img_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Preprocess WSI image (Legacy Stub).")
    parser.add_argument("image_path", help="Path to input image")
    args = parser.parse_args()
    
    if os.path.exists(args.image_path):
        print(f"Processing {args.image_path}...")
        try:
            out = preprocess_image(args.image_path)
            print(f"Success: {out}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("File not found.")


