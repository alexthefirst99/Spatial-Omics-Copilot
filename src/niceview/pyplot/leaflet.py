from niceview.utils.colors import CMAX, CMIN, get_hex_values
import dash_viv_viewer
from dash import html, dcc
import urllib.parse
import time

def create_viv_viewer(
    map_id,
    base_client,
    base_layer,
    list_of_clients,
    cmax=CMAX,
    classes=None,
    cmap=None,
    geojson_coords=None,
    workspace_id="",
    token=None,
    overlay=False,
    spots=None
):
    """Create viv viewer.
    
    Args:
        map_id (str): Map ID.
        base_client (TileClient): Base client.
        base_layer: Kept for compat.
        list_of_clients (list[tuple]): List of (TileClient, name).
        cmax (int, optional): Max value.
        cmap: (str, optional): Color map.
        geojson_coords: input coordinate 
        workspace_id: workspace id used in app routes
        
    Returns:
        html.Div containing VivViewer component and custom legend
    """
    if geojson_coords is None:
        geojson_coords = []
    if spots is None:
        spots = []
    if token is not None and not workspace_id:
        workspace_id = token

    cluster_counts = {}
    cluster_colors = {}
    for spot in spots:
        cluster = spot.get("cluster") if isinstance(spot, dict) else None
        if cluster is None:
            continue
        cluster = str(cluster)
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        color = spot.get("color")
        if color:
            cluster_colors[cluster] = color

    image_urls = []
    
    # GIS files are already converted to pyramidal OME-TIFF and uploaded to S3
    # during preprocessing, so let VivViewer request S3 directly.
    def to_url(filename):
        if str(filename).startswith("s3://"):
            s3_path = str(filename)[5:]
            bucket, key = s3_path.split("/", 1)
            url = f"https://{bucket}.s3.us-east-2.amazonaws.com/{key}"
            print(f"[viv_url] Direct S3 OME-TIFF URL: {url}", flush=True)
            return url
        encoded_path = urllib.parse.quote(filename, safe='')
        url = f"/workspaces/{workspace_id}/ome_tiff?path={encoded_path}"
        print(f"[viv_url] Proxy OME-TIFF URL: {url}", flush=True)
        return url
    
    # Push base image first
    image_urls.append(to_url(base_client.filename))
    
    # Push any overlays
    # In interface.py, list_of_clients is now e.g. [(cell_type_client, 'cell type')]
    for client, _ in list_of_clients:
        image_urls.append(to_url(client.filename))
        
    COLOR_DICT_CELLS = {
        1: [255, 0, 0],        # Bright red — Neoplastic
        2: [34, 221, 77],      # Bright green — Immune
        3: [35, 92, 236],      # Strong blue — Stromal
        4: [255, 209, 102],    # Soft yellow-orange — Epithelial
        5: [255, 159, 68],     # Warm orange — Fibroblast
        6: [200, 50, 50],      # Medium red — Endothelial
        7: [60, 40, 120],      # Deep indigo — Cardiomyocyte
        8: [35, 192, 236],     # Sky blue — Cardiac Fibroblast
        9: [254, 255, 100],    # Pale yellow — Smooth Muscle
        10: [153, 102, 255],   # Lavender — Adipose
        11: [255, 159, 168],   # Light pink — Oligodendrocyte
        12: [255, 59, 68],     # Bright coral red — Astrocyte
        13: [92, 200, 186],    # Teal — Neuron
        14: [255, 0, 100],     # Magenta — Vascular Smooth Muscle
        15: [34, 221, 177],    # Aqua green — Alveolar pneumocytes
        16: [35, 92, 136],     # Steel blue — Chondrocytes
        17: [254, 55, 0],      # Vivid orange-red — Hepatocyte
        18: [120, 68, 229],    # Violet — Glia
        19: [68, 133, 229],    # Azure — Pericentral hepatocytes
        20: [120, 229, 68],    # Lime green — Proliferating keratinocytes
        21: [0, 180, 229],     # Aqua-blue — Spinous keratinocytes
        22: [120, 0, 68],      # Maroon — Connective
        23: [229, 180, 68],    # Golden tan — Lamina propria
        24: [229, 68, 180],    # Hot pink — Reserved / extra
        25: [68, 229, 120],    # Mint green — Reserved / extra
    }

    TYPE_NUCLEI_DICT_PANNUKE = {
        1: "Neoplastic", 2: "Immune", 3: "Stromal", 4: "Epithelial", 5: "Fibroblast",
        6: "Endothelial", 7: "Cardiomyocyte", 8: "Cardiac Fibroblast", 9: "Smooth Muscle",
        10: "Adipose", 11: "Oligodendrocyte", 12: "Astrocyte", 13: "Neuron",
        14: "Vascular Smooth Muscle", 15: "Alveolar pneumocytes", 16: "Chondrocytes",
        17: "Hepatocyte", 18: "Glia", 19: "Pericentral hepatocytes",
        20: "Proliferating keratinocytes", 21: "Spinous keratinocytes",
        22: "Connective", 23: "Lamina propria",
    }
    
    # Custom HTML Legend Construction
    legend_items = []
    
    if classes is not None:
        iterator = classes.items() if isinstance(classes, dict) else ((idx, TYPE_NUCLEI_DICT_PANNUKE.get(idx)) for idx in classes)
        for idx, name in iterator:
            if name and idx in COLOR_DICT_CELLS:
                rgb = COLOR_DICT_CELLS[idx]
                hex_color = '#%02x%02x%02x' % tuple(rgb)
                
                legend_items.append(
                    html.Div([
                        html.Div(style={
                            'width': '16px', 'height': '16px',
                            'backgroundColor': hex_color,
                            'marginRight': '8px', 'borderRadius': '3px'
                        }),
                        html.Span(name, style={
                            'fontFamily': 'SF Pro Text, sans-serif',
                            'fontSize': '12px', 'color': '#1e1e1e', 'fontWeight': '500'
                        })
                    ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '4px'})
                )
    
    # Spatial cluster legend from uploaded h5ad clustering.
    if cluster_counts:
        legend_items.append(
            html.Div("Spatial clusters", style={
                'fontFamily': 'SF Pro Text, sans-serif',
                'fontSize': '12px',
                'fontWeight': '700',
                'color': '#1d1d1f',
                'marginBottom': '8px'
            })
        )
        fallback_colors = [
            '#0071e3', '#ff9500', '#34c759', '#af52de', '#ff3b30',
            '#00c7be', '#5856d6', '#ffcc00', '#5ac8fa', '#ff2d55',
            '#30d158', '#bf5af2', '#ffd60a', '#64d2ff', '#a2845e',
        ]
        def cluster_sort_key(value):
            try:
                return (0, int(value))
            except ValueError:
                return (1, value)

        for idx, cluster in enumerate(sorted(cluster_counts.keys(), key=cluster_sort_key)):
            color = cluster_colors.get(cluster, fallback_colors[idx % len(fallback_colors)])
            legend_items.append(
                html.Button([
                    html.Div(style={
                        'width': '12px',
                        'height': '12px',
                        'backgroundColor': color,
                        'marginRight': '8px',
                        'borderRadius': '50%',
                        'border': '1px solid rgba(0,0,0,0.18)',
                        'flexShrink': '0',
                    }),
                    html.Span(f"Cluster {cluster}", style={
                        'fontFamily': 'SF Pro Text, sans-serif',
                        'fontSize': '12px',
                        'color': '#1e1e1e',
                        'fontWeight': '600',
                        'marginRight': '8px'
                    }),
                    html.Span(f"{cluster_counts[cluster]:,}", style={
                        'fontFamily': 'SF Pro Text, sans-serif',
                        'fontSize': '11px',
                        'color': '#6e6e73',
                        'marginLeft': 'auto'
                    })
                ],
                id={"type": "cluster-legend-btn", "index": str(cluster)},
                n_clicks=0,
                type="button",
                title=f"Show high-expression genes for Cluster {cluster}",
                style={
                    'display': 'flex',
                    'alignItems': 'center',
                    'minWidth': '150px',
                    'marginBottom': '5px',
                    'width': '100%',
                    'background': 'transparent',
                    'border': 'none',
                    'padding': '3px 4px',
                    'borderRadius': '6px',
                    'cursor': 'pointer',
                    'textAlign': 'left',
                })
            )

    # Continuous Colormap Legend (if no classes, but cmap active)
    elif cmap is not None:
        # Build simple gradient box
        hex_colors = get_hex_values(cmap)
        if hex_colors:
            gradient = f"linear-gradient(to right, {', '.join(hex_colors)})"
            legend_items.append(
                html.Div([
                    html.Div(style={
                        'width': '100px', 'height': '12px',
                        'background': gradient, 'borderRadius': '2px', 'marginBottom': '4px'
                    }),
                    html.Div([
                        html.Span('0', style={'fontSize': '10px', 'color': '#666'}),
                        html.Span(str(cmax), style={'fontSize': '10px', 'color': '#666'})
                    ], style={'display': 'flex', 'justifyContent': 'space-between', 'width': '100px'})
                ])
            )

    legend_div = html.Div(legend_items, style={
        'position': 'absolute', 'bottom': '20px', 'left': '20px', 'zIndex': 1000,
        'backgroundColor': 'rgba(255, 255, 255, 0.92)', 'padding': '10px 12px',
        'borderRadius': '8px', 'boxShadow': '0 8px 24px rgba(0,0,0,0.14)',
        'border': '1px solid rgba(0,0,0,0.08)',
        'maxHeight': '300px', 'overflowY': 'auto',
        'display': 'none' if not legend_items else 'block'
    })

    # Convert geojson_coords to ROIs format expected by VivViewer (or just an empty list so it doesn't break)
    # The expected ROIs structure might differ, VivViewer expects standard arrays [minX, minY, maxX, maxY]
    # for rectangle or polygon points.
    # VivViewer.react.js handles `rois` as an array of features or objects.
    # We will just pass an empty list for initialization since typically `geojson_coords` are empty on initial load.
    
    num_classes = len(classes) if classes else 0
    required_height = num_classes * 35 + 100
    base_height = max(940, required_height)
    print("DEBUG: image_urls: ", image_urls)
    has_overlay = len(image_urls) >= 2

    # Return parent Div with Viewer and Legend overlapping
    return html.Div([
        dash_viv_viewer.VivViewer(
            id=map_id,
            image_url=image_urls,
            height=base_height,
            bg_color="white",
            active_layer=1 if has_overlay else 0,
            opacity={0: 1.0, 1: 0.5},
            rois=[],
            spots=spots,
            selected_spot=None,
            selected_cluster=None
        ),
        legend_div,
    ], style={'position': 'relative', 'width': '100%', 'height': f'{base_height}px'})
