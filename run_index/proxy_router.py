from flask import Flask, request, Response
import requests, json
import boto3
from botocore import UNSIGNED
from botocore.config import Config
import os

# --------------------- AWS S3 SETUP ---------------------
AWS_REGION = "us-east-2"
BUCKET = "alextrywebsite"
PROXY_MAP_S3_KEY = "proxy_map.json"
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

s3_client = boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version=UNSIGNED))

app = Flask(__name__)

def load_proxy_map_from_s3():
    """Load proxy_map.json from S3"""
    try:
        response = s3_client.get_object(Bucket=BUCKET, Key=PROXY_MAP_S3_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"[ERROR] Failed to load proxy_map from S3: {e}")
        return {}

def get_port(token):
    data = load_proxy_map_from_s3()
    return data.get(token)

@app.route("/app/<token>/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/app/<token>/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def proxy(token, path):
    port = get_port(token)
    if not port:
        return f"❌ Token '{token}' not found", 404

    url = f"http://127.0.0.1:{port}/app/{token}/{path}"
    print("this is url", url)
    if request.query_string:
        url += "?" + request.query_string.decode()

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers if k != 'Host'},
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
        )
        excluded = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]
        return Response(resp.content, resp.status_code, headers)
    except requests.exceptions.ConnectionError:
        return f"🚨 Port {port} unreachable", 502


@app.route("/tiles/<token>/<path:path>", methods=["GET", "POST"])
def tile_proxy(token, path):
    port = get_port(token)
   # if not port:
    #    return f"❌ Token '{token}' not found", 404

    tile_port = port+1
    url = f"http://127.0.0.1:{tile_port}/{path}"
    if request.query_string:
        url += "?" + request.query_string.decode()
    print("this is url tile client", url)
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers if k.lower() != "host"},
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
        )
        excluded = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]
        return Response(resp.content, resp.status_code, headers)
    except requests.exceptions.ConnectionError:
        return f"🚨 Tile port {tile_port} unreachable", 502




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000)
