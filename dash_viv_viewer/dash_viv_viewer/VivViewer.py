import dash
from dash.development.base_component import Component, _explicitize_args


class VivViewer(Component):
    """A VivViewer component.

    A Viv-powered WebGL image viewer with an ROI drawing toolbar.

    Usage::

        VivViewer(
            id="viewer",
            image_url="http://localhost:5001/images/sample.ome.tif",
            height=600,
        )

    Keyword arguments:

    - id (string; optional):
        The ID used to identify this component in Dash callbacks.

    - image_url (string; optional):
        URL to the OME-TIFF image to display.

    - height (number; default 600):
        Height of the viewer in pixels.

    - width (number; optional):
        Width of the viewer in pixels. If omitted, fills the container.

    - bg_color (string; default '#111'):
        Background color of the viewer container.

    - active_layer (number; default 0):
        The index of the layer currently selected as "active" (shown on top).

    - opacity (dict; optional):
        Dictionary mapping layer indices to their CSS opacity (0–1).
        Updated whenever the user moves the opacity slider.

    - rois (list; default []):
        List of drawn ROIs. Each item has ``{type, points}`` where
        ``points`` is a list of ``[x, y]`` image-pixel coordinates.
        Updated whenever the user draws or clears ROIs.

    - spots (list; default []):
        Spatial transcriptomics spot overlay items. Each item has ``{id, x, y, r}``.

    - selected_spot (dict; optional):
        The spot most recently selected in the viewer."""

    _children_props = []
    _base_nodes = ['children']
    _namespace = 'dash_viv_viewer'
    _type = 'VivViewer'

    @_explicitize_args
    def __init__(
        self,
        id=Component.UNDEFINED,
        image_url=Component.UNDEFINED,
        height=Component.UNDEFINED,
        width=Component.UNDEFINED,
        bg_color=Component.UNDEFINED,
        active_layer=Component.UNDEFINED,
        opacity=Component.UNDEFINED,
        rois=Component.UNDEFINED,
        spots=Component.UNDEFINED,
        selected_spot=Component.UNDEFINED,
        **kwargs
    ):
        self._prop_names = ['id', 'image_url', 'height', 'width', 'bg_color', 'active_layer', 'opacity', 'rois', 'spots', 'selected_spot']
        self._valid_wildcard_attributes = []
        self.available_properties = ['id', 'image_url', 'height', 'width', 'bg_color', 'active_layer', 'opacity', 'rois', 'spots', 'selected_spot']
        self.available_wildcard_properties = []
        _explicit_args = kwargs.pop('_explicit_args')
        _locals = locals()
        _locals.update(kwargs)
        args = {k: _locals[k] for k in _explicit_args}
        super(VivViewer, self).__init__(**args)



