from .VivViewer import VivViewer
from .utils import convert_to_ome_tiff, serve_directory

__version__ = '0.1.0'
__all__ = ['VivViewer', 'convert_to_ome_tiff', 'serve_directory']

# Dash reads _js_dist from the package __init__ module level to know what JS to serve
_js_dist = [
    {
        'relative_package_path': 'dash_viv_viewer.min.js',
        'namespace': 'dash_viv_viewer',
    }
]

_css_dist = []
