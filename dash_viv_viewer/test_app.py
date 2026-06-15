import dash
from dash import html, Input, Output, callback
import json
from dash_viv_viewer import VivViewer

app = dash.Dash(__name__)

IMAGE_URL = "http://127.0.0.1:5001/images/df35dedb-4894-402d-8842-cbfcd6532934.ome.tif"

app.layout = html.Div([
    html.H3("dash_viv_viewer test", style={"fontFamily": "sans-serif", "padding": "12px"}),
    VivViewer(
        id="viewer",
        image_url=[IMAGE_URL, IMAGE_URL],
        height=600,
    ),
    html.H4("ROI coordinates:", style={"fontFamily": "monospace", "padding": "8px 12px"}),
    html.Pre(
        id="roi-out",
        style={"background": "#1e1e1e", "color": "#d4d4d4", "margin": "0 12px", "padding": 12, "borderRadius": 4}
    )
], style={"maxWidth": 1100, "margin": "0 auto"})

@callback(Output("roi-out", "children"), Input("viewer", "rois"))
def show_rois(rois):
    if not rois:
        return "No ROIs yet"
    return json.dumps(rois, indent=2)

if __name__ == "__main__":
    app.run(debug=True, port=8050)
