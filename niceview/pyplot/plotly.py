"""Tile server."""

import plotly.graph_objects as go

CMAX = 255
CMIN = 0


def scatter_overlay_gis(
    map_client, map_tile_layer,
    lons, lats,
    marker_size, marker_color, marker_opacity, marker_color_scale,
    window_width, window_height, title=None,
):
    """Scatter overlay on GIS map.
    
    Args:
        map_client: TileClient
        map_tile_layer: TileLayer
        lons: list[float]
        lats: list[float]
        marker_size: list[float]
        marker_color: list[float]
        marker_opacity: float
        marker_color_scale: str
        window_width: int
        window_height: int
        title: str
    
    Returns:
        Figure
    """
    fig = go.Figure(
        data=(
            go.Scattermapbox(
                lon=lons,
                lat=lats,
                mode='markers',
                marker=go.scattermapbox.Marker(
                    size=marker_size,
                    color=marker_color,
                    opacity=marker_opacity,
                    colorscale=marker_color_scale,
                    colorbar={
                        'thickness': 10,
                        'ticklen': 3,
                        'title': {
                            'text': 'Gene Expression',
                            'side': 'right',
                        },
                        'titleside': 'right',
                    },
                    cmin=CMIN,
                    cmax=CMAX,
                ),
                text='center',
                hoverinfo='text',
            ),
        ),
        layout=go.Layout(
            mapbox={
                'style': 'white-bg',
                'center': {
                    'lon': map_client.center()[1],
                    'lat': map_client.center()[0],
                },
                'zoom': map_client.default_zoom,
                'pitch': 0,
                'layers': [
                    {
                        'below': 'traces',
                        'sourcetype': 'raster',
                        'source': [map_tile_layer.url],
                    },
                ],
            },
            margin={
                'l': 30,
                'r': 30,
                'b': 30,
                't': 30,
            },
            autosize=False,
            width=window_width,
            height=window_height,
            paper_bgcolor='LightSteelBlue',
        ),
    )
    return fig
