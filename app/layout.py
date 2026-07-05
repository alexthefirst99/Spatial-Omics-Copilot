import os
import shutil
import uuid
import time

import dash
import plotly.graph_objs as go
from dash import html, dcc

# Custom spinner helper function
def custom_spinner(children, spinner_type="orbit", label="Loading visualization"):
    """Create a custom CSS-based loading indicator."""
    return dcc.Loading(
        type="circle",  # Use built-in type, we'll hide it with CSS
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
        # Upload Drop Zone
        html.Div(className="s3-upload-zone", children=[
            html.Div(className="upload-icon", children=[
                html.I(className="fa fa-cloud-upload", style={"fontSize": "32px", "marginBottom": "8px"})
            ]),
            html.Div(className="upload-text-primary", children=primary_text),
            html.Div(className="upload-text-secondary", children="or click to browse"),
            html.Div(className="upload-file-types", children=file_types),
            # File input will be created by JavaScript
            html.Div(className="upload-file-input-placeholder")
        ]),
        
        # File Info (shown after selection)
        html.Div(className="upload-file-info", style={"display": "none"}, children=[
            html.Div(className="file-info-row", children=[
                html.I(className="fa fa-file-image-o", style={"marginRight": "10px"}),
                html.Span(className="file-name", children=""),
                html.Span(className="file-size", children="")
            ])
        ]),
        
        # Progress Bar Container
        html.Div(className="upload-progress-container", style={"display": "none"}, children=[
            html.Div(className="upload-progress-bar-wrapper", children=[
                html.Div(className="upload-progress", style={"width": "0%"}),
                html.Div(className="upload-progress-text", children="0%")
            ]),
            html.Div(className="upload-status", children=""),
            # Cancel button (hidden by default, shown during upload)
            html.Button("Cancel Upload", className="upload-cancel-btn", style={"display": "none"})
        ]),
        
        # Hidden input for Dash callback
        dcc.Input(id=id, className="upload-result", type="text", style={"display": "none"})
    ])

def create_layout(work_dir, folder_id):
    """
    Create and return the Dash HTML layout for the Mjolnir app.

    :param work_dir: The working directory for user data.
    :param folder_id: Some identifier (string) for user session/folder.
    :return: An `html.Div` representing the entire UI layout.
    """
    # Clean up leftover files/folders for this user/folder if they exist
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass
    try:
        shutil.rmtree(f"{work_dir}/user{folder_id}/selected_area/")
    except FileNotFoundError:
        pass

    # Example placeholders for demonstration
    gene_chosen = None

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

        # Hero/Intro Section with Parallax — commented out to save resources
        # html.Div(
        #     id='hero-section',
        #     className='hero-parallax',
        #     children=[
        #         html.Canvas(id='hero-canvas', style={'position': 'absolute', 'top': 0, 'left': 0, 'width': '100%', 'height': '100%', 'zIndex': 1}),
        #         html.Div(className='hero-content', style={'zIndex': 2, 'position': 'relative'}, children=[
        #             html.H1('Spatial Omics Copilot', className='hero-title'),
        #             html.P('AI-assisted spatial omics interpretation', className='hero-subtitle'),
        #             html.Button([
        #                 'Get Started ',
        #                 html.I(className='fa fa-arrow-down')
        #             ], id='scroll-to-app-btn', className='hero-btn')
        #         ])
        #     ]
        # ),

        # Main App Section (wrapped)
        html.Div(
            id='main-app',
            children=[
                html.Div(
                    id='header',
                    children=[
                        html.H4(id="logo", children='Spatial Omics Copilot', className="text"),
                        html.Button("Tutorial", id="tutorial-open-btn", className="tutorial-open-btn", type="button"),
                        # html.H4(children='/ˈlɒki/', className="text")
                    ]
                ),

        html.Div(
            className='container clearfix',
            id="display",
            children=[

                # Left Column holds the map + floating submit container on top
                html.Div(
                    id='left-column-temp',
                    children=[
                        # Submit panel floats over the map area
                        html.Div(
                            id="submit-wrapper",
                            children=[
                                html.Div(
                                    id='submit-container',
                                    children=[
                                        html.Div(className="upload-data", children=[
                                            html.H5("Upload Histology Image", className="text", style={"fontWeight": "600", "marginBottom": "5px"}),
                                            html.P("Standard Upload (Chunked):", className="text", style={"fontSize": "14px", "marginBottom": "5px"}),
                                            # -------------------------------------------------------------
                # Custom Direct S3 Multipart Uploader
                # -------------------------------------------------------------
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
                    
                    # Hidden input to store result for Dash callback
                    dcc.Input(id="upload-data-image-result-dash", type="text", className="upload-result", style={'display': 'none'}),
                    html.Button(id="upload-trigger-btn", className="upload-trigger", style={'width': '0', 'height': '0', 'opacity': '0', 'padding': '0', 'border': 'none', 'overflow': 'hidden'}),
                    
                    html.Button("Cancel", className="upload-cancel-btn", style={'display': 'none'})
                ], className="s3-upload-wrapper"),

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

                # -------------------------------------------------------------
                # Progress Bar for Preprocessing (Backend)
                # ------------------------------------------------------------- 
                # Disabled: using JS-based bar inside uploader
                # html.Div(id='processing-status-container', style={'marginTop': '20px'}),
                # dcc.Interval(id='processing-interval', interval=1000, n_intervals=0, disabled=True),
                dcc.Store(id='processing-job-id'),
                
                # Hidden Dash Uploader (Legacy - kept if needed or can be removed)
                # du.Upload(...)
                                        ]),
                                        html.Br(),
                                        html.Br(),
                                        # Removed Direct-to-S3 section as requested
                                        html.Br(),
                                        html.Br(),
                                        html.Button('END SESSION', className="button btn-danger", id="clear-cache", n_clicks=0, style={'backgroundColor': '#ff3b30', 'color': 'white'}),
                                        html.P(["If you had not clicked END SESSION",html.Br(),"you can still visualize your latest data without re-upload"], className="text upload-instructions", style={"fontSize": "13px", "color": "#666", "lineHeight": "1.4"}),
                                        # Hidden status loaders grouped to prevent flex-gap usage
                                        # change hash color to black
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

                        # Map display area
                        custom_spinner(
                            html.Div(
                                id="input-image",
                                children=[
                                    html.Div([
                                        # html.Img(src="/assets/logo.png", style={"height": "60px", "marginBottom": "20px"}), # Optional branding
                                        # html.H2("Welcome to Loki", className="text", style={"marginBottom": "10px"}),
                                        # html.P("Upload an H&E image to get started.", className="text", style={"color": "#666"})
                                    ], style={"display": "flex", "flexDirection": "column", "alignItems": "center", "justifyContent": "center", "height": "100%", "opacity": "0.6"})
                                ],
                            ),
                        ),
                        html.Div(id="roi-gene-popup", className="roi-gene-popup"),
                    ],
                ),

                # Right column for graphs + Chatbot
                html.Div(
                    id='right-column-temp',
                    children=[
                        # html.Div(id="hist", children=[dcc.Graph(figure=hist, style={"height": "300px"})]),
                        # html.Div(id="stats", children=[dcc.Graph(figure=table, style={"height": "300px"})])
                        
                        html.Div(className="chatbot-panel", children=[
                            html.Button("✕", id="chat-toggle", className="chat-toggle"),
                            html.Div(className="chat-content", children=[
                                html.H2("AI Chatbot"),
                                html.Div(className="chat-model-controls", children=[
                                    html.Select(
                                        id="chatModelSelect",
                                        className="chat-model-select",
                                        title="Choose model",
                                        children=[
                                            html.Option("Ollama local  qwen2.5vl:7b", value="ollama:qwen2.5vl:7b", selected=True),
                                            html.Option("Ollama local  qwen2.5vl:32b", value="ollama:qwen2.5vl:32b"),
                                            html.Option("ChatGPT  GPT-4o", value="openai:gpt-4o"),
                                        ],
                                    ),
                                ]),
                                html.Div(id="chatMessages", className="chat-messages"),
                                html.Div(className="chat-input", children=[
                                    dcc.Input(
                                        id="chatInput",
                                        type="text",
                                        placeholder="Type your message...",
                                        debounce=True,
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
            ]  # Close main-app
        )
    ]
)
