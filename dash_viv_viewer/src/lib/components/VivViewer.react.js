import {
    AdditiveColormapExtension,
    ColorPaletteExtension,
    LensExtension,
    PictureInPictureViewer,
    SideBySideView,
    VivViewer as CoreVivViewer,
    getDefaultInitialViewState,
    loadOmeTiff
} from '@hms-dbmi/viv';
import { DetailView } from '@vivjs/views';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// A subclass of Viv's view that enforces EXACT camera state synchronization bounds
// to prevent floating-point drift and lag when panning very quickly.
class SyncedSideBySideView extends SideBySideView {
    filterViewState({ viewState }) {
        return {
            id: this.id,
            target: viewState.target,
            zoom: viewState.zoom,
            height: this.height,
            width: this.width
        };
    }
}

/* ─── Toolbar button style ───────────────────────────────────────── */
const btnStyle = (active, disabled, last = false) => ({
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: 30, height: 30,
    background: active ? '#cce8ff' : 'white',
    border: 'none',
    borderBottom: last ? 'none' : '1px solid #bbb',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.4 : 1,
    fontSize: 15,
    userSelect: 'none',
    transition: 'background 0.12s',
});

/* ─── Helpers ────────────────────────────────────────────────────── */
function guessRgb({ Pixels }) {
    const numChannels = Pixels.Channel?.length ?? 1;
    const SamplesPerPixel = Pixels.Channel?.[0]?.SamplesPerPixel ?? 1;
    const is3Or4Channel8Bit = (numChannels === 3 || numChannels === 4) && Pixels.Type === 'uint8';
    const interleavedRgb = (Pixels.SizeC === 3 || Pixels.SizeC === 4 || SamplesPerPixel === 4) && numChannels === 1 && Pixels.Interleaved;
    return SamplesPerPixel === 3 || SamplesPerPixel === 4 || is3Or4Channel8Bit || interleavedRgb;
}

function isInterleaved(shape) {
    const lastDimSize = shape[shape.length - 1];
    return lastDimSize === 3 || lastDimSize === 4;
}

const SPOT_CLUSTER_COLORS = [
    '#0071e3', '#ff9500', '#34c759', '#af52de', '#ff3b30',
    '#00c7be', '#5856d6', '#ffcc00', '#5ac8fa', '#ff2d55',
    '#30d158', '#bf5af2', '#ffd60a', '#64d2ff', '#a2845e',
];
const SPOT_RENDER_LIMIT = 200000;
const SPOT_GRID_MIN_SIZE = 96;
const SPOT_RASTER_MAX_SIZE = 4096;

function colorWithAlpha(color, alpha) {
    if (typeof color !== 'string') return `rgba(0,113,227,${alpha})`;
    if (!color.startsWith('#')) return color;
    const hex = color.slice(1);
    const full = hex.length === 3 ? hex.split('').map(ch => ch + ch).join('') : hex;
    const n = Number.parseInt(full, 16);
    if (!Number.isFinite(n)) return `rgba(0,113,227,${alpha})`;
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r},${g},${b},${alpha})`;
}

function spotClusterColor(spot) {
    if (spot?.color) return spot.color;
    if (spot?.cluster === undefined || spot?.cluster === null) return '#0071e3';
    const text = String(spot.cluster);
    let hash = 0;
    for (let i = 0; i < text.length; i++) hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    return SPOT_CLUSTER_COLORS[Math.abs(hash) % SPOT_CLUSTER_COLORS.length];
}

const VivViewer = ({ id, image_url, height = 600, width, bg_color = '#111', active_layer = 0, opacity, rois = [], spots = [], selected_spot, selected_cluster, setProps }) => {
    const containerRef = useRef(null);
    const svgRef = useRef(null);
    const spotCanvasRef = useRef(null);
    const dragStartRef = useRef(null);
    const polyDownPos = useRef(null);
    const overlayDivRef = useRef(null);

    /* Viewer & loader state */
    const [loaders, setLoaders] = useState([]);
    const [internalActiveLayer, setInternalActiveLayer] = useState(active_layer);
    const [viewMode, setViewMode] = useState('single'); // 'single' | 'side-by-side'

    // Sync Dash active_layer -> local
    useEffect(() => {
        if (active_layer !== undefined) setInternalActiveLayer(active_layer);
    }, [active_layer]);

    const [internalOpacity, setInternalOpacity] = useState(() => {
        if (opacity === undefined || opacity === null) return 0.5;
        if (typeof opacity === 'object') return opacity[internalActiveLayer] !== undefined ? opacity[internalActiveLayer] : 0.5;
        return opacity; // Support numeric opacity if passed
    });
    const [clusterOpacity, setClusterOpacity] = useState(0.5);
    // Sync Dash->local when the prop changes externally
    useEffect(() => {
        if (opacity === undefined || opacity === null) return;
        if (typeof opacity === 'object') {
            if (opacity[internalActiveLayer] !== undefined) setInternalOpacity(opacity[internalActiveLayer]);
        } else {
            setInternalOpacity(opacity);
        }
    }, [opacity, internalActiveLayer]);

    const [colors, setColors] = useState([[255, 0, 0]]);
    const [contrastLimits, setContrastLimits] = useState([[0, 65535]]);
    const [channelsVisible, setChannelsVisible] = useState([true]);
    const [selections, setSelections] = useState([{ z: 0, c: 0, t: 0 }]);
    const [viewState, setViewState] = useState(null);
    const [initialViewState, setInitialViewState] = useState(null);
    const [sharedViewState, setSharedViewState] = useState(null);

    const VIV_EXTENSIONS = useMemo(() => [new ColorPaletteExtension()], []);

    // Initial view state once loader and container are ready
    useEffect(() => {
        if (loaders.length > 0 && containerSize.width > 0 && !initialViewState) {
            try {
                // Determine layout bounds. Multi-image Side-by-Side uses half width.
                const vsWidth = (viewMode === 'side-by-side' && loaders.length >= 2) ? containerSize.width / 2 : containerSize.width;
                const vs = getDefaultInitialViewState(loaders[0], { width: vsWidth, height: containerSize.height });
                setInitialViewState(vs);
                setViewState(vs);
            } catch (err) {
                console.warn('[VivViewer] Could not compute initial view state', err);
            }
        }
    }, [loaders, containerSize, viewMode]);
    const [containerSize, setContainerSize] = useState({ width: width || 800, height });

    /* Drawing state */
    const [drawMode, setDrawMode] = useState(null); // null | 'rect' | 'polygon'
    const [spotSelectMode, setSpotSelectMode] = useState(false);
    const [selectedSpotId, setSelectedSpotId] = useState(selected_spot?.id || null);
    const [hoverSpot, setHoverSpot] = useState(null);
    const [roiList, setRoiList] = useState(rois || []);
    const [rectDraft, setRectDraft] = useState(null);
    const [polyDraft, setPolyDraft] = useState([]);
    const [cursor, setCursor] = useState(null);

    /* Sync roiList → Dash prop */
    useEffect(() => {
        if (setProps) setProps({ rois: roiList });
    }, [roiList, setProps]);

    /* Track container size */
    useEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        const obs = new ResizeObserver(entries => {
            const { width: w, height: h } = entries[0].contentRect;
            const nw = Math.round(w || width || 800);
            const nh = Math.round(h || height);
            // Only update if size actually changed by at least 1px — prevents resize loop
            setContainerSize(prev => (prev.width === nw && prev.height === nh) ? prev : { width: nw, height: nh });
        });
        obs.observe(el);
        return () => obs.disconnect();
    }, [width, height]);

    /* Load images */
    useEffect(() => {
        if (!image_url) return;
        setLoaders([]); // Reset pending load

        let urls = [];
        if (Array.isArray(image_url)) urls = image_url;
        else if (typeof image_url === 'string') urls = [image_url];

        if (urls.length === 0) return;

        // Ensure URLs are absolute for loadOmeTiff
        urls = urls.map(u => {
            try {
                return new URL(u, window.location.origin).href;
            } catch (e) {
                return u;
            }
        });

        Promise.all(urls.map(u => loadOmeTiff(u, { images: 'all', pool: false })))
            .then(sourcesArr => {
                // loaders array will be populated with the `data` chunk of each source
                const loadedDatas = sourcesArr.map(src => Array.isArray(src) ? src[0] : src);
                setLoaders(loadedDatas.map(x => x.data));

                // Process metadata from the FIRST image to set colors/contrast generically
                const { data, metadata } = loadedDatas[0];
                const pixels = metadata?.Pixels ?? {};
                const dtype = (pixels.Type ?? 'uint8').toLowerCase();
                const maxVal = dtype.includes('16') ? 65535 : dtype.includes('float') ? 1 : 255;

                const isRgb = guessRgb(metadata);
                const isInter = isInterleaved(data[0].shape);
                console.log('[VivViewer] Loader metadata:', metadata);

                if (isRgb) {
                    if (isInter) {
                        setColors([[255, 255, 255]]);
                        setContrastLimits([[0, maxVal]]);
                        setChannelsVisible([true]);
                        setSelections([{ z: 0, c: 0, t: 0 }]);
                    } else {
                        // Planar RGB
                        setColors([[255, 0, 0], [0, 255, 0], [0, 0, 255]]);
                        setContrastLimits([[0, maxVal], [0, maxVal], [0, maxVal]]);
                        setChannelsVisible([true, true, true]);
                        setSelections([{ z: 0, c: 0, t: 0 }, { z: 0, c: 1, t: 0 }, { z: 0, c: 2, t: 0 }]);
                    }
                } else {
                    // Default to first channel
                    setColors([[255, 255, 255]]);
                    setContrastLimits([[0, maxVal]]);
                    setChannelsVisible([true]);
                    setSelections([{ z: 0, c: 0, t: 0 }]);
                }
            })
            .catch(err => console.error('[VivViewer] Failed to load images:', err));
    }, [image_url]);

    /* Coordinate conversion */
    const screenToImage = useCallback((sx, sy) => {
        const vs = viewState;
        if (!vs) return null;

        const s = Math.pow(2, vs.zoom);
        let adjX = sx;
        const isSBS = viewMode === 'side-by-side' && loaders.length >= 2;
        const vsWidth = isSBS ? containerSize.width / 2 : containerSize.width;

        // If side-by-side, determine which half the click happened on
        if (isSBS && sx >= vsWidth) {
            adjX = sx - vsWidth;
        }

        return [
            Math.round(vs.target[0] + (adjX - vsWidth / 2) / s),
            Math.round(vs.target[1] + (sy - containerSize.height / 2) / s)
        ];
    }, [containerSize, viewState, viewMode, loaders]);

    const imageToScreen = useCallback((ix, iy, paneIndex = 0) => {
        const vs = viewState;
        if (!vs) return null;

        const s = Math.pow(2, vs.zoom);
        const isSBS = viewMode === 'side-by-side' && loaders.length >= 2;
        const vsWidth = isSBS ? containerSize.width / 2 : containerSize.width;
        const xOffset = (isSBS && paneIndex === 1) ? vsWidth : 0;

        return [
            (ix - vs.target[0]) * s + vsWidth / 2 + xOffset,
            (iy - vs.target[1]) * s + containerSize.height / 2
        ];
    }, [containerSize, viewState, viewMode, loaders]);

    const onViewStateChange = useCallback(({ viewState: vs }) => {
        setViewState(vs);
    }, []);

    const handleBaseViewStateChange = useCallback(({ viewId, viewState: vs, interactionState, oldViewState }) => {
        setSharedViewState(vs);
        setViewState(vs); // Also keep our coordinate math state updated
    }, []);

    /* ── Rect handlers ── */
    const onRectDown = useCallback((e) => {
        if (drawMode !== 'rect') return;
        e.preventDefault();
        const r = svgRef.current.getBoundingClientRect();
        dragStartRef.current = { x: e.clientX - r.left, y: e.clientY - r.top };
        setRectDraft({ x: e.clientX - r.left, y: e.clientY - r.top, width: 0, height: 0 });
    }, [drawMode]);

    const onRectMove = useCallback((e) => {
        if (drawMode !== 'rect' || !dragStartRef.current) return;
        e.preventDefault();
        const r = svgRef.current.getBoundingClientRect();
        const x = e.clientX - r.left, y = e.clientY - r.top;
        const { x: sx, y: sy } = dragStartRef.current;
        setRectDraft({ x: Math.min(x, sx), y: Math.min(y, sy), width: Math.abs(x - sx), height: Math.abs(y - sy) });
    }, [drawMode]);

    const onRectUp = useCallback((e) => {
        if (drawMode !== 'rect' || !dragStartRef.current) return;
        e.preventDefault();
        const draft = rectDraft;
        dragStartRef.current = null;
        setRectDraft(null);
        if (draft && draft.width > 4 && draft.height > 4) {
            const tl = screenToImage(draft.x, draft.y);
            const br = screenToImage(draft.x + draft.width, draft.y + draft.height);
            if (tl && br) {
                const newRoi = { id: Date.now(), type: 'rect', points: [tl, [br[0], tl[1]], br, [tl[0], br[1]]] };
                setRoiList(prev => [...prev, newRoi]);
                setDrawMode(null);
            }
        }
    }, [drawMode, rectDraft, screenToImage]);

    /* ── Polygon handlers ── */
    const onPolyDown = useCallback((e) => {
        if (drawMode !== 'polygon') return;
        const r = svgRef.current.getBoundingClientRect();
        polyDownPos.current = { x: e.clientX - r.left, y: e.clientY - r.top };
    }, [drawMode]);

    const onPolyUp = useCallback((e) => {
        if (drawMode !== 'polygon' || !polyDownPos.current) return;
        const r = svgRef.current.getBoundingClientRect();
        const x = e.clientX - r.left, y = e.clientY - r.top;
        const dx = x - polyDownPos.current.x, dy = y - polyDownPos.current.y;
        polyDownPos.current = null;
        if (Math.sqrt(dx * dx + dy * dy) < 6) {
            setPolyDraft(prev => [...prev, { x, y }]);
        }
    }, [drawMode]);

    const onPolyMove = useCallback((e) => {
        if (drawMode !== 'polygon') return;
        const r = svgRef.current.getBoundingClientRect();
        setCursor({ x: e.clientX - r.left, y: e.clientY - r.top });
    }, [drawMode]);

    const finishPolygon = useCallback(() => {
        if (polyDraft.length < 3) return;
        const pts = polyDraft.map(p => screenToImage(p.x, p.y)).filter(Boolean);
        setRoiList(prev => [...prev, { id: Date.now(), type: 'polygon', points: pts }]);
        setPolyDraft([]); setCursor(null); setDrawMode(null);
    }, [polyDraft, screenToImage]);

    const cancelPolygon = useCallback(() => { setPolyDraft([]); setCursor(null); }, []);

    /* Forward wheel through SVG overlay */
    const onWheel = useCallback((e) => {
        if (!svgRef.current) return;
        svgRef.current.style.pointerEvents = 'none';
        const el = document.elementFromPoint(e.clientX, e.clientY);
        svgRef.current.style.pointerEvents = 'auto';
        el?.dispatchEvent(new WheelEvent('wheel', {
            bubbles: true, cancelable: true, view: window,
            deltaX: e.deltaX, deltaY: e.deltaY, deltaZ: e.deltaZ, deltaMode: e.deltaMode,
            clientX: e.clientX, clientY: e.clientY, ctrlKey: e.ctrlKey, shiftKey: e.shiftKey,
        }));
    }, []);

    const spotList = Array.isArray(spots) ? spots : [];
    const hasSpots = spotList.length > 0;
    const selectedClusterId = selected_cluster === undefined || selected_cluster === null || selected_cluster === ''
        ? null
        : String(selected_cluster);

    const spotGrid = useMemo(() => {
        if (!hasSpots || !spotSelectMode) return null;
        let maxRadius = 0;
        for (const spot of spotList) {
            const radius = Number(spot.r || 4);
            if (Number.isFinite(radius)) maxRadius = Math.max(maxRadius, radius);
        }
        const cellSize = Math.max(SPOT_GRID_MIN_SIZE, maxRadius * 4);
        const cells = new Map();
        for (let i = 0; i < spotList.length; i++) {
            const spot = spotList[i];
            const x = Number(spot.x);
            const y = Number(spot.y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
            const gx = Math.floor(x / cellSize);
            const gy = Math.floor(y / cellSize);
            const key = `${gx}:${gy}`;
            const bucket = cells.get(key);
            if (bucket) bucket.push(i);
            else cells.set(key, [i]);
        }
        return { cells, cellSize, maxRadius };
    }, [hasSpots, spotList, spotSelectMode]);

    const spotRaster = useMemo(() => {
        if (!hasSpots) return null;
        let minX = Infinity;
        let minY = Infinity;
        let maxX = -Infinity;
        let maxY = -Infinity;
        let maxRadius = 0;
        for (const spot of spotList) {
            const x = Number(spot.x);
            const y = Number(spot.y);
            const radius = Number(spot.r || 4);
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x);
            maxY = Math.max(maxY, y);
            if (Number.isFinite(radius)) maxRadius = Math.max(maxRadius, radius);
        }
        if (!Number.isFinite(minX) || !Number.isFinite(minY) || maxX <= minX || maxY <= minY) return null;

        const pad = Math.max(8, maxRadius * 2);
        minX -= pad;
        minY -= pad;
        maxX += pad;
        maxY += pad;
        const imageWidth = maxX - minX;
        const imageHeight = maxY - minY;
        const scale = Math.min(1, SPOT_RASTER_MAX_SIZE / Math.max(imageWidth, imageHeight));
        const rasterWidth = Math.max(1, Math.ceil(imageWidth * scale));
        const rasterHeight = Math.max(1, Math.ceil(imageHeight * scale));
        const raster = document.createElement('canvas');
        raster.width = rasterWidth;
        raster.height = rasterHeight;
        const rasterCtx = raster.getContext('2d');
        rasterCtx.clearRect(0, 0, rasterWidth, rasterHeight);
        for (const spot of spotList) {
            const x = Number(spot.x);
            const y = Number(spot.y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
            const color = spotClusterColor(spot);
            const r = Math.max(1, Number(spot.r || 4) * scale);
            rasterCtx.fillStyle = colorWithAlpha(color, 1);
            rasterCtx.beginPath();
            rasterCtx.arc((x - minX) * scale, (y - minY) * scale, r, 0, Math.PI * 2);
            rasterCtx.fill();
        }
        return { canvas: raster, minX, minY, maxX, maxY, scale, rasterWidth, rasterHeight };
    }, [hasSpots, spotList]);

    const selectedClusterRaster = useMemo(() => {
        if (!selectedClusterId || !spotRaster) return null;
        const raster = document.createElement('canvas');
        raster.width = spotRaster.rasterWidth;
        raster.height = spotRaster.rasterHeight;
        const rasterCtx = raster.getContext('2d');
        rasterCtx.clearRect(0, 0, raster.width, raster.height);
        let drewAny = false;

        for (const spot of spotList) {
            if (String(spot.cluster) !== selectedClusterId) continue;
            const x = Number(spot.x);
            const y = Number(spot.y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;

            const color = spotClusterColor(spot);
            const baseRadius = Math.max(1, Number(spot.r || 4) * spotRaster.scale);
            const cx = (x - spotRaster.minX) * spotRaster.scale;
            const cy = (y - spotRaster.minY) * spotRaster.scale;

            rasterCtx.fillStyle = 'rgba(255,255,255,0.9)';
            rasterCtx.beginPath();
            rasterCtx.arc(cx, cy, baseRadius * 1.42, 0, Math.PI * 2);
            rasterCtx.fill();

            rasterCtx.fillStyle = colorWithAlpha(color, 1);
            rasterCtx.beginPath();
            rasterCtx.arc(cx, cy, baseRadius * 1.16, 0, Math.PI * 2);
            rasterCtx.fill();
            drewAny = true;
        }

        if (!drewAny) return null;
        return {
            canvas: raster,
            minX: spotRaster.minX,
            minY: spotRaster.minY,
            maxX: spotRaster.maxX,
            maxY: spotRaster.maxY,
        };
    }, [selectedClusterId, spotList, spotRaster]);

    const drawSpotMarker = useCallback((ctx, spot, fill, stroke, scale = 1.2) => {
        const screen = imageToScreen(Number(spot.x), Number(spot.y), 0);
        if (!screen || !viewState) return;
        const zoomScale = Math.pow(2, viewState.zoom);
        const r = Math.max(2, Number(spot.r || 4) * zoomScale * scale);
        const [cx, cy] = screen;
        ctx.fillStyle = fill;
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
    }, [imageToScreen, viewState]);

    const drawSpotCanvas = useCallback(() => {
        const canvas = spotCanvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const w = Math.max(1, Math.round(containerSize.width));
        const h = Math.max(1, Math.round(containerSize.height));
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
            canvas.style.width = `${w}px`;
            canvas.style.height = `${h}px`;
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        if (!viewState || !hasSpots) return;

        const drawRaster = (raster, alpha) => {
            const topLeft = imageToScreen(raster.minX, raster.minY, 0);
            const bottomRight = imageToScreen(raster.maxX, raster.maxY, 0);
            if (topLeft && bottomRight) {
                const dx = topLeft[0];
                const dy = topLeft[1];
                const dw = bottomRight[0] - topLeft[0];
                const dh = bottomRight[1] - topLeft[1];
                if (dw > 1 && dh > 1 && dx < w && dy < h && dx + dw > 0 && dy + dh > 0) {
                    ctx.imageSmoothingEnabled = true;
                    ctx.save();
                    ctx.globalAlpha = alpha;
                    ctx.drawImage(raster.canvas, dx, dy, dw, dh);
                    ctx.restore();
                }
            }
        };

        if (spotRaster) {
            drawRaster(spotRaster, selectedClusterId ? clusterOpacity * 0.2 : clusterOpacity);
        }
        if (selectedClusterRaster) {
            drawRaster(selectedClusterRaster, clusterOpacity);
        }

        if (!spotSelectMode) return;
        if (selectedSpotId) {
            const selected = spotList.find(s => String(s.id) === String(selectedSpotId));
            if (selected) drawSpotMarker(ctx, selected, 'rgba(255,149,0,0.65)', '#ff9500', 1.5);
        }
        if (hoverSpot) {
            const color = spotClusterColor(hoverSpot);
            drawSpotMarker(ctx, hoverSpot, colorWithAlpha(color, 0.64), color, 1.35);
        }
    }, [clusterOpacity, containerSize, drawSpotMarker, hasSpots, hoverSpot, imageToScreen, selectedClusterId, selectedClusterRaster, selectedSpotId, spotList, spotRaster, spotSelectMode, viewState]);

    useEffect(() => { drawSpotCanvas(); }, [drawSpotCanvas]);

    const findNearestSpot = useCallback((clientX, clientY) => {
        const canvas = spotCanvasRef.current;
        if (!canvas || !hasSpots || !spotGrid || !viewState) return null;
        const bounds = canvas.getBoundingClientRect();
        const clickX = clientX - bounds.left;
        const clickY = clientY - bounds.top;
        const imagePoint = screenToImage(clickX, clickY);
        if (!imagePoint) return null;

        const zoomScale = Math.pow(2, viewState.zoom);
        const searchImageRadius = spotGrid.maxRadius + 24 / Math.max(zoomScale, 0.0001);
        const range = Math.max(1, Math.ceil(searchImageRadius / spotGrid.cellSize));
        const centerGx = Math.floor(imagePoint[0] / spotGrid.cellSize);
        const centerGy = Math.floor(imagePoint[1] / spotGrid.cellSize);

        let best = null;
        let bestDist = Infinity;
        for (let gx = centerGx - range; gx <= centerGx + range; gx++) {
            for (let gy = centerGy - range; gy <= centerGy + range; gy++) {
                const bucket = spotGrid.cells.get(`${gx}:${gy}`);
                if (!bucket) continue;
                for (const i of bucket) {
                    const spot = spotList[i];
                    const screen = imageToScreen(Number(spot.x), Number(spot.y), 0);
                    if (!screen) continue;
                    const dx = screen[0] - clickX;
                    const dy = screen[1] - clickY;
                    const dist = dx * dx + dy * dy;
                    if (dist < bestDist) {
                        bestDist = dist;
                        best = { ...spot, index: i, screenX: screen[0], screenY: screen[1] };
                    }
                }
            }
        }
        if (!best) return null;
        const visibleRadius = Math.max(5, Number(best.r || 4) * zoomScale + 5);
        return bestDist <= visibleRadius * visibleRadius ? best : null;
    }, [hasSpots, imageToScreen, screenToImage, spotGrid, spotList, viewState]);

    const onSpotClick = useCallback((e) => {
        if (!spotSelectMode || !hasSpots) return;
        const best = findNearestSpot(e.clientX, e.clientY);
        if (best) {
            if (String(best.id) === String(selectedSpotId)) {
                setSelectedSpotId(null);
                if (setProps) setProps({ selected_spot: null });
            } else {
                setSelectedSpotId(best.id);
                if (setProps) setProps({ selected_spot: best });
            }
        } else {
            setSelectedSpotId(null);
            if (setProps) setProps({ selected_spot: null });
        }
    }, [findNearestSpot, hasSpots, selectedSpotId, setProps, spotSelectMode]);

    const onSpotMove = useCallback((e) => {
        if (!spotSelectMode || !hasSpots || isDrawing) return;
        const best = findNearestSpot(e.clientX, e.clientY);
        setHoverSpot(best);
    }, [findNearestSpot, hasSpots, isDrawing, spotSelectMode]);

    const onMouseDown = drawMode === 'rect' ? onRectDown : onPolyDown;
    const onMouseMove = useCallback((e) => { onRectMove(e); onPolyMove(e); }, [onRectMove, onPolyMove]);
    const onMouseUp = drawMode === 'rect' ? onRectUp : onPolyUp;

    const isDrawing = drawMode !== null;

    const overlayViewerConfig = useMemo(() => {
        if (viewMode !== 'single' || loaders.length < 2) return null;
        
        const loader = loaders[internalActiveLayer];
        if (!loader) return null;

        // Determine data type and bands for the overlay loader
        const isRgb = loader.dtype === 'Uint8' && (loader.shape[loader.shape.length - 1] === 3 || loader.shape[loader.shape.length - 1] === 4);
        const is16 = loader.dtype === 'Uint16';
        const maxVal = is16 ? 65535 : 255;

        const overlayColors = isRgb ? [[255, 0, 0], [0, 255, 0], [0, 0, 255]] : [[255, 255, 255]];
        const overlayLimits = isRgb ? [[0, maxVal], [0, maxVal], [0, maxVal]] : [[0, maxVal]];
        const overlayVisible = isRgb ? [true, true, true] : [true];
        const overlaySels = isRgb 
            ? [{ z: 0, c: 0, t: 0 }, { z: 0, c: 1, t: 0 }, { z: 0, c: 2, t: 0 }]
            : [{ z: 0, c: 0, t: 0 }];

        return {
            views: [new DetailView({ id: 'cssoverlay', height: containerSize.height, width: containerSize.width })],
            layerProps: [{ 
                loader: loader, 
                contrastLimits: overlayLimits, 
                colors: overlayColors, 
                channelsVisible: overlayVisible, 
                selections: overlaySels, 
                extensions: VIV_EXTENSIONS 
            }]
        };
    }, [viewMode, loaders, internalActiveLayer, containerSize.height, containerSize.width, VIV_EXTENSIONS]);

    const viewerConfig = useMemo(() => {
        if (!initialViewState || loaders.length === 0) return null;

        if (viewMode === 'side-by-side' && loaders.length >= 2) {
            const detailViewLeft = new SyncedSideBySideView({
                id: 'left',
                height: containerSize.height,
                width: containerSize.width / 2,
            });
            const detailViewRight = new SyncedSideBySideView({
                id: 'right',
                x: containerSize.width / 2,
                height: containerSize.height,
                width: containerSize.width / 2,
            });

            return {
                views: [detailViewRight, detailViewLeft],
                layerProps: [
                    { loader: loaders[1], contrastLimits, colors, channelsVisible, selections, extensions: VIV_EXTENSIONS },
                    { loader: loaders[0], contrastLimits, colors, channelsVisible, selections, extensions: VIV_EXTENSIONS }
                ],
                viewStates: [{ ...initialViewState, id: 'left' }, { ...initialViewState, id: 'right' }]
            };
        }

        if (viewMode === 'single') {
            // Base layer is always index 0 to allow overlays to be stacked on top
            const currentLoader = loaders[0];
            return {
                views: [new DetailView({ id: 'single', height: containerSize.height, width: containerSize.width })],
                layerProps: [{ loader: currentLoader, contrastLimits, colors, channelsVisible, selections, extensions: VIV_EXTENSIONS }],
                viewStates: [{ ...initialViewState, id: 'single' }]
            };
        }

        return null;
    }, [
        loaders, internalActiveLayer, viewMode, contrastLimits, colors, channelsVisible, selections,
        containerSize.height, containerSize.width, initialViewState, VIV_EXTENSIONS
    ]);

    console.log('[VivViewer] rendering, containerSize:', containerSize);

    return (
        <div id={id} ref={containerRef} onMouseMove={onSpotMove} onMouseLeave={() => setHoverSpot(null)} style={{ position: 'relative', width: width || '100%', height, background: bg_color, overflow: 'hidden', touchAction: 'none', overscrollBehavior: 'none' }}>
            {!loaders.length ? (
                <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#aaa', fontFamily: 'monospace' }}>
                    {image_url ? 'Loading images…' : 'No image_url provided'}
                </div>
            ) : (
                <>
                    {/* Base viewer — Image 1 */}
                    {viewerConfig && (
                        <CoreVivViewer
                            layerProps={viewerConfig.layerProps}
                            views={viewerConfig.views}
                            randomize
                            onViewStateChange={handleBaseViewStateChange}
                            viewStates={viewerConfig.viewStates}
                        />
                    )}

                    {/* CSS opacity overlay — Image 2 */}
                    {overlayViewerConfig && (
                        <div
                            ref={overlayDivRef}
                            style={{
                                position: 'absolute', inset: 0,
                                opacity: internalOpacity,
                                pointerEvents: 'none'
                            }}
                        >
                            <CoreVivViewer
                                layerProps={overlayViewerConfig.layerProps}
                                views={overlayViewerConfig.views}
                                randomize={false}
                                viewStates={[{ ...(sharedViewState || initialViewState), id: 'cssoverlay' }]}
                            />
                        </div>
                    )}


                    {hasSpots && (
                        <canvas
                            ref={spotCanvasRef}
                            onClick={onSpotClick}
                            title={`${spotList.length} spatial spots`}
                            style={{
                                position: 'absolute', inset: 0, zIndex: 350,
                                pointerEvents: spotSelectMode ? 'auto' : 'none',
                                cursor: spotSelectMode ? 'pointer' : 'default'
                            }}
                        />
                    )}
                    {hoverSpot && (
                        <div style={{
                            position: 'absolute', zIndex: 700,
                            left: Math.min(containerSize.width - 220, Math.max(10, hoverSpot.screenX + 12)),
                            top: Math.min(containerSize.height - 74, Math.max(10, hoverSpot.screenY + 12)),
                            pointerEvents: 'none',
                            background: 'rgba(255,255,255,0.94)',
                            border: '1px solid rgba(0,0,0,0.12)',
                            borderRadius: 8,
                            boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
                            padding: '8px 10px',
                            fontFamily: 'system-ui, -apple-system, sans-serif',
                            fontSize: 12,
                            color: '#1d1d1f',
                            maxWidth: 220
                        }}>
                            <div style={{ fontWeight: 700, marginBottom: 3 }}>Spot {hoverSpot.id}</div>
                            {hoverSpot.cluster !== undefined && hoverSpot.cluster !== null && (
                                <div>Cluster {hoverSpot.cluster}</div>
                            )}
                            <div>x {Number(hoverSpot.x).toFixed(1)}, y {Number(hoverSpot.y).toFixed(1)}</div>
                        </div>
                    )}

                    {/* Layer & Mode Toolbar (Right aligned) */}
                    {(loaders.length > 1 || hasSpots) && (
                        <div style={{
                            position: 'absolute', right: 12, bottom: 12, zIndex: 650,
                            display: 'flex', flexDirection: 'column', gap: 6,
                            background: 'white', padding: '10px 14px',
                            border: '2px solid rgba(0,0,0,0.25)', borderRadius: 4,
                            boxShadow: '0 1px 5px rgba(0,0,0,0.4)',
                            pointerEvents: 'auto', fontFamily: 'sans-serif', fontSize: 13
                        }}>
                            {loaders.length > 1 && (
                                <>
                                    <div style={{ fontWeight: 'bold', marginBottom: 2 }}>View Mode</div>
                                    <select
                                        value={viewMode}
                                        onChange={e => setViewMode(e.target.value)}
                                        style={{ padding: 4, borderRadius: 3, border: '1px solid #ccc', outline: 'none' }}
                                    >
                                        <option value="single">Single Layer</option>
                                        <option value="side-by-side">Side by Side</option>
                                    </select>
                                </>
                            )}

                            {viewMode === 'single' && loaders.length > 1 && (
                                <>
                                    <div style={{ fontWeight: 'bold', marginTop: 8, marginBottom: 2 }}>Active Layer</div>
                                    <select
                                        value={internalActiveLayer}
                                        onChange={e => {
                                            const val = Number(e.target.value);
                                            setInternalActiveLayer(val);
                                            if (setProps) setProps({ active_layer: val });
                                        }}
                                        style={{ padding: 4, borderRadius: 3, border: '1px solid #ccc', outline: 'none' }}
                                    >
                                        {loaders.map((_, i) => (
                                            <option key={i} value={i}>Image Layer {i + 1}</option>
                                        ))}
                                    </select>

                                    {loaders.length >= 2 && (
                                        <div style={{ marginTop: 8 }}>
                                            <div style={{ fontWeight: 'bold', marginBottom: 2 }}>Overlay Opacity</div>
                                            <input
                                                type="range"
                                                min="0" max="1" step="0.05"
                                                value={internalOpacity}
                                                onChange={e => {
                                                    const val = parseFloat(e.target.value);
                                                    // Direct DOM mutation — instant, zero React overhead
                                                    if (overlayDivRef.current) overlayDivRef.current.style.opacity = val;
                                                    setInternalOpacity(val);
                                                }}
                                                onMouseUp={e => {
                                                    const val = parseFloat(e.target.value);
                                                    if (setProps) {
                                                        const newOpacity = {};
                                                        loaders.forEach((_, i) => {
                                                            newOpacity[i] = (i === internalActiveLayer) ? val : 1.0;
                                                        });
                                                        setProps({ opacity: newOpacity });
                                                    }
                                                }}
                                                style={{ width: '100%' }}
                                            />
                                        </div>
                                    )}
                                </>
                            )}

                            {hasSpots && (
                                <div style={{ marginTop: 8 }}>
                                    <div style={{ fontWeight: 'bold', marginBottom: 2 }}>Cluster Opacity</div>
                                    <input
                                        type="range"
                                        min="0" max="1" step="0.05"
                                        value={clusterOpacity}
                                        onChange={e => setClusterOpacity(parseFloat(e.target.value))}
                                        style={{ width: '100%' }}
                                    />
                                </div>
                            )}
                        </div>
                    )}

                    {/* Leaflet-style floating toolbar */}
                    <div style={{
                        position: 'absolute', top: 12, left: 12, zIndex: 500,
                        display: 'flex', flexDirection: 'column',
                        border: '2px solid rgba(0,0,0,0.25)', borderRadius: 4,
                        boxShadow: '0 1px 5px rgba(0,0,0,0.4)', overflow: 'hidden',
                        pointerEvents: 'auto'
                    }}>
                        {hasSpots && <button title={spotSelectMode ? 'Turn off spot selection' : `Show/select spatial spots (${spotList.length})`} onClick={() => { const next = !spotSelectMode; setSpotSelectMode(next); if (!next) { setSelectedSpotId(null); setHoverSpot(null); if (setProps) setProps({ selected_spot: null }); } setDrawMode(null); cancelPolygon(); }} style={btnStyle(spotSelectMode, false)}>•</button>}
                        <button title="Draw rectangle ROI" onClick={() => { setDrawMode(m => m === 'rect' ? null : 'rect'); setSpotSelectMode(false); cancelPolygon(); }} style={btnStyle(drawMode === 'rect', false)}>▭</button>
                        <button title="Draw polygon ROI" onClick={() => { setDrawMode(m => m === 'polygon' ? null : 'polygon'); setSpotSelectMode(false); setRectDraft(null); }} style={btnStyle(drawMode === 'polygon', false)}>⬡</button>
                        {drawMode === 'polygon' && polyDraft.length >= 3 && (
                            <button title="Finish polygon" onClick={finishPolygon} style={{ ...btnStyle(false, false), background: '#d4f7d4', color: 'green', fontWeight: 'bold', fontSize: 13 }}>✓</button>
                        )}
                        {drawMode === 'polygon' && polyDraft.length > 0 && (
                            <button title="Cancel polygon" onClick={cancelPolygon} style={{ ...btnStyle(false, false), color: '#c00', fontSize: 13 }}>✕</button>
                        )}
                        {drawMode === 'polygon' && polyDraft.length > 0 && (
                            <button title="Undo last point" onClick={() => setPolyDraft(p => p.slice(0, -1))} style={{ ...btnStyle(false, false), fontSize: 11, color: '#555' }}>↩pt</button>
                        )}
                        <div style={{ borderTop: '1px solid #ddd' }} />
                        <button title="Undo last ROI" onClick={() => setRoiList(p => p.slice(0, -1))} disabled={roiList.length === 0} style={btnStyle(false, roiList.length === 0)}>↩</button>
                        <button title="Clear all ROIs" onClick={() => setRoiList([])} disabled={roiList.length === 0} style={btnStyle(false, roiList.length === 0, true)}>🗑</button>
                    </div>

                    {/* SVG overlay */}
                    <svg ref={svgRef} style={{
                        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
                        pointerEvents: isDrawing ? 'auto' : 'none',
                        cursor: drawMode === 'rect' ? 'crosshair' : drawMode === 'polygon' ? 'cell' : 'default',
                        userSelect: 'none', overflow: 'hidden', zIndex: 600
                    }}
                        onMouseDown={onMouseDown} onMouseMove={onMouseMove}
                        onMouseUp={onMouseUp} onMouseLeave={onMouseUp} onWheel={onWheel}
                    >
                        <defs>
                            <clipPath id="pane0">
                                <rect x={0} y={0} width={viewMode === 'side-by-side' && loaders.length >= 2 ? containerSize.width / 2 : containerSize.width} height={containerSize.height} />
                            </clipPath>
                            <clipPath id="pane1">
                                <rect x={viewMode === 'side-by-side' && loaders.length >= 2 ? containerSize.width / 2 : 0} y={0} width={viewMode === 'side-by-side' && loaders.length >= 2 ? containerSize.width / 2 : containerSize.width} height={containerSize.height} />
                            </clipPath>
                        </defs>

                        {/* Committed ROIs */}
                        {roiList.map((roi, idx) => {
                            const isSBS = viewMode === 'side-by-side' && loaders.length >= 2;
                            const panes = isSBS ? [0, 1] : [0];

                            return panes.map(paneIndex => {
                                const screenPts = roi.points.map(([ix, iy]) => imageToScreen(ix, iy, paneIndex)).filter(Boolean);
                                if (!screenPts.length) return null;
                                const ptStr = screenPts.map(([x, y]) => `${x},${y}`).join(' ');
                                const color = '#00e5ff';
                                const fill = 'rgba(0,229,255,0.12)';
                                return (
                                    <g key={`${roi.id}-pane${paneIndex}`} clipPath={`url(#pane${paneIndex})`}>
                                        <polygon points={ptStr} fill={fill} stroke={color} strokeWidth={3} />
                                        <text x={screenPts[0][0] + 4} y={screenPts[0][1] + 14} fontSize={11} fill={color} style={{ fontFamily: 'monospace', pointerEvents: 'none' }}>#{idx + 1}</text>
                                    </g>
                                );
                            });
                        })}

                        {/* Live rect draft */}
                        {rectDraft && rectDraft.width > 0 && (
                            <g clipPath={viewMode === 'side-by-side' && loaders.length >= 2 ? (rectDraft.x >= containerSize.width / 2 ? 'url(#pane1)' : 'url(#pane0)') : undefined}>
                                <rect x={rectDraft.x} y={rectDraft.y} width={rectDraft.width} height={rectDraft.height}
                                    fill="rgba(0,229,255,0.14)" stroke="#00e5ff" strokeWidth={3} strokeDasharray="6 3" />
                            </g>
                        )}

                        {/* Live polygon draft */}
                        {polyDraft.length > 0 && (
                            <g clipPath={viewMode === 'side-by-side' && loaders.length >= 2 ? (polyDraft[0].x >= containerSize.width / 2 ? 'url(#pane1)' : 'url(#pane0)') : undefined}>
                                {polyDraft.length >= 3 && <polygon points={polyDraft.map(p => `${p.x},${p.y}`).join(' ')} fill="rgba(0,229,255,0.12)" stroke="none" />}
                                <polyline points={polyDraft.map(p => `${p.x},${p.y}`).join(' ')} fill="none" stroke="#00e5ff" strokeWidth={3} />
                                {cursor && <line x1={polyDraft[polyDraft.length - 1].x} y1={polyDraft[polyDraft.length - 1].y} x2={cursor.x} y2={cursor.y} stroke="#00e5ff" strokeWidth={2} strokeDasharray="5 3" />}
                                {cursor && polyDraft.length >= 2 && <line x1={cursor.x} y1={cursor.y} x2={polyDraft[0].x} y2={polyDraft[0].y} stroke="#00e5ff" strokeWidth={1.5} strokeDasharray="3 4" opacity={0.4} />}
                                {polyDraft.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r={i === 0 ? 6 : 4} fill={i === 0 ? '#00e5ff' : 'white'} stroke="#00343a" strokeWidth={2} />)}
                            </g>
                        )}
                    </svg>
                </>
            )}
        </div>
    );
};

export default VivViewer;
