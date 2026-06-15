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
    const is3Channel8Bit = numChannels === 3 && Pixels.Type === 'uint8';
    const interleavedRgb = Pixels.SizeC === 3 && numChannels === 1 && Pixels.Interleaved;
    return SamplesPerPixel === 3 || is3Channel8Bit || interleavedRgb;
}

function isInterleaved(shape) {
    const lastDimSize = shape[shape.length - 1];
    return lastDimSize === 3 || lastDimSize === 4;
}

const VivViewer = ({ id, image_url, height = 600, width, bg_color = '#111', active_layer = 0, opacity, rois = [], setProps }) => {
    const containerRef = useRef(null);
    const svgRef = useRef(null);
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

    const onMouseDown = drawMode === 'rect' ? onRectDown : onPolyDown;
    const onMouseMove = useCallback((e) => { onRectMove(e); onPolyMove(e); }, [onRectMove, onPolyMove]);
    const onMouseUp = drawMode === 'rect' ? onRectUp : onPolyUp;

    const isDrawing = drawMode !== null;

    const overlayViewerConfig = useMemo(() => {
        if (viewMode !== 'single' || loaders.length < 2) return null;
        
        const loader = loaders[internalActiveLayer];
        if (!loader) return null;

        // Determine data type and bands for the overlay loader
        const isRgb = loader.dtype === 'Uint8' && loader.shape[loader.shape.length - 1] === 3;
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
        <div id={id} ref={containerRef} style={{ position: 'relative', width: width || '100%', height, background: bg_color, overflow: 'hidden', touchAction: 'none', overscrollBehavior: 'none' }}>
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

                    {/* Layer & Mode Toolbar (Right aligned) */}
                    {loaders.length > 1 && (
                        <div style={{
                            position: 'absolute', top: 12, right: 12, zIndex: 500,
                            display: 'flex', flexDirection: 'column', gap: 6,
                            background: 'white', padding: '10px 14px',
                            border: '2px solid rgba(0,0,0,0.25)', borderRadius: 4,
                            boxShadow: '0 1px 5px rgba(0,0,0,0.4)',
                            pointerEvents: 'auto', fontFamily: 'sans-serif', fontSize: 13
                        }}>
                            <div style={{ fontWeight: 'bold', marginBottom: 2 }}>View Mode</div>
                            <select
                                value={viewMode}
                                onChange={e => setViewMode(e.target.value)}
                                style={{ padding: 4, borderRadius: 3, border: '1px solid #ccc', outline: 'none' }}
                            >
                                <option value="single">Single Layer</option>
                                <option value="side-by-side">Side by Side</option>
                            </select>

                            {viewMode === 'single' && (
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
                        <button title="Draw rectangle ROI" onClick={() => { setDrawMode(m => m === 'rect' ? null : 'rect'); cancelPolygon(); }} style={btnStyle(drawMode === 'rect', false)}>▭</button>
                        <button title="Draw polygon ROI" onClick={() => { setDrawMode(m => m === 'polygon' ? null : 'polygon'); setRectDraft(null); }} style={btnStyle(drawMode === 'polygon', false)}>⬡</button>
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
                        userSelect: 'none', overflow: 'hidden'
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
                                const color = roi.type === 'rect' ? 'red' : '#1a66ff';
                                const fill = roi.type === 'rect' ? 'rgba(255,50,50,0.1)' : 'rgba(26,102,255,0.1)';
                                return (
                                    <g key={`${roi.id}-pane${paneIndex}`} clipPath={`url(#pane${paneIndex})`}>
                                        <polygon points={ptStr} fill={fill} stroke={color} strokeWidth={2} />
                                        <text x={screenPts[0][0] + 4} y={screenPts[0][1] + 14} fontSize={11} fill={color} style={{ fontFamily: 'monospace', pointerEvents: 'none' }}>#{idx + 1}</text>
                                    </g>
                                );
                            });
                        })}

                        {/* Live rect draft */}
                        {rectDraft && rectDraft.width > 0 && (
                            <g clipPath={viewMode === 'side-by-side' && loaders.length >= 2 ? (rectDraft.x >= containerSize.width / 2 ? 'url(#pane1)' : 'url(#pane0)') : undefined}>
                                <rect x={rectDraft.x} y={rectDraft.y} width={rectDraft.width} height={rectDraft.height}
                                    fill="rgba(255,50,50,0.15)" stroke="red" strokeWidth={2} strokeDasharray="6 3" />
                            </g>
                        )}

                        {/* Live polygon draft */}
                        {polyDraft.length > 0 && (
                            <g clipPath={viewMode === 'side-by-side' && loaders.length >= 2 ? (polyDraft[0].x >= containerSize.width / 2 ? 'url(#pane1)' : 'url(#pane0)') : undefined}>
                                {polyDraft.length >= 3 && <polygon points={polyDraft.map(p => `${p.x},${p.y}`).join(' ')} fill="rgba(26,102,255,0.1)" stroke="none" />}
                                <polyline points={polyDraft.map(p => `${p.x},${p.y}`).join(' ')} fill="none" stroke="#1a66ff" strokeWidth={2} />
                                {cursor && <line x1={polyDraft[polyDraft.length - 1].x} y1={polyDraft[polyDraft.length - 1].y} x2={cursor.x} y2={cursor.y} stroke="#1a66ff" strokeWidth={1.5} strokeDasharray="5 3" />}
                                {cursor && polyDraft.length >= 2 && <line x1={cursor.x} y1={cursor.y} x2={polyDraft[0].x} y2={polyDraft[0].y} stroke="#1a66ff" strokeWidth={1} strokeDasharray="3 4" opacity={0.4} />}
                                {polyDraft.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r={i === 0 ? 6 : 4} fill={i === 0 ? '#1a66ff' : 'white'} stroke="#1a66ff" strokeWidth={2} />)}
                            </g>
                        )}
                    </svg>
                </>
            )}
        </div>
    );
};

export default VivViewer;
