import dash
from dash import html, dcc, Input, Output
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'dash_viv_viewer')))
from dash_viv_viewer import VivViewer
from flask import send_file, request
import uuid

# Sample image to test with.
# Change this to an actual valid local OME-TIFF path or S3 URL.
TEST_IMAGE_PATH = "s3://alextrywebsite/test/b1be4ed3-6dc5-4878-9580-7479a657d5fd.ome.tif" 

import argparse

parser = argparse.ArgumentParser(description='Loki VivViewer Test App')
parser.add_argument('--port', type=int, default=8080, help='Port to run the server on')
parser.add_argument('--token', type=str, required=True, help='Token for route paths')
args = parser.parse_args()

VALID_TOKEN = args.token

app = dash.Dash(__name__, url_base_pathname=f'/app/{VALID_TOKEN}/')
server = app.server

# 1. Custom proxy route for local files, replicating what app.py does.
# Because url_base_pathname is '/app/{VALID_TOKEN}/', the actual browser 
# URL for this route will be: /app/{VALID_TOKEN}/ome_tiff
@server.route("/ome_tiff")
def serve_ome_tiff():
    path = request.args.get("path")
    if not path:
        return "Missing path", 400
    path = urllib.parse.unquote(path)
    
    # Proxy S3 files through the backend to avoid Browser CORS issues
    if path.startswith("s3://"):
        import boto3
        from botocore.config import Config
        from flask import Response
        
        # Parse s3 path
        s3_path_no_prefix = path[5:]
        bucket, key = s3_path_no_prefix.split('/', 1)
        
        # Create a boto3 client (credentials should be configured on EC2 role or env)
        s3 = boto3.client('s3', config=Config(signature_version='s3v4'))
        
        try:
            # Get the object from S3
            s3_response = s3.get_object(Bucket=bucket, Key=key)
            
            # Stream the response back to the client
            def generate():
                for chunk in iter(lambda: s3_response['Body'].read(8192), b''):
                    yield chunk
                    
            return Response(
                generate(),
                mimetype='image/tiff',
                headers={
                    'Access-Control-Allow-Origin': '*', # Explicit CORS just in case
                    'Accept-Ranges': 'bytes', # Required for VivViewer
                    'Content-Length': str(s3_response['ContentLength'])
                }
            )
        except Exception as e:
            return f"Error proxying S3 file: {str(e)}", 500
            
    # Proxy Local files
    elif os.path.exists(path):
        return send_file(path, conditional=True, mimetype='image/tiff')
    else:
        return f"Not found: {path}", 404

# Helper to format URL
def get_image_url(path):
    # Direct HTTPS fetch since bucket has correct CORS
    if path.startswith("s3://"):
        s3_path_no_prefix = path[5:]
        bucket, key = s3_path_no_prefix.split('/', 1)
        
        # Determine correct region based on bucket name
        # (alextrywebsite is in Ohio/us-east-2)
        region = "us-east-2" if bucket == "alextrywebsite" else "us-east-1"
        
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    else:
        # Fallback to local proxy if it's a local file
        encoded_path = urllib.parse.quote(path, safe='')
        return f"/app/{VALID_TOKEN}/ome_tiff?path={encoded_path}"

image_url = get_image_url(TEST_IMAGE_PATH)

app.layout = html.Div([
    html.H1("Viv Viewer EC2 Test App"),
    html.Div(f"Testing image: {TEST_IMAGE_PATH}"),
    html.Div(f"Resolved URL: {image_url}"),
    html.Hr(),
    
    VivViewer(
        id='test-viewer',
        image_url=[image_url],
        height=800,
        rois=[]
    ),
    
    html.Hr(),
    html.H3("Drawn ROIs:"),
    html.Pre(id='roi-output', style={'backgroundColor': '#f4f4f4', 'padding': '10px'})
])

@app.callback(
    Output('roi-output', 'children'),
    Input('test-viewer', 'rois'),
    prevent_initial_call=True
)
def display_rois(rois):
    if not rois:
        return "No ROIs drawn yet"
    import json
    return json.dumps(rois, indent=2)

if __name__ == '__main__':
    # Run barebones app. Must use use_reloader=False if relying on specific ports.
    print(f"Starting VivViewer test app on port {args.port} with token {args.token}")
    app.run_server(host='0.0.0.0', port=args.port, debug=False, dev_tools_hot_reload=False)
