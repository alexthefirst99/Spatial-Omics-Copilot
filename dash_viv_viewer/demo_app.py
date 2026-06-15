import argparse
import os

from dash import Dash, html
from flask import Flask, send_from_directory
from flask_cors import CORS

from dash_viv_viewer import VivViewer, convert_to_ome_tiff


VALID_TOKEN = "hello"


def default_input_image():
    repo_demo_image = os.path.join(
        os.path.dirname(__file__),
        "demo_data",
        "data20_V1_Hskin_Melanoma_Xenium_tissue_image_PYRAMIDAL.tiff",
    )
    if os.path.exists(repo_demo_image):
        return repo_demo_image
    return "/Users/alex/Downloads/data20_V1_Hskin_Melanoma_Xenium_tissue_image_PYRAMIDAL.tiff"


def create_app(image_url, dual_layer=False):
    server = Flask(__name__)
    CORS(server)

    app = Dash(
        __name__,
        server=server,
        requests_pathname_prefix=f"/app/{VALID_TOKEN}/",
        routes_pathname_prefix=f"/app/{VALID_TOKEN}/",
    )

    viewer_image_url = [image_url, image_url] if dual_layer else image_url
    app.layout = html.Div(
        [
            html.H2("Interactive VivViewer"),
            VivViewer(
                id="viewer",
                image_url=viewer_image_url,
                height=700,
            ),
        ],
        style={"margin": 0, "padding": 0},
    )

    return app, server


def main():
    parser = argparse.ArgumentParser(description="Run the dash_viv_viewer notebook demo as a Python app.")
    parser.add_argument("--image", default=default_input_image(), help="Input image to convert to OME-TIFF.")
    parser.add_argument("--port", type=int, default=8080, help="Port to run the demo app on.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind.")
    parser.add_argument("--dual-layer", action="store_true", help="Load the image twice to test website-style overlay mode.")
    parser.add_argument("--no-convert", action="store_true", help="Use an existing OME-TIFF in demo_data without reconverting.")
    args = parser.parse_args()

    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "demo_data"))
    os.makedirs(output_dir, exist_ok=True)

    ome_filename = "test_pyramid.ome.tif"
    ome_path = os.path.join(output_dir, ome_filename)

    if args.no_convert and os.path.exists(ome_path):
        print(f"Using existing OME-TIFF: {ome_path}")
    else:
        print(f"Converting {args.image}...")
        ome_path = convert_to_ome_tiff(args.image, output_path=ome_path)
        print(f"Done: {ome_path}")

    image_url = f"/app/{VALID_TOKEN}/demo_data/{ome_filename}"
    app, server = create_app(image_url, dual_layer=args.dual_layer)

    @server.route(f"/app/{VALID_TOKEN}/demo_data/<path:filename>")
    def serve_demo_data(filename):
        return send_from_directory(output_dir, filename, conditional=True)

    print(f"Dash URL: http://127.0.0.1:{args.port}/app/{VALID_TOKEN}/")
    print(f"Image URL: http://127.0.0.1:{args.port}{image_url}")
    print(f"Dual layer: {args.dual_layer}")
    app.run_server(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
