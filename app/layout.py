import os
import shutil
import uuid
import time

import dash
import plotly.graph_objs as go
from dash import html, dcc

try:
    from app.inference import get_default_model_spec
except ImportError:
    from inference import get_default_model_spec

def custom_spinner(children, spinner_type="orbit", label="Loading visualization"):
    """Create a custom CSS-based loading indicator."""
    return dcc.Loading(
        type="circle",
        children=html.Div(
            className="custom-loading-wrapper",
            children=[
                html.Div(
                    className=f"custom-loader custom-loader-{spinner_type}",
                    role="status",
                    **{"aria-live": "polite"},
                    children=[
                        html.Div(className="custom-loader-orbit", children=[
                            html.Span(),
                            html.Span(),
                            html.Span(),
                        ]),
                        html.Div(className="custom-loader-copy", children=[
                            html.Span(label, className="custom-loader-title"),
                            html.Span("Preparing a clear view", className="custom-loader-subtitle"),
                        ]),
                    ],
                ),
                html.Div(className="custom-loading-content", children=children),
            ],
        ),
        className="custom-spinner-container",
        color="#0071e3",
        style={"backgroundColor": "transparent"}
    )

def S3Upload(id, label="Upload File", primary_text="Drop your H&E image here", file_types="Supports: TIFF, JPEG, PNG (up to 5GB)", accept="image/*"):
    return html.Div(className="s3-upload-wrapper", **{"data-accept": accept}, children=[
        html.Div(className="s3-upload-zone", children=[
            html.Div(className="upload-icon", children=[
                html.I(className="fa fa-cloud-upload", style={"fontSize": "32px", "marginBottom": "8px"})
            ]),
            html.Div(className="upload-text-primary", children=primary_text),
            html.Div(className="upload-text-secondary", children="or click to browse"),
            html.Div(className="upload-file-types", children=file_types),
            html.Div(className="upload-file-input-placeholder")
        ]),
        
        html.Div(className="upload-file-info", style={"display": "none"}, children=[
            html.Div(className="file-info-row", children=[
                html.I(className="fa fa-file-image-o", style={"marginRight": "10px"}),
                html.Span(className="file-name", children=""),
                html.Span(className="file-size", children="")
            ])
        ]),
        
        html.Div(className="upload-progress-container", style={"display": "none"}, children=[
            html.Div(className="upload-progress-bar-wrapper", children=[
                html.Div(className="upload-progress", style={"width": "0%"}),
                html.Div(className="upload-progress-text", children="0%")
            ]),
            html.Div(className="upload-status", children=""),
            html.Button("Cancel Upload", className="upload-cancel-btn", style={"display": "none"})
        ]),
        
        dcc.Input(id=id, className="upload-result", type="text", style={"display": "none"})
    ])

def create_layout(work_dir, folder_id):
    """Create the Dash layout for a workspace."""
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass
    try:
        shutil.rmtree(f"{work_dir}/user{folder_id}/selected_area/")
    except FileNotFoundError:
        pass

    gene_chosen = None
    default_model_spec = get_default_model_spec()
    chat_model_choices = [
        ("Ollama fast text  qwen2.5:0.5b", "ollama:qwen2.5:0.5b"),
        ("Ollama vision  qwen2.5vl:7b", "ollama:qwen2.5vl:7b"),
        ("Ollama local  qwen2.5vl:32b", "ollama:qwen2.5vl:32b"),
    ]
    if default_model_spec.startswith("deepinfra:"):
        deepinfra_model = default_model_spec.split(":", 1)[1]
        chat_model_choices.append(
            (
                f"DeepInfra  {deepinfra_model or 'set DEEPINFRA_MODEL'}",
                default_model_spec,
            )
        )
    elif default_model_spec not in {value for _, value in chat_model_choices}:
        chat_model_choices.append(
            (
                f"Ollama configured  {default_model_spec.split(':', 1)[-1]}",
                default_model_spec,
            )
        )
    chat_model_options = [
        html.Option(label, value=value, selected=value == default_model_spec)
        for label, value in chat_model_choices
    ]

    hist = go.Figure(
        data=go.Histogram(),
        layout=go.Layout(
            title=f'Histogram: selected region of {gene_chosen} for cell data',
            xaxis={'title': 'Gene expression', 'showline': True},
            yaxis={'title': 'Number of cells', 'showline': True},
            font=dict(color='white'),
            paper_bgcolor='black',
            plot_bgcolor='black'
        ),
    )

    table = go.Figure(
        data=go.Table(
            header={
                'values': ['Mean', 'Median', 'Std'],
                'align': 'center',
                'fill_color': 'black'
            },
            cells={
                'values': [0, 0, 0],
                'align': 'center',
                'fill_color': 'black'
            },
        ),
        layout=go.Layout(
            title=f'Statistics: selected region of {gene_chosen} for cell data',
            font=dict(color='white'),
            paper_bgcolor='black',
            plot_bgcolor='black'
        ),
    )

    return html.Div(
    id="body",
    children=[
        dcc.Location(id='url', refresh=False),

        html.Link(
            rel='stylesheet',
            href="https://use.fontawesome.com/releases/v5.5.0/css/all.css",
            integrity="sha384-B4dIYHKNBt8Bc12p+WXckhzcICo0wtJAoU8YZTY5qE0Id1GSseTk6S+L3BlXeVIU",
            crossOrigin="anonymous"
        ),
        html.Link(
            rel='stylesheet',
            href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css"
        ),

        html.Div(
            id='main-app',
            children=[
                html.Div(
                    id='header',
                    children=[
                        html.H4(id="logo", children='Spatial Omics Copilot', className="text"),
                        html.Button("Tutorial", id="tutorial-open-btn", className="tutorial-open-btn", type="button"),
                    ]
                ),

        html.Div(
            className='container clearfix',
            id="display",
            children=[

                html.Div(
                    id='left-column-temp',
                    children=[
                        html.Div(
                            id="submit-wrapper",
                            children=[
                                html.Div(
                                    id='submit-container',
                                    children=[
                                        html.Div(className="upload-data", children=[
                                            html.H5("Upload Histology Image", className="text", style={"fontWeight": "600", "marginBottom": "5px"}),
                                            html.P("Standard Upload (Chunked):", className="text", style={"fontSize": "14px", "marginBottom": "5px"}),
                html.Div([
                    html.Div([
                        html.Div("Drag and Drop or Click to Upload", className="upload-text"),
                        html.Div(className="upload-file-input-placeholder")
                    ], className="s3-upload-zone"),
                    
                    html.Div([
                        html.Div(className="file-name"),
                        html.Span(className="file-size")
                    ], className="upload-file-info", style={'display': 'none'}),
                    
                    html.Div([
                        html.Div([
                            html.Div(className="upload-progress"),
                            html.Div("0%", className="upload-progress-text")
                        ], className="upload-progress-bar-wrapper")
                    ], className="upload-progress-container", style={'display': 'none'}),
                    
                    html.Div(className="upload-status"),
                    
                    dcc.Input(id="upload-data-image-result-dash", type="text", className="upload-result", style={'display': 'none'}),
                    html.Button(id="upload-trigger-btn", className="upload-trigger", style={'width': '0', 'height': '0', 'opacity': '0', 'padding': '0', 'border': 'none', 'overflow': 'hidden'}),
                    
                    html.Button("Cancel", className="upload-cancel-btn", style={'display': 'none'})
                ], className="s3-upload-wrapper", **{"data-accept": ".tiff,.tif,.ome.tiff,.svs,.btf"}),

                html.P([
                        "Click 'Re-visualize Image' to view the tissue image.",
                    ], className="text upload-instructions", style={"fontSize": "13px", "color": "#666", "lineHeight": "1.4", "marginTop": "8px"}),
                html.Button('Re-visualize Image', className="button btn-secondary", id="visual-input", n_clicks=0, style={'backgroundColor': '#e5e5ea', 'color': '#1d1d1f'}),

                html.Div(className="upload-data omics-upload-data", style={"marginTop": "24px"}, children=[
                    html.H5("Upload Gene Expression Matrix", className="text", style={"fontWeight": "600", "marginBottom": "5px"}),
                    html.P("Spatial transcriptomics data (.h5ad):", className="text", style={"fontSize": "14px", "marginBottom": "5px"}),
                    S3Upload(
                        id="upload-spatial-h5ad-result",
                        primary_text="Drag and drop h5ad file here",
                        file_types="Supports: .h5ad gene expression matrix",
                        accept=".h5ad"
                    ),
                    html.Div(id="h5ad-upload-summary", className="omics-upload-result")
                ]),

                dcc.Store(id='processing-job-id'),
                                        ]),
                                        html.Br(),
                                        html.Br(),
                                        html.Br(),
                                        html.Br(),
                                        html.Button('END SESSION', className="button btn-danger", id="clear-cache", n_clicks=0, style={'backgroundColor': '#ff3b30', 'color': 'white'}),
                                        html.P(["If you had not clicked END SESSION",html.Br(),"you can still visualize your latest data without re-upload"], className="text upload-instructions", style={"fontSize": "13px", "color": "#666", "lineHeight": "1.4"}),
                                        html.Div([
                                            custom_spinner(html.Div(id="status1")),
                                            custom_spinner(html.Div(id="status5")),
                                            custom_spinner(html.Div(id="status6")),
                                        ], style={'display': 'none'}),
                                    ]
                                ),
                                html.Button("❮", id="toggle-submit-btn", n_clicks=0, className="toggle-btn toggle-control"),
                            ]
                        ),

                        custom_spinner(
                            html.Div(
                                id="input-image",
                                children=[
                                    html.Div([], style={"display": "flex", "flexDirection": "column", "alignItems": "center", "justifyContent": "center", "height": "100%", "opacity": "0.6"})
                                ],
                            ),
                        ),
                        html.Div(id="roi-gene-popup", className="roi-gene-popup"),
                    ],
                ),

                html.Div(
                    id='right-column-temp',
                    children=[
                        html.Div(className="chatbot-panel", children=[
                            html.Button("✕", id="chat-toggle", className="chat-toggle"),
                            html.Div(className="chat-content", children=[
                                html.H2("AI Chatbot"),
                                html.Div(className="chat-model-controls", children=[
                                    html.Select(
                                        id="chatModelSelect",
                                        className="chat-model-select",
                                        title="Choose model",
                                        children=chat_model_options,
                                    ),
                                ]),
                                html.Div(id="chatMessages", className="chat-messages"),
                                html.Div(className="chat-input", children=[
                                    dcc.Input(
                                        id="chatInput",
                                        type="text",
                                        placeholder="Type your message...",
                                        autoComplete="off",
                                        spellCheck=False,
                                    ),
                                    html.Button("Send", id="sendBtn"),
                                ]),
                                html.Div(style={"textAlign": "center", "marginTop": "20px"}, children=[
                                    html.Button("Clear Session", id="clearSessionBtn", className="clear-session-btn"),
                                    html.Div(id="clear-status", className="clear-status-msg")
                                ]),
                            ])
                        ]),
                    ],
                ),
                ],
        ),

        html.Div(
            id="footer",
            children=[
                html.A(html.I(className="fa fa-github", style={"font-size": "24px"}), href="https://github.com/GuangyuWangLab2021", target="_blank"),
                html.A(html.I(className="fa fa-linkedin-square", style={"font-size": "24px"}), href="https://www.linkedin.com/in/guangyu-wang-27696819b/", target="_blank"),
                html.A(html.I(className="fa fa-twitter", style={"font-size": "24px"}), href="https://twitter.com/Guangyu_Wang01", target="_blank"),
                html.P("©2023 by Wang lab.", className="text")
            ]
        ),
            ]
        )
    ]
)
