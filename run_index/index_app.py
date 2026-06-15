import dash
from dash import dcc, html
from dash.dependencies import Output, Input, State
import subprocess, socket, threading, uuid, time, smtplib, json, os, signal
from pathlib import Path
from datetime import datetime, timedelta
import boto3
from flask import request, abort
import ipaddress
from botocore import UNSIGNED
from botocore.config import Config

# --------------------- AWS S3 SETUP ---------------------
AWS_REGION = "us-east-2"
BUCKET = "alextrywebsite"
PROXY_MAP_S3_KEY = "proxy_map.json"
SESSIONS_S3_KEY = "user_sessions.csv"

s3_client = boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version=UNSIGNED))

def load_proxy_map_from_s3():
    """Load proxy_map.json from S3"""
    try:
        response = s3_client.get_object(Bucket=BUCKET, Key=PROXY_MAP_S3_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_proxy_map_to_s3(data):
    """Save proxy_map.json to S3"""
    try:
        s3_client.put_object(
            Bucket=BUCKET,
            Key=PROXY_MAP_S3_KEY,
            Body=json.dumps(data),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"Error saving proxy_map to S3: {e}")

def save_session_to_s3(token, email, port, start_time, end_time):
    """Append session info to user_sessions.csv on S3"""
    try:
        # Load existing CSV from S3
        try:
            response = s3_client.get_object(Bucket=BUCKET, Key=SESSIONS_S3_KEY)
            csv_content = response['Body'].read().decode('utf-8')
        except:
            # Create CSV with header if it doesn't exist
            csv_content = "token,email,port,start_time,end_time\n"
        
        # Append new session data
        csv_content += f"{token},{email},{port},{start_time.isoformat()},{end_time.isoformat()}\n"
        
        # Save back to S3
        s3_client.put_object(
            Bucket=BUCKET,
            Key=SESSIONS_S3_KEY,
            Body=csv_content,
            ContentType='text/csv'
        )
        print(f"[INFO] Saved session {token} to S3")
    except Exception as e:
        print(f"[ERROR] Failed to save session to S3: {e}")

# --------------------- DASH SETUP ---------------------
EC2_PUBLIC_IP = "wanglab.tech"
SESSION_DURATION_SECONDS = 7200  # 2 hours
running_sessions = {}

app = dash.Dash(
    __name__,
    requests_pathname_prefix="/loki_launch/",
    routes_pathname_prefix="/loki_launch/",
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600&display=swap",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
    ]
)
server = app.server

# --------------------- IP RESTRICTION ---------------------
# Allowed IPs / Subnets
ALLOWED_NETWORKS = [
    ipaddress.ip_network("127.0.0.1/32"),    # Localhost
    ipaddress.ip_network("10.108.56.22/32"), # Specific Internal IP
    ipaddress.ip_network("206.83.0.0/16"),   # Methodist Hospital Network (Covers all coworkers)
    ipaddress.ip_network("10.0.0.0/8"),    # Uncomment to allow entire private network
]

@server.before_request
def limit_remote_addr():
    # 1. Get Client IP
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    if not client_ip:
        print("[Access Control] ⚠️ Could not determine Client IP. Request blocked.")
        abort(403)

    # Handle multiple IPs in X-Forwarded-For
    client_ip = client_ip.split(',')[0].strip()
    
    # 2. Check Allowed
    try:
        ip = ipaddress.ip_address(client_ip)
        allowed = False
        for net in ALLOWED_NETWORKS:
            if ip in net:
                allowed = True
                break
        
        # Log decision
        if not allowed:
            print(f"[Access Control] 🚫 BLOCKED: {client_ip}")
            abort(403)
        # else:
            # print(f"[Access Control] ✅ ALLOWED: {client_ip}")

    except ValueError:
        print(f"[Access Control] ⚠️ Invalid IP Format: {client_ip}")
        abort(403)

app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>Loki - Access Portal</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

# --- Helper functions ---
def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def add_token_to_map(token, port):
    data = load_proxy_map_from_s3()
    data[token] = port
    save_proxy_map_to_s3(data)

def send_email(recipient_email, link, expired=False):
    FROM = "wanglab2025@gmail.com"
    SUBJECT = "Loki Session Expired" if expired else "Link To Access Your Loki Instance"
    BODY = (
        f"Your Loki session at {link} has expired. If you need a new session, please relaunch it from the Loki portal."
        if expired else
        f"Access your interactive genomic visualization here: {link}.\n\n"
        "Please note that each token is valid for 2 hours. After this period, the session will be terminated "
        "and all associated data will be purged.\n\n"
        "Please reply to this email if you have any problem with the session."
    )
    message = f"Subject: {SUBJECT}\n\n{BODY}"
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(FROM, "lqjb ezgk pgje mfij")
        server.sendmail(FROM, recipient_email, message)
        server.quit()
        print(f"[Email] Sent {'expiration' if expired else 'launch'} email to {recipient_email}")
    except Exception as e:
        print(f"[Email] Failed to send email: {e}")

def run_external_app(port, token, email):
    venv_python = "/home/ssm-user/github/env/bin/python"
    script_path = "/mnt/data/loki2_web/EC2_Loki2_web.viv/app/app.py"
    command = [venv_python, script_path, "--port", str(port), "--token", token]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    pid = process.pid
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=SESSION_DURATION_SECONDS)
    running_sessions[token] = {
        "port": port, "email": email, "pid": pid, "start_time": start_time, "end_time": end_time
    }
    print(f"[INFO] Started app on port {port} with PID {pid} for token {token}")
    
    def auto_kill_after_timeout():
        time.sleep(SESSION_DURATION_SECONDS)
        try:
            try:
                data = load_proxy_map_from_s3()
                if token in data:
                    del data[token]
                    save_proxy_map_to_s3(data)
                    print(f"[INFO] Removed token {token} from proxy_map.json")
            except Exception as e:
                print(f"[ERROR] Failed to remove token {token} from proxy_map.json: {e}")
            send_email(email, f"https://{EC2_PUBLIC_IP}/app/{token}/", expired=True)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            print(f"[INFO] Killed session PID {pid} after timeout.")
        except ProcessLookupError:
            print(f"[INFO] Session PID {pid} already exited before timeout.")
        except Exception as e:
            print(f"[ERROR] Failed to kill session {token}: {e}")
        running_sessions.pop(token, None)
    
    threading.Thread(target=auto_kill_after_timeout, daemon=True).start()
    for line in process.stdout:
        print(f"[App on port {port}] {line.strip()}")

def count_running_jobs():
    try:
        result = subprocess.run(["pgrep", "-f", "app/app.py"], capture_output=True, text=True)
        return len([pid for pid in result.stdout.strip().split("\\n") if pid.strip()])
    except Exception as e:
        print(f"[Error] Could not count running jobs: {e}")
        return 0

# --------------------- Styled Layout ---------------------
app.layout = html.Div(
    className="portal-container",
    children=[
        dcc.Store(id="launch-trigger"),
        dcc.Store(id="launch-data"),
        
        # Hero Section
        html.Div(
            className="hero-section",
            children=[
                html.Div(className="hero-content", children=[
                    html.H1("Loki", className="hero-title"),
                    html.P("Advanced AI-Powered Histopathology Analysis", className="hero-subtitle"),
                    html.Div(className="hero-badge", children="Access Portal")
                ])
            ]
        ),
        
        # Main Content
        html.Div(
            className="main-content",
            children=[
                # Access Card
                html.Div(
                    className="access-card",
                    children=[
                        html.Div(className="card-icon", children=[
                            html.I(className="fas fa-lock")
                        ]),
                        html.H2("Authorized Access", className="card-title"),
                        html.P("Enter your Houston Methodist email to launch a session", className="card-subtitle"),
                        
                        # Email Input
                        html.Div(className="input-group", children=[
                            html.Label("Email Address", className="input-label"),
                            dcc.Input(
                                id='email-input',
                                type='email',
                                placeholder="your.name@houstonmethodist.org",
                                className="email-input"
                            ),
                            html.Div(id="email-error", className="error-message")
                        ]),
                        
                        # Terms
                        dcc.Checklist(
                            id='terms-check',
                            options=[{
                                'label': "I acknowledge that the session will be terminated after 2 hours",
                                'value': 'agree'
                            }],
                            className="terms-check"
                        ),
                        
                        # Launch Button
                        html.Button(
                            ["Launch Session ", html.I(className="fas fa-arrow-right")],
                            id="run-button",
                            n_clicks=0,
                            className="launch-button"
                        ),
                        
                        html.Div(id="launch-output", className="launch-output")
                    ]
                ),
                
                # Info Section
                html.Div(
                    className="info-section",
                    children=[
                    #     html.H3("About Loki"),
                    #     html.P("Loki is an interactive browser for visualizing gigapixel spatial-omics datasets. Navigate from tissue-wide expression down to single-cell resolution in real-time."),
                        html.Div(className="info-stats", children=[
                            html.Div(className="stat-item", children=[
                                html.I(className="fas fa-clock"),
                                html.Span("2 Hour Sessions")
                            ]),
                            html.Div(className="stat-item", children=[
                                html.I(className="fas fa-lock"),
                                html.Span("Secure Access")
                            ]),
                            html.Div(className="stat-item", children=[
                                html.I(className="fas fa-envelope"),
                                html.Span("Email Notification")
                            ])
                        ])
                    ]
                )
            ]
        ),
        
        # Footer
        html.Div(
            className="portal-footer",
            children=[
                html.P("© 2025 Wang Lab. All rights reserved.", className="footer-text")
            ]
        )
    ]
)

# --------------------- Callbacks ---------------------
@app.callback(
    Output("launch-output", "children", allow_duplicate=True),
    Output("email-error", "children"),
    Output("launch-trigger", "data"),
    Output("launch-data", "data"),
    Input("run-button", "n_clicks"),
    State("email-input", "value"),
    State("terms-check", "value"),
    prevent_initial_call=True
)
def prepare_launch(n_clicks, email, terms):
    # Validate email
    if not email:
        return "", "Please enter your email address", None, None
    
    # if not email.lower().endswith("@houstonmethodist.org"):
    #     return "", "⚠️ Access restricted to Houston Methodist email addresses only", None, None
    
    if not terms or 'agree' not in terms:
        return "⚠️ You must acknowledge the session terms before launching", "", None, None
    
    if count_running_jobs() >= 10:
        return html.Div([
            "❌ Server is currently at capacity (10 concurrent sessions).",
            html.Br(),
            "Please try again later."
        ], className="error-box"), "", None, None

    launch_msg = html.Div([
        html.I(className="fas fa-spinner fa-spin"),
        " Preparing your session... Please wait."
    ], className="loading-box")

    data = {"email": email}
    return launch_msg, "", True, data

@app.callback(
    Output("launch-output", "children", allow_duplicate=True),
    Input("launch-trigger", "data"),
    State("launch-data", "data"),
    prevent_initial_call="initial_duplicate"
)
def complete_launch(trigger, data):
    if not trigger or not data:
        return dash.no_update

    try:
        port = get_free_port()
        token = str(uuid.uuid4())[:8]
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=SESSION_DURATION_SECONDS)
        
        # Save session to S3
        save_session_to_s3(token, data["email"], port, start_time, end_time)
        
        threading.Thread(target=run_external_app, args=(port, token, data["email"]), daemon=True).start()
        link = f"https://{EC2_PUBLIC_IP}/app/{token}/"
        add_token_to_map(token, port)
        send_email(data["email"], link)

        return html.Div([
            html.I(className="fas fa-check-circle", style={"color": "#28a745", "marginRight": "8px"}),
            f"Session prepared! A link has been sent to {data['email']}.",
            html.Br(),
            "Please check your inbox or spam folder."
        ], className="success-box")
    except RuntimeError:
        return html.Div([
            html.I(className="fas fa-exclamation-triangle"),
            " Server encountered an error. Please try again."
        ], className="error-box")

if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=2002)
