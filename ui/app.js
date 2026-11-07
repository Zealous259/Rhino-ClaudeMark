/* ClaudeMark 4: the markup app. Runs in WebView2 inside Rhino, talking to claude_mark.py over the
   WebView2 message channel, or in a plain browser with mock.js for testing.
   Image px everywhere in the data (origin top left, y down), exactly as claude_mark.py records them. */
'use strict';
(function () {

  // ---------------------------------------------------------------- small helpers
  const $ = (s, r) => (r || document).querySelector(s);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const hyp = Math.hypot;
  const r1 = v => Math.round(v * 10) / 10;
  const clone = o => JSON.parse(JSON.stringify(o));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, cls) => `<svg class="ic ${cls || ''}"><use href="#i-${n}"/></svg>`;
  const el = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const debounce = (f, ms) => { let t; return function () { const a = arguments; clearTimeout(t); t = setTimeout(() => f.apply(null, a), ms); }; };
  const css = (rgb, a) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a == null ? 1 : a})`;
  const lum = rgb => (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;

  // ---------------------------------------------------------------- the bridge to claude_mark.py
  const Bridge = (function () {
    const wv = window.chrome && window.chrome.webview;
    let seq = 0;
    const pending = new Map();
    const listeners = {};
    function receive(data) {
      if (typeof data === 'string') { try { data = JSON.parse(data); } catch (e) { return; } }
      if (data && data.id != null && pending.has(data.id)) {
        const p = pending.get(data.id); pending.delete(data.id);
        if (data.ok === false) p.rej(new Error(data.error || 'failed')); else p.res(data.result);
      } else if (data && data.event) {
        (listeners[data.event] || []).forEach(f => { try { f(data); } catch (e) { console.error(e); } });
      }
    }
    if (wv) wv.addEventListener('message', ev => receive(ev.data));
    function post(text, files) {
      if (wv) {
        if (files && files.length && wv.postMessageWithAdditionalObjects) wv.postMessageWithAdditionalObjects(text, files);
        else wv.postMessage(text);
      } else if (window.MockBridge) {
        window.MockBridge.receive(text, receive);
      }
    }
    return {
      live: !!wv,
      call(type, payload, files) {
        const id = ++seq;
        return new Promise((res, rej) => {
          pending.set(id, { res, rej });
          try { post(JSON.stringify({ id, type, payload: payload || {} }), files); }
          catch (e) { pending.delete(id); e.postFailed = true; rej(e); }   // never reached Python
        });
      },
      fire(type, payload) { post(JSON.stringify({ type, payload: payload || {} })); },
      raw(prefix, text) { post(prefix + text); },
      on(ev, f) { (listeners[ev] = listeners[ev] || []).push(f); },
    };
  })();

  function report(err, where) {
    console.error(where, err);
    try { Bridge.fire('log', { msg: 'UI error in ' + where + ': ' + (err && err.stack || err) }); } catch (e) { /* nothing */ }
  }
  window.addEventListener('error', e => report(e.error || e.message, 'window'));
  window.addEventListener('unhandledrejection', e => report(e.reason, 'promise'));

  // ---------------------------------------------------------------- constants
  const COLOURS = [
    { name: 'red', rgb: [255, 69, 58] }, { name: 'yellow', rgb: [255, 204, 0] }, { name: 'green', rgb: [48, 209, 88] },
    { name: 'blue', rgb: [10, 132, 255] }, { name: 'purple', rgb: [191, 90, 242] }, { name: 'white', rgb: [255, 255, 255] },
    { name: 'black', rgb: [28, 28, 30] },
  ];
  const OLD_COLOUR_ORDER = ['red', 'yellow', 'blue', 'green'];
  const WIDTHS = [{ name: 'Thin', px: 3 }, { name: 'Medium', px: 6 }, { name: 'Thick', px: 11 }];
  const MARKER_W = { 3: 16, 6: 26, 11: 44 };
  const TEXT_PX = { 3: 22, 6: 30, 11: 42 };
  const ERASER_R = { 3: 14, 6: 28, 11: 56 };
  const CLAUDE_RGB = [217, 119, 87];
  const MATCH_RGB = [56, 215, 255];
  const NUMBERED = new Set(['box', 'ellipse', 'cloud', 'pin']);
  const KIND_NAME = { pen: 'Pen line', highlight: 'Highlight', box: 'Box', ellipse: 'Ellipse', cloud: 'Cloud', arrow: 'Arrow', pin: 'Pin',
    text: 'Text note', measure: 'Measure', ref: 'Reference', match: 'Match point' };
  const KIND_ICON = { pen: 'pen', highlight: 'marker', box: 'box', ellipse: 'ellipse', cloud: 'cloud', arrow: 'arrow', pin: 'pin',
    text: 'text', measure: 'ruler', ref: 'image', match: 'match' };
  const TOOLS = [
    { id: 'select', icon: 'select', key: 'v', name: 'Select', sub: 'Move, resize, recolour or comment on a mark' },
    { id: 'box', icon: 'box', key: 'b', name: 'Box', sub: 'Numbered. Shift draws a square' },
    { id: 'ellipse', icon: 'ellipse', key: 'o', name: 'Ellipse', sub: 'Numbered. Shift draws a circle', more: 2 },
    { id: 'cloud', icon: 'cloud', key: 'c', name: 'Revision cloud', sub: 'Numbered. Drag over the area to cloud', more: 2 },
    { id: 'pen', icon: 'pen', key: 'p', name: 'Pen', sub: 'Freehand. Hold Shift for a straight line' },
    { id: 'marker', icon: 'marker', key: 'm', name: 'Highlighter', sub: 'A wide translucent marker for areas', more: 3 },
    { id: 'arrow', icon: 'arrow', key: 'a', name: 'Arrow', sub: 'Drag from tail to tip. Shift snaps to 15 degrees' },
    { id: 'pin', icon: 'pin', key: 'n', name: 'Pin', sub: 'A numbered point' },
    { id: 'text', icon: 'text', key: 't', name: 'Text note', sub: 'Click and type on the spot it applies to' },
    { id: 'measure', icon: 'ruler', key: 'd', name: 'Measure', sub: 'Real distance between two points on the model', more: 1 },
    { id: 'eraser', icon: 'eraser', key: 'e', name: 'Eraser', sub: 'Rubs out pen lines; other marks go whole' },
    { id: 'match', icon: 'match', key: 'M', name: 'Photo match', sub: 'Pair points on a site photo with the model, for a montage', more: 1 },
  ];
  const KIND_OF_TOOL = { marker: 'highlight' };
  const ACCENTS = { blue: '#0A84FF', purple: '#BF5AF2', pink: '#FF375F', orange: '#FF9F0A', green: '#30D158', graphite: '#8E8E93', claude: '#D97757' };

  // ---------------------------------------------------------------- state
  const S = {
    boot: null, mode: 'capture', iw: 1, ih: 1, img: null,
    strokes: [], claude: [], showClaude: true,
    options: [], optImgs: {}, optIndex: 0,
    history: [], future: [],
    tool: 'box', colour: 0, width: 1,
    sel: null, hoverMark: null, live: null, drag: null, shiftBase: null,
    view: { zoom: 1, cx: 0, cy: 0 }, pview: { zoom: 1, cx: 0, cy: 0 }, safe: { x: 0, y: 0, w: 100, h: 100 }, dpr: 1,
    photo: null, photoLast: null, refImgs: {}, refCount: 0,
    task: 'general', presets: [], allPresets: {}, categories: [],
    settings: {}, terminals: [], target: null, queue: [],
    insetL: 0, insetR: 0, mouse: { x: -1, y: -1, in: false }, spaceDown: false,
    editing: null, comment: null, dropHover: false, sending: false, raf: 0, ids: 0,
    hoverPick: { seq: 0, last: null, timer: 0, slow: 0, paused: false },
    recallIndex: -1,
  };

  // ---------------------------------------------------------------- geometry
  function baseImage() {
    if (S.photo && S.refImgs[S.photo]) return S.refImgs[S.photo];
    if (S.options.length) { const o = S.options[S.optIndex]; if (o && S.optImgs[o.label]) return S.optImgs[o.label]; }
    return S.img;
  }
  function dims() {
    if (S.photo && S.refImgs[S.photo]) { const im = S.refImgs[S.photo]; return [im.naturalWidth, im.naturalHeight]; }
    return [S.iw, S.ih];
  }
  function vstate() { return S.photo ? S.pview : S.view; }
  function T() {
    const [iw, ih] = dims();
    const sa = S.safe, v = vstate();
    const fit = Math.min(sa.w / iw, sa.h / ih, S.photo ? 4 : 1);
    const s = fit * v.zoom;
    return { s, fit, iw, ih, ox: sa.x + sa.w / 2 - v.cx * s, oy: sa.y + sa.h / 2 - v.cy * s };
  }
  function toImg(x, y, noClamp) {
    const t = T();
    const p = [(x - t.ox) / t.s, (y - t.oy) / t.s];
    if (noClamp) return p;
    return [r1(clamp(p[0], 0, t.iw - 1)), r1(clamp(p[1], 0, t.ih - 1))];
  }
  function toScreen(p) { const t = T(); return [t.ox + p[0] * t.s, t.oy + p[1] * t.s]; }
  function insideImage(x, y) { const t = T(); return x >= t.ox && y >= t.oy && x <= t.ox + t.iw * t.s && y <= t.oy + t.ih * t.s; }

  function computeSafe() {
    const W = innerWidth, H = innerHeight;
    const tb = $('#toolbar').getBoundingClientRect();
    const tl = $('.cluster.tl').getBoundingClientRect();
    const cp = $('#composer').getBoundingClientRect();
    let top = Math.max(tb.bottom, tl.bottom) + 14;
    const bn = $('#banners');
    if (bn.children.length) top = Math.max(top, bn.getBoundingClientRect().bottom + 12);
    const left = 18 + S.insetL, right = W - 18 - S.insetR;
    const bottom = cp.top - 14;
    S.safe = { x: left, y: top, w: Math.max(120, right - left), h: Math.max(120, bottom - top) };
  }
  function resetView(v, w, h) { v.zoom = 1; v.cx = w / 2; v.cy = h / 2; }
  function clampView() {
    const v = vstate(); const [iw, ih] = dims();
    if (v.zoom <= 1.0001) { v.zoom = Math.max(v.zoom, 0.5); if (v.zoom >= 0.999) { v.cx = iw / 2; v.cy = ih / 2; } }
    v.cx = clamp(v.cx, 0, iw); v.cy = clamp(v.cy, 0, ih);
  }
  function zoomAt(factor, sx, sy) {
    const t = T(); const v = vstate();
    const ix = (sx - t.ox) / t.s, iy = (sy - t.oy) / t.s;
    const z = clamp(v.zoom * factor, 0.5, 16);
    const s2 = t.fit * z;
    v.zoom = z;
    v.cx = ix - (sx - S.safe.x - S.safe.w / 2) / s2;
    v.cy = iy - (sy - S.safe.y - S.safe.h / 2) / s2;
    if (z < 1.0001 && z > 0.999) { const [iw, ih] = dims(); v.cx = iw / 2; v.cy = ih / 2; }
    clampView(); zoomLabel(); dirty(); hideHover();
  }
  function animateView(target) {
    const v = vstate(); const from = { zoom: v.zoom, cx: v.cx, cy: v.cy }; const t0 = performance.now(); const dur = S.settings.reduce_motion ? 1 : 380;
    function step(now) {
      const k = clamp((now - t0) / dur, 0, 1); const e = 1 - Math.pow(1 - k, 3);
      v.zoom = from.zoom + (target.zoom - from.zoom) * e; v.cx = from.cx + (target.cx - from.cx) * e; v.cy = from.cy + (target.cy - from.cy) * e;
      zoomLabel(); dirty(); if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  function fitView() { const [iw, ih] = dims(); animateView({ zoom: 1, cx: iw / 2, cy: ih / 2 }); }
  function actualSize() { const t = T(); const v = vstate(); animateView({ zoom: 1 / t.fit, cx: v.cx, cy: v.cy }); }
  function zoomBy(f) { zoomAt(f, S.safe.x + S.safe.w / 2, S.safe.y + S.safe.h / 2); }
  function zoomLabel() {
    const v = vstate(); const t = T();
    const b = $('#zoom-btn');
    b.textContent = Math.abs(v.zoom - 1) < 0.002 ? 'Fit' : Math.round(t.s * 100) + '%';
  }

  function segDist(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], d2 = dx * dx + dy * dy;
    const t = d2 === 0 ? 0 : clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2, 0, 1);
    return hyp(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  }
  function rectOf(st) {
    const a = st.points[0], b = st.points[st.points.length - 1];
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])];
  }
  function pinRadius(st) { return 12 + (st.width || 6); }
  function badgeRadius(st) { return 11 + (st.width || 6) * 0.35; }
  function badgeCentre(st) {
    if (st.kind === 'pin') return st.points[0];
    const [x, y, w, h] = rectOf(st);
    if (st.kind === 'ellipse') return [x + w / 2 - (w / 2) * 0.7071, y + h / 2 - (h / 2) * 0.7071];
    return [x, y];
  }
  function bounds(st) {
    const k = st.kind, p = st.points;
    if (k === 'ref') { const [cx, cy] = p[0]; const [w, h] = st.size; return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]; }
    if (k === 'text') { const [x, y] = p[0]; const [w, h] = st.size || [0, 0]; return [x, y, x + w, y + h]; }
    if (k === 'pin') { const r = pinRadius(st); return [p[0][0] - r, p[0][1] - r, p[0][0] + r, p[0][1] + r]; }
    if (k === 'match') return [p[0][0] - 12, p[0][1] - 12, p[0][0] + 12, p[0][1] + 12];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const q of p) { x0 = Math.min(x0, q[0]); y0 = Math.min(y0, q[1]); x1 = Math.max(x1, q[0]); y1 = Math.max(y1, q[1]); }
    const pad = (st.width || 6) / 2;
    return [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
  }
  function hitMark(st, p, tol) {
    const k = st.kind, pts = st.points, w = st.width || 6;
    if (k === 'ref' || k === 'text') { const b = bounds(st); return p[0] >= b[0] - tol && p[0] <= b[2] + tol && p[1] >= b[1] - tol && p[1] <= b[3] + tol; }
    if (k === 'pin') return hyp(p[0] - pts[0][0], p[1] - pts[0][1]) <= Math.max(pinRadius(st), 9 / T().s) + tol;
    if (k === 'match') return hyp(p[0] - pts[0][0], p[1] - pts[0][1]) <= 14 + tol;
    if (k === 'box' || k === 'cloud') { const [x, y, ww, hh] = rectOf(st); const pad = tol + w; return p[0] >= x - pad && p[0] <= x + ww + pad && p[1] >= y - pad && p[1] <= y + hh + pad; }
    if (k === 'ellipse') { const [x, y, ww, hh] = rectOf(st); const rx = ww / 2 + tol + w / 2, ry = hh / 2 + tol + w / 2; const dx = (p[0] - x - ww / 2) / rx, dy = (p[1] - y - hh / 2) / ry; return dx * dx + dy * dy <= 1; }
    if (k === 'arrow' || k === 'measure') return segDist(p, pts[0], pts[pts.length - 1]) <= w / 2 + tol + 4;
    if (k === 'pen' || k === 'highlight') {
      if (pts.length === 1) return hyp(p[0] - pts[0][0], p[1] - pts[0][1]) <= w / 2 + tol;
      for (let i = 1; i < pts.length; i++) if (segDist(p, pts[i - 1], pts[i]) <= w / 2 + tol) return true;
      return false;
    }
    return false;
  }
  function hitTest(p, tol, filter) {
    for (let i = S.strokes.length - 1; i >= 0; i--) { const st = S.strokes[i]; if (filter && !filter(st)) continue; if (hitMark(st, p, tol)) return st; }
    return null;
  }
  function hitBadge(p, t) {
    for (let i = S.strokes.length - 1; i >= 0; i--) {
      const st = S.strokes[i];
      if (st.label == null || !NUMBERED.has(st.kind) || st.kind === 'pin') continue;
      const c = badgeCentre(st);
      if (hyp(p[0] - c[0], p[1] - c[1]) <= Math.max(badgeRadius(st), 9 / t.s) + 3 / t.s) return st;
    }
    return null;
  }
  function translate(st, dx, dy, orig) {
    st.points = orig.points.map(q => [r1(q[0] + dx), r1(q[1] + dy)]);
  }
  function nextLabel() {
    let m = 0;
    for (const st of S.strokes) if (typeof st.label === 'number') m = Math.max(m, st.label);
    return m + 1;
  }

  // ---------------------------------------------------------------- text measuring (same rules for screen and file)
  const mctx = document.createElement('canvas').getContext('2d');
  const FONT = '"Inter", "Segoe UI Variable Text", "Segoe UI", sans-serif';
  function textLayout(st, s) {
    const px = (st.font_px || 30) * s;
    const font = `650 ${px}px ${FONT}`;
    mctx.font = font;
    const lines = String(st.text || '').split('\n');
    const tw = Math.max.apply(null, lines.map(l => mctx.measureText(l || ' ').width));
    const lh = px * 1.28;
    return { font, lines, lh, tw, th: lh * lines.length, padx: 0.42 * px, pady: 0.2 * px, px };
  }
  function measureText(st) { const L = textLayout(st, 1); st.size = [r1(L.tw + 2 * L.padx), r1(L.th + 2 * L.pady)]; }
  function wrapLines(ctx, text, maxW, maxLines) {
    const words = String(text || '').split(/\s+/).filter(Boolean); const out = []; let cur = '';
    for (const w of words) {
      const trial = cur ? cur + ' ' + w : w;
      if (ctx.measureText(trial).width <= maxW || !cur) cur = trial; else { out.push(cur); cur = w; if (out.length === maxLines) break; }
    }
    if (cur && out.length < maxLines) out.push(cur);
    if (out.length === maxLines && out.join(' ').length < words.join(' ').length) {
      let last = out[maxLines - 1];
      while (last.length > 1 && ctx.measureText(last + '...').width > maxW) last = last.slice(0, -1);
      out[maxLines - 1] = last + '...';
    }
    return out;
  }

  // ---------------------------------------------------------------- drawing marks (screen and the exported PNG)
  function rr(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function smoothPath(ctx, pts, X, Y) {
    ctx.beginPath(); ctx.moveTo(X(pts[0]), Y(pts[0]));
    if (pts.length < 3) { for (let i = 1; i < pts.length; i++) ctx.lineTo(X(pts[i]), Y(pts[i])); return; }
    for (let i = 1; i < pts.length - 1; i++) {
      const mx = (X(pts[i]) + X(pts[i + 1])) / 2, my = (Y(pts[i]) + Y(pts[i + 1])) / 2;
      ctx.quadraticCurveTo(X(pts[i]), Y(pts[i]), mx, my);
    }
    const L = pts[pts.length - 1]; ctx.lineTo(X(L), Y(L));
  }
  function perimPoint(x, y, w, h, d) {
    const per = 2 * (w + h); d = ((d % per) + per) % per;
    if (d < w) return [x + d, y]; d -= w;
    if (d < h) return [x + w, y + d]; d -= h;
    if (d < w) return [x + w - d, y + h]; d -= w;
    return [x, y + h - d];
  }
  function cloudPath(ctx, x, y, w, h, s) {
    const r = clamp(Math.min(w, h) / 5, 6 * s, 24 * s);
    const per = 2 * (w + h); const n = Math.max(8, Math.round(per / (r * 1.9)));
    const pts = []; for (let i = 0; i < n; i++) pts.push(perimPoint(x, y, w, h, (i / n) * per));
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 0; i < n; i++) {
      const p = pts[i], q = pts[(i + 1) % n];
      const dx = q[0] - p[0], dy = q[1] - p[1], d = hyp(dx, dy) || 1;
      const nx = dy / d, ny = -dx / d, b = d * 0.62;
      ctx.quadraticCurveTo((p[0] + q[0]) / 2 + nx * b, (p[1] + q[1]) / 2 + ny * b, q[0], q[1]);
    }
    ctx.closePath();
  }
  function arrowHead(a, b, w, s) {
    const dx = b[0] - a[0], dy = b[1] - a[1], len = hyp(dx, dy) || 1, ux = dx / len, uy = dy / len;
    const head = Math.min(Math.max(4.2 * w, 15 * s), 0.6 * len);
    const bx = b[0] - ux * head, by = b[1] - uy * head, half = 0.56 * head;
    return { base: [bx + ux * head * 0.12, by + uy * head * 0.12], tri: [b, [bx - uy * half, by + ux * half], [bx + uy * half, by - ux * half]] };
  }
  function inkOn(rgb) { return lum(rgb) > 0.62 ? '#000' : '#fff'; }

  function drawTag(ctx, x, y, lines, fontPx, s, opts) {
    // a dark rounded tag with light text, used for comments and measures
    const px = fontPx * s; ctx.font = `600 ${px}px ${FONT}`;
    const lh = px * 1.3, padx = px * 0.55, pady = px * 0.32;
    const tw = Math.max.apply(null, lines.map(l => ctx.measureText(l).width));
    const w = tw + padx * 2, h = lh * lines.length + pady * 2;
    let tx = x, ty = y;
    if (opts && opts.centre) { tx = x - w / 2; ty = y - h / 2; }
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.35)'; ctx.shadowBlur = 8 * s; ctx.shadowOffsetY = 2 * s;
    rr(ctx, tx, ty, w, h, Math.min(h / 2, 9 * s)); ctx.fillStyle = (opts && opts.bg) || 'rgba(18,18,22,0.84)'; ctx.fill();
    ctx.restore();
    if (opts && opts.edge) { ctx.save(); rr(ctx, tx, ty, w, h, Math.min(h / 2, 9 * s)); ctx.strokeStyle = opts.edge; ctx.lineWidth = Math.max(1, 1.5 * s); ctx.stroke(); ctx.restore(); }
    ctx.fillStyle = (opts && opts.ink) || '#fff'; ctx.textBaseline = 'middle';
    lines.forEach((l, i) => ctx.fillText(l, tx + padx, ty + pady + lh * i + lh / 2));
    return [tx, ty, w, h];
  }
  // annotations keep a readable size on screen however far the capture is zoomed out; the exported PNG (s = 1) is exact
  const MIN_R = 9, MIN_TAG = 11.5;
  function drawBadge(ctx, st, t, X, Y) {
    const c = badgeCentre(st), s = t.s, r = Math.max(MIN_R, badgeRadius(st) * s), rgb = rgbOf(st);
    const cx = X(c), cy = Y(c);
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.35)'; ctx.shadowBlur = 6 * s; ctx.shadowOffsetY = 1.5 * s;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fillStyle = css(rgb); ctx.fill();
    ctx.restore();
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.lineWidth = Math.max(1, 1.8 * s); ctx.strokeStyle = 'rgba(255,255,255,0.95)'; ctx.stroke();
    ctx.fillStyle = inkOn(rgb); ctx.font = `750 ${r * 1.08}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(String(st.label), cx, cy + r * 0.04); ctx.textAlign = 'start';
    if (st.comment && S.settings.show_comments !== false) drawComment(ctx, st, t, cx + r + 6 * s, cy);
  }
  function drawComment(ctx, st, t, x, y) {
    const fontPx = 15, s = Math.max(t.s, MIN_TAG / fontPx); ctx.font = `600 ${fontPx * s}px ${FONT}`;
    const lines = wrapLines(ctx, st.comment, 360 * s, 3);
    const px = fontPx * s, h = px * 1.3 * lines.length + px * 0.64;
    return drawTag(ctx, x, y - h / 2, lines, fontPx, s, {});
  }
  function rgbOf(st) { return st.by === 'claude' ? CLAUDE_RGB : (st.rgb || CLAUDE_RGB); }

  function drawMark(ctx, st, t, o) {
    o = o || {};
    const s = t.s, X = p => t.ox + p[0] * s, Y = p => t.oy + p[1] * s;
    const rgb = rgbOf(st), col = css(rgb);
    const w = Math.max(1, (st.width || 6) * s), haloW = w + Math.max(2, 3.2 * s), HALO = 'rgba(0,0,0,0.30)';
    const pts = st.points;
    ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    switch (st.kind) {
      case 'pen': {
        if (pts.length === 1) {
          ctx.beginPath(); ctx.arc(X(pts[0]), Y(pts[0]), haloW / 2, 0, 7); ctx.fillStyle = HALO; ctx.fill();
          ctx.beginPath(); ctx.arc(X(pts[0]), Y(pts[0]), w / 2, 0, 7); ctx.fillStyle = col; ctx.fill(); break;
        }
        smoothPath(ctx, pts, X, Y); ctx.strokeStyle = HALO; ctx.lineWidth = haloW; ctx.stroke();
        ctx.strokeStyle = col; ctx.lineWidth = w; ctx.stroke(); break;
      }
      case 'highlight': {
        ctx.globalAlpha = 0.38; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w;
        if (pts.length === 1) { ctx.beginPath(); ctx.arc(X(pts[0]), Y(pts[0]), w / 2, 0, 7); ctx.fill(); break; }
        smoothPath(ctx, pts, X, Y); ctx.stroke(); break;
      }
      case 'box': case 'ellipse': case 'cloud': {
        const [x, y, ww, hh] = rectOf(st); const sx = X([x, 0]), sy = Y([0, y]), sw = ww * s, sh = hh * s;
        const shape = () => {
          if (st.kind === 'box') rr(ctx, sx, sy, sw, sh, Math.min(3 * s, sw / 2, sh / 2));
          else if (st.kind === 'ellipse') { ctx.beginPath(); ctx.ellipse(sx + sw / 2, sy + sh / 2, Math.max(0.5, sw / 2), Math.max(0.5, sh / 2), 0, 0, Math.PI * 2); }
          else cloudPath(ctx, sx, sy, sw, sh, s);
        };
        shape(); ctx.strokeStyle = HALO; ctx.lineWidth = haloW; ctx.stroke();
        shape(); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.stroke();
        if (st.label != null && !o.live) drawBadge(ctx, st, t, X, Y);
        break;
      }
      case 'arrow': {
        const a = pts[0], b = pts[pts.length - 1];
        if (hyp(b[0] - a[0], b[1] - a[1]) * s < 1) break;
        const A = [X(a), Y(a)], B = [X(b), Y(b)];
        const hd = arrowHead(A, B, w, s);
        ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(hd.base[0], hd.base[1]); ctx.strokeStyle = HALO; ctx.lineWidth = haloW; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(hd.tri[0][0], hd.tri[0][1]); ctx.lineTo(hd.tri[1][0], hd.tri[1][1]); ctx.lineTo(hd.tri[2][0], hd.tri[2][1]); ctx.closePath();
        ctx.lineWidth = Math.max(2, 3 * s); ctx.strokeStyle = HALO; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(hd.base[0], hd.base[1]); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(hd.tri[0][0], hd.tri[0][1]); ctx.lineTo(hd.tri[1][0], hd.tri[1][1]); ctx.lineTo(hd.tri[2][0], hd.tri[2][1]); ctx.closePath();
        ctx.fillStyle = col; ctx.fill(); ctx.lineWidth = Math.max(1, 1.2 * s); ctx.strokeStyle = col; ctx.stroke();
        if (st.comment && S.settings.show_comments !== false) drawComment(ctx, st, t, A[0] + 8 * s, A[1]);
        break;
      }
      case 'measure': {
        const a = pts[0], b = pts[pts.length - 1];
        const A = [X(a), Y(a)], B = [X(b), Y(b)];
        const dx = B[0] - A[0], dy = B[1] - A[1], len = hyp(dx, dy) || 1, nx = -dy / len, ny = dx / len, tk = 9 * s;
        const path = () => { ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]);
          ctx.moveTo(A[0] - nx * tk, A[1] - ny * tk); ctx.lineTo(A[0] + nx * tk, A[1] + ny * tk);
          ctx.moveTo(B[0] - nx * tk, B[1] - ny * tk); ctx.lineTo(B[0] + nx * tk, B[1] + ny * tk); };
        path(); ctx.strokeStyle = HALO; ctx.lineWidth = Math.max(2, w * 0.6) + 3 * s; ctx.stroke();
        path(); ctx.strokeStyle = col; ctx.lineWidth = Math.max(1.5, w * 0.6); ctx.stroke();
        const txt = st.measure ? st.measure.text : (o.live ? '' : 'measuring...');
        if (txt) drawTag(ctx, (A[0] + B[0]) / 2, (A[1] + B[1]) / 2, [txt], 15, Math.max(s, MIN_TAG / 15), { centre: true, edge: css(rgb, 0.9) });
        break;
      }
      case 'pin': {
        const c = [X(pts[0]), Y(pts[0])], r = Math.max(MIN_R, pinRadius(st) * s);
        ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.38)'; ctx.shadowBlur = 8 * s; ctx.shadowOffsetY = 2.5 * s;
        ctx.beginPath(); ctx.arc(c[0], c[1], r, 0, 7); ctx.fillStyle = col; ctx.fill(); ctx.restore();
        ctx.beginPath(); ctx.arc(c[0], c[1], r, 0, 7); ctx.lineWidth = Math.max(1, 2.2 * s); ctx.strokeStyle = '#fff'; ctx.stroke();
        ctx.fillStyle = inkOn(rgb); ctx.font = `750 ${r * (String(st.label).length > 2 ? 0.8 : 1.0)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(String(st.label == null ? '' : st.label), c[0], c[1] + r * 0.04); ctx.textAlign = 'start';
        if (st.comment && S.settings.show_comments !== false) drawComment(ctx, st, t, c[0] + r + 6 * s, c[1]);
        break;
      }
      case 'text': {
        const L = textLayout(st, s); const x = X(pts[0]), y = Y(pts[0]);
        const bw = L.tw + 2 * L.padx, bh = L.th + 2 * L.pady, rad = Math.min(bh / 2, Math.max(3, 0.3 * L.px));
        const dark = lum(rgb) < 0.2;
        ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.3)'; ctx.shadowBlur = 10 * s; ctx.shadowOffsetY = 2.5 * s;
        rr(ctx, x, y, bw, bh, rad); ctx.fillStyle = dark ? 'rgba(255,255,255,0.9)' : 'rgba(18,18,22,0.82)'; ctx.fill(); ctx.restore();
        rr(ctx, x, y, bw, bh, rad); ctx.strokeStyle = css(rgb, 0.6); ctx.lineWidth = Math.max(1, 1.5 * s); ctx.stroke();
        ctx.font = L.font; ctx.fillStyle = col; ctx.textBaseline = 'middle';
        L.lines.forEach((ln, i) => ctx.fillText(ln, x + L.padx, y + L.pady + i * L.lh + L.lh / 2));
        break;
      }
      case 'ref': {
        const [cx, cy] = pts[0], [rw0, rh0] = st.size; const rw = rw0 * s, rh = rh0 * s, x = X([cx - rw0 / 2, 0]), y = Y([0, cy - rh0 / 2]);
        const pad = Math.max(2, 5 * s);
        if (ghostRefs() && !t.export) {   // placing model points: just an outline, so the model under it shows and takes the click
          const cs = Math.max(s, MIN_TAG / 14);
          ctx.save(); rr(ctx, x - pad, y - pad, rw + 2 * pad, rh + 2 * pad, 6 * s);
          ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = Math.max(2, 3 * s); ctx.stroke();
          ctx.setLineDash([6 * s, 5 * s]); ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = Math.max(1, 1.5 * s); ctx.stroke(); ctx.restore();
          drawTag(ctx, x - pad, y - pad - 30 * cs, [st.label + '  Tab opens the photo'], 14, cs, { bg: 'rgba(20,20,24,0.6)' });
          break;
        }
        ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.42)'; ctx.shadowBlur = 18 * s; ctx.shadowOffsetY = 6 * s;
        rr(ctx, x - pad, y - pad, rw + 2 * pad, rh + 2 * pad, 6 * s); ctx.fillStyle = '#fff'; ctx.fill(); ctx.restore();
        const im = S.refImgs[st.label];
        ctx.save(); rr(ctx, x, y, rw, rh, 3 * s); ctx.clip();
        if (im && im.complete && im.naturalWidth) ctx.drawImage(im, x, y, rw, rh); else { ctx.fillStyle = '#ccc'; ctx.fillRect(x, y, rw, rh); }
        ctx.restore();
        const chip = st.label + (st.matches && st.matches.length ? '  ' + st.matches.length + ' match points' : '');
        const cs = Math.max(s, MIN_TAG / 14);
        drawTag(ctx, x - pad, y - pad - 30 * cs, [chip], 14, cs, { bg: 'rgba(20,20,24,0.86)' });
        if (st.comment && S.settings.show_comments !== false) drawComment(ctx, st, t, x + rw + pad + 8 * s, y + 14 * s);
        break;
      }
      case 'match': drawMatch(ctx, X(pts[0]), Y(pts[0]), st.label, s); break;
    }
    ctx.restore();
  }
  function drawMatch(ctx, x, y, label, s, big) {
    const rad = big ? 13 : Math.max(8, 12 * s); const col = css(MATCH_RGB);
    ctx.save(); ctx.lineCap = 'round';
    for (const [c, wd] of [['rgba(0,0,0,0.6)', 4], [col, 1.8]]) {
      ctx.strokeStyle = c; ctx.lineWidth = wd;
      ctx.beginPath(); ctx.arc(x, y, rad, 0, 7); ctx.stroke();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { ctx.beginPath(); ctx.moveTo(x + dx * rad * 0.35, y + dy * rad * 0.35); ctx.lineTo(x + dx * rad * 1.5, y + dy * rad * 1.5); ctx.stroke(); }
    }
    ctx.restore();
    drawTag(ctx, x + rad * 1.1, y - rad * 1.1 - 22, [String(label)], 13, 1, { bg: 'rgba(8,40,52,0.9)', ink: col });
  }

  function drawSelection(ctx, st, t) {
    const b = bounds(st); const [x0, y0] = toScreen([b[0], b[1]]); const [x1, y1] = toScreen([b[2], b[3]]);
    ctx.save();
    ctx.setLineDash([5, 4]); ctx.strokeStyle = getAccent(); ctx.lineWidth = 1.5;
    rr(ctx, x0 - 6, y0 - 6, x1 - x0 + 12, y1 - y0 + 12, 8); ctx.stroke(); ctx.setLineDash([]);
    for (const hd of handles(st)) {
      ctx.beginPath(); ctx.arc(hd.x, hd.y, 6, 0, 7); ctx.fillStyle = '#fff';
      ctx.shadowColor = 'rgba(0,0,0,0.35)'; ctx.shadowBlur = 5; ctx.fill(); ctx.shadowBlur = 0;
      ctx.lineWidth = 2; ctx.strokeStyle = getAccent(); ctx.stroke();
    }
    ctx.restore();
    // the number stays readable on top of its corner handle
    if (st.label != null && ['box', 'ellipse', 'cloud'].includes(st.kind)) {
      ctx.save(); drawBadge(ctx, Object.assign({}, st, { comment: null }), t, p => t.ox + p[0] * t.s, p => t.oy + p[1] * t.s); ctx.restore();
    }
  }
  function drawHover(ctx, st) {
    const b = bounds(st); const [x0, y0] = toScreen([b[0], b[1]]); const [x1, y1] = toScreen([b[2], b[3]]);
    ctx.save(); ctx.strokeStyle = getAccent(); ctx.globalAlpha = 0.65; ctx.lineWidth = 1.5;
    rr(ctx, x0 - 5, y0 - 5, x1 - x0 + 10, y1 - y0 + 10, 8); ctx.stroke(); ctx.restore();
  }
  function handles(st) {
    const out = [];
    if (['box', 'ellipse', 'cloud'].includes(st.kind)) {
      const [x, y, w, h] = rectOf(st); const [X0, Y0] = toScreen([x, y]); const [X1, Y1] = toScreen([x + w, y + h]);
      const xs = [X0, (X0 + X1) / 2, X1], ys = [Y0, (Y0 + Y1) / 2, Y1];
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) if (!(i === 1 && j === 1)) out.push({ x: xs[i], y: ys[j], i, j });
    } else if (st.kind === 'arrow' || st.kind === 'measure') {
      const [ax, ay] = toScreen(st.points[0]); const [bx, by] = toScreen(st.points[st.points.length - 1]);
      out.push({ x: ax, y: ay, end: 0 }, { x: bx, y: by, end: 1 });
    } else if (st.kind === 'ref') {
      const b = bounds(st); const [X0, Y0] = toScreen([b[0], b[1]]); const [X1, Y1] = toScreen([b[2], b[3]]);
      out.push({ x: X0, y: Y0, corner: 1 }, { x: X1, y: Y0, corner: 1 }, { x: X0, y: Y1, corner: 1 }, { x: X1, y: Y1, corner: 1 });
    } else if (st.kind === 'text') {
      const b = bounds(st); const [X1, Y1] = toScreen([b[2], b[3]]); out.push({ x: X1, y: Y1, font: 1 });
    }
    return out;
  }
  function handleAt(x, y) { if (!S.sel) return null; for (const hd of handles(S.sel)) if (hyp(hd.x - x, hd.y - y) <= 9) return hd; return null; }

  function getAccent() { return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#0A84FF'; }

  // ---------------------------------------------------------------- the render loop
  const cv = $('#canvas'); const ctx = cv.getContext('2d');
  function resize() {
    const d = window.devicePixelRatio || 1; S.dpr = d;
    cv.width = Math.round(innerWidth * d); cv.height = Math.round(innerHeight * d);
    layoutChrome(); computeSafe(); clampView(); dirty();
  }
  function dirty() { if (!S.raf) S.raf = requestAnimationFrame(render); }
  function render() {
    S.raf = 0;
    try {
      const d = S.dpr; ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, innerWidth, innerHeight);
      const img = baseImage(); if (!img) return;
      const t = T(); const x = t.ox, y = t.oy, w = t.iw * t.s, hh = t.ih * t.s, rad = 14;
      ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 50; ctx.shadowOffsetY = 18;
      rr(ctx, x, y, w, hh, rad); ctx.fillStyle = '#000'; ctx.fill(); ctx.restore();
      ctx.save(); rr(ctx, x, y, w, hh, rad); ctx.clip(); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(img, x, y, w, hh); ctx.restore();
      ctx.save(); rr(ctx, x + 0.5, y + 0.5, w - 1, hh - 1, rad); ctx.strokeStyle = 'rgba(255,255,255,0.16)'; ctx.lineWidth = 1; ctx.stroke(); ctx.restore();
      if (S.photo) {
        const ref = refByLabel(S.photo); const m = (ref && ref.matches) || [];
        m.forEach((uv, k) => drawMatch(ctx, x + uv[0] * w, y + uv[1] * hh, k + 1, 1, true));
        eraserCursor(t); updateGlass(); return;
      }
      if (S.claude.length && S.showClaude) for (const st of S.claude) drawMark(ctx, st, t);
      for (const st of S.strokes) { if (S.editing && S.editing.st === st) continue; drawMark(ctx, st, t); }
      if (S.live) drawMark(ctx, S.live, t, { live: true });
      if (S.hoverMark && S.hoverMark !== S.sel) drawHover(ctx, S.hoverMark);
      if (S.sel && S.strokes.includes(S.sel)) drawSelection(ctx, S.sel, t);
      if (S.dropHover) {
        ctx.save(); rr(ctx, x, y, w, hh, rad); ctx.fillStyle = `rgba(${getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb')},0.14)`; ctx.fill(); ctx.restore();
      }
      eraserCursor(t);
      updateGlass();
    } catch (e) { report(e, 'render'); }
  }
  // ---------------------------------------------------------------- Liquid Glass: adaptive tint and edge lensing
  const lumCache = new WeakMap();
  function lumGrid(img) {
    if (!img) return null;
    let g = lumCache.get(img); if (g) return g;
    const gw = 96, gh = Math.max(1, Math.round(gw * img.naturalHeight / img.naturalWidth));
    const c = document.createElement('canvas'); c.width = gw; c.height = gh;
    const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(img, 0, 0, gw, gh);
    let d; try { d = x.getImageData(0, 0, gw, gh).data; } catch (e) { return null; }
    const L = new Float32Array(gw * gh); let sum = 0;
    for (let i = 0; i < gw * gh; i++) { const v = (0.2126 * d[i * 4] + 0.7152 * d[i * 4 + 1] + 0.0722 * d[i * 4 + 2]) / 255; L[i] = v; sum += v; }
    g = { gw, gh, L, mean: sum / (gw * gh) }; lumCache.set(img, g); return g;
  }
  function lumUnder(r, t, g, dark) {
    // what the glass sits on: the capture where it overlaps, else the blurred, tinted backdrop
    const back = dark ? g.mean * 0.5 * 0.72 : Math.min(1, g.mean * 1.12) * 0.66 + 0.34;
    let sum = 0, n = 0;
    for (let i = 0; i < 8; i++) for (let j = 0; j < 3; j++) {
      const x = r.left + r.width * (i + 0.5) / 8, y = r.top + r.height * (j + 0.5) / 3;
      const u = (x - t.ox) / (t.s * t.iw), v = (y - t.oy) / (t.s * t.ih);
      sum += (u >= 0 && u < 1 && v >= 0 && v < 1) ? g.L[Math.floor(v * g.gh) * g.gw + Math.floor(u * g.gw)] : back; n++;
    }
    return sum / n;
  }
  const ADAPT = ['#toolbar', '.cluster.tl .glass', '.cluster.tr .glass', '#composer', '#sidebar', '#marks-panel', '#banners .banner', '#mini-bar'];
  let glassSig = '';
  function updateGlass(force) {
    const t = T(); const dark = document.documentElement.dataset.theme === 'dark';
    const sig = [t.s.toFixed(4), Math.round(t.ox), Math.round(t.oy), innerWidth, innerHeight, dark, S.photo, S.optIndex, S.insetL, S.insetR, $('#banners').children.length].join(',');
    if (sig === glassSig && !force) return; glassSig = sig;
    const g = lumGrid(baseImage()); if (!g) return;
    for (const sel of ADAPT) for (const e of document.querySelectorAll(sel)) {
      const r = e.getBoundingClientRect(); if (!r.width) continue;
      const L = lumUnder(r, t, g, dark);
      e.classList.toggle('lit', dark && L > 0.4); e.classList.toggle('dim', !dark && L < 0.46);
    }
  }
  // edge lensing: each bar gets a displacement map the size of itself, so the capture bends at its rim
  const LENS = ['#toolbar', '.cluster.tl .glass', '.cluster.tr .glass', '#composer', '#banners .banner'];
  const SVGNS = 'http://www.w3.org/2000/svg';
  let lensSeq = 0;
  function lensMap(w, h, r, edge) {
    const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d'); const im = x.createImageData(w, h);
    const D = im.data;
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      const cx = i + 0.5 - w / 2, cy = j + 0.5 - h / 2;
      const qx = Math.abs(cx) - (w / 2 - r), qy = Math.abs(cy) - (h / 2 - r);
      const inside = -(Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r);
      let k = inside >= 0 && inside < edge ? Math.pow(1 - inside / edge, 2.4) : 0;
      let gx = Math.sign(cx) * Math.max(qx, 0), gy = Math.sign(cy) * Math.max(qy, 0);
      if (qx <= 0 && qy <= 0) { if (qx > qy) { gx = Math.sign(cx); gy = 0; } else { gx = 0; gy = Math.sign(cy); } }
      const n = Math.hypot(gx, gy) || 1, p = (j * w + i) * 4;
      D[p] = 128 - 127 * k * gx / n; D[p + 1] = 128 - 127 * k * gy / n; D[p + 2] = 128; D[p + 3] = 255;
    }
    x.putImageData(im, 0, 0); return c.toDataURL();
  }
  function updateLens() {
    const on = S.settings.refraction !== false && !S.settings.reduce_transparency;
    let defs = $('#lens-defs');
    if (!defs) { const svg = document.createElementNS(SVGNS, 'svg'); svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.style.position = 'absolute'; defs = document.createElementNS(SVGNS, 'defs'); defs.id = 'lens-defs'; svg.appendChild(defs); document.body.appendChild(svg); }
    for (const sel of LENS) for (const e of document.querySelectorAll(sel)) {
      if (!on) { if (e.dataset.lens) { e.style.backdropFilter = ''; e.style.webkitBackdropFilter = ''; delete e.dataset.lens; delete e.dataset.lensSize; } continue; }
      const w = Math.round(e.offsetWidth), h = Math.round(e.offsetHeight); if (w < 10 || h < 10) continue;
      if (e.dataset.lensSize === w + 'x' + h) continue;
      const id = e.dataset.lens || ('lens' + (++lensSeq));
      let f = document.getElementById(id);
      if (!f) { f = document.createElementNS(SVGNS, 'filter'); f.id = id; f.setAttribute('x', '0'); f.setAttribute('y', '0'); f.setAttribute('width', '100%'); f.setAttribute('height', '100%'); f.setAttribute('color-interpolation-filters', 'sRGB'); defs.appendChild(f); }
      const rad = Math.min(parseFloat(getComputedStyle(e).borderTopLeftRadius) || 0, h / 2, w / 2);
      const edge = Math.min(24, h * 0.42);
      f.innerHTML = '';
      const fi = document.createElementNS(SVGNS, 'feImage'); fi.setAttribute('href', lensMap(w, h, rad, edge)); fi.setAttribute('x', '0'); fi.setAttribute('y', '0');
      fi.setAttribute('width', String(w)); fi.setAttribute('height', String(h)); fi.setAttribute('preserveAspectRatio', 'none'); fi.setAttribute('result', 'm');
      const fd = document.createElementNS(SVGNS, 'feDisplacementMap'); fd.setAttribute('in', 'SourceGraphic'); fd.setAttribute('in2', 'm');
      const bend = Math.round(Math.min(44, h * 0.8));
      fd.setAttribute('scale', String(bend)); fd.setAttribute('data-bend', String(bend)); fd.setAttribute('xChannelSelector', 'R'); fd.setAttribute('yChannelSelector', 'G');
      f.appendChild(fi); f.appendChild(fd);
      const bf = `url(#${id}) blur(9px) saturate(1.9)`;
      e.style.backdropFilter = bf; e.style.webkitBackdropFilter = bf; e.dataset.lens = id; e.dataset.lensSize = w + 'x' + h;
    }
    // drop the filters of bars that no longer exist (banners are rebuilt on every change)
    const used = new Set([...document.querySelectorAll('[data-lens]')].map(e => e.dataset.lens));
    for (const f of [...defs.children]) if (!used.has(f.id)) f.remove();
  }
  const lensSoon = debounce(updateLens, 120);
  const ro = new ResizeObserver(() => lensSoon());

  // opening: the capture comes into focus and each bar condenses out of it as glass (the shapes, delays and the
  // capture's focus pull are in app.css, body.opening). Here the lens itself forms: no bend and no frost at first,
  // then the bend overshoots by about a third and settles like a drop of liquid while the frost comes up.
  const OPEN_MS = 1250;
  let openGen = 0, openHold = null;
  function lensSet(e, bend, blur, sat) {
    if (!e.dataset.lens) return;
    const fd = document.querySelector(`#${e.dataset.lens} feDisplacementMap`);
    if (fd) fd.setAttribute('scale', (bend * (+fd.getAttribute('data-bend') || 0)).toFixed(2));
    const bf = `url(#${e.dataset.lens}) blur(${blur.toFixed(2)}px) saturate(${sat.toFixed(3)})`;
    e.style.backdropFilter = bf; e.style.webkitBackdropFilter = bf;
  }
  function lensAt(ms) {   // [bend, blur px, saturation] for one bar, ms after its own start
    const u = ms / 850;
    if (u <= 0) return [0, 0, 1];
    if (u >= 1) return [1, 9, 1.9];
    const f = Math.min(1, u / 0.75), frost = f * f * (3 - 2 * f);
    return [1 - Math.exp(-5.2 * u) * Math.cos(14 * u), 9 * frost, 1 + 0.9 * frost];
  }
  function openGlass() {
    const b = document.body; b.classList.remove('boot');
    if (S.settings.reduce_motion || matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    const gen = ++openGen; b.classList.add('opening');
    const bars = [...document.querySelectorAll('[data-lens]')];
    for (const e of bars) { e._openDelay = (parseFloat(getComputedStyle(e).getPropertyValue('--d')) || 0) * 1000; lensSet(e, 0, 0, 1); }
    const t0 = performance.now();
    const step = now => {
      if (gen !== openGen) return;
      const t = openHold != null ? openHold : now - t0;
      if (t >= OPEN_MS) { endOpening(); return; }
      for (const e of bars) lensSet(e, ...lensAt(t - e._openDelay));
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    return true;
  }
  function endOpening() {
    openGen++;
    const was = document.body.classList.contains('opening');
    document.body.classList.remove('opening');
    for (const e of document.querySelectorAll('[data-lens]')) lensSet(e, 1, 9, 1.9);
    if (was) { computeSafe(); updateGlass(true); dirty(); }   // anything measured mid-opening saw the bars still scaled
  }

  function eraserCursor(t) {
    if (S.tool !== 'eraser' || !S.mouse.in || S.drag && S.drag.type === 'pan') return;
    const rad = ERASER_R[WIDTHS[S.width].px] * t.s;
    ctx.save(); ctx.beginPath(); ctx.arc(S.mouse.x, S.mouse.y, rad, 0, 7);
    ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,0.45)'; ctx.stroke(); ctx.lineWidth = 1.5; ctx.strokeStyle = '#fff'; ctx.stroke(); ctx.restore();
  }

  // ---------------------------------------------------------------- editing: history, marks
  function snapshot() { S.history.push(clone(strip(S.strokes))); if (S.history.length > 300) S.history.shift(); S.future = []; }
  function strip(list) { return list.map(st => { const o = {}; for (const k in st) if (k[0] !== '_' || k === '_id' || k === '_hit') o[k] = st[k]; return o; }); }
  function changed() { dirty(); renderMarks(); updateCounts(); saveDraft(); syncSidebar(); if (ghostRefs()) renderBanners(); }   // keeps "N here" true
  function undo() {
    if (S.editing) { cancelText(); return; }
    if (!S.history.length) { toast('Nothing to undo'); return; }
    S.future.push(clone(strip(S.strokes))); S.strokes = S.history.pop(); S.sel = null; changed();
  }
  function redo() {
    if (S.editing) commitText();
    if (!S.future.length) return;
    S.history.push(clone(strip(S.strokes))); S.strokes = S.future.pop(); S.sel = null; changed();
  }
  function newStroke(kind, p) {
    const c = COLOURS[S.colour], wpx = WIDTHS[S.width].px;
    const st = { kind, colour: c.name, rgb: c.rgb.slice(), width: kind === 'highlight' ? MARKER_W[wpx] : wpx, points: [p], _id: ++S.ids };
    return st;
  }
  function commitNew(st) {
    if (NUMBERED.has(st.kind) && (st.kind === 'pin' || S.settings.number_marks !== false)) st.label = nextLabel();
    if (st.kind === 'text') measureText(st);
    snapshot(); S.strokes.push(st); changed();
    if (NUMBERED.has(st.kind) || st.kind === 'arrow' || st.kind === 'text') lookUnder(st);
    if (st.kind === 'measure') runMeasure(st);
  }
  function removeMark(st) {
    const i = S.strokes.indexOf(st); if (i < 0) return;
    snapshot(); S.strokes.splice(i, 1); if (S.sel === st) S.sel = null; if (S.hoverMark === st) S.hoverMark = null; changed();
  }
  function duplicateMark(st) {
    const c = clone(st); c._id = ++S.ids; c.points = c.points.map(q => [r1(q[0] + 24), r1(q[1] + 24)]);
    if (typeof c.label === 'number') c.label = nextLabel();
    if (c.kind === 'ref') { toast('References can\'t be duplicated'); return; }
    snapshot(); S.strokes.push(c); S.sel = c; changed();
  }
  function toFront(st) { const i = S.strokes.indexOf(st); if (i < 0) return; snapshot(); S.strokes.splice(i, 1); S.strokes.push(st); changed(); }
  function recolour(st, k) { snapshot(); st.colour = COLOURS[k].name; st.rgb = COLOURS[k].rgb.slice(); changed(); }
  function clearAll() {
    if (S.editing) cancelText();
    if (!S.strokes.length) return;
    snapshot(); S.strokes = []; S.sel = null; changed(); toast('Cleared. Ctrl+Z brings them back', 'undo');
  }
  function select(st) { S.sel = st; dirty(); renderMarks(); miniBar(); }

  // what's under a mark (for the marks list), and real measurements: Rhino answers both
  function lookUnder(st) {
    let p;
    if (st.kind === 'pin') p = st.points[0];
    else if (st.kind === 'arrow') p = st.points[st.points.length - 1];
    else if (st.kind === 'text') p = [st.points[0][0] + (st.size || [0, 0])[0] / 2, st.points[0][1] + (st.size || [0, 0])[1] / 2];
    else { const [x, y, w, h] = rectOf(st); p = [x + w / 2, y + h / 2]; }
    Bridge.call('pick', { x: p[0], y: p[1] }).then(r => { st._hit = r && r.hit || null; renderMarks(); }).catch(() => { });
  }
  function runMeasure(st) {
    const a = st.points[0], b = st.points[st.points.length - 1];
    Bridge.call('measure', { a, b }).then(r => {
      st.measure = r || { text: 'no depth here' }; dirty(); renderMarks();
      if (r && r.method === 'none') toast('Nothing under one end, so no real distance. Put both ends on the model.', 'warn');
    }).catch(e => { st.measure = { text: 'measure failed' }; dirty(); });
  }

  // ---------------------------------------------------------------- the eraser
  function densify(pts, step) {
    step = step || 1.5; const out = [pts[0]];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i]; const n = Math.floor(hyp(b[0] - a[0], b[1] - a[1]) / step);
      for (let k = 1; k <= n; k++) { const f = k / (n + 1); out.push([r1(a[0] + f * (b[0] - a[0])), r1(a[1] + f * (b[1] - a[1]))]); }
      out.push(b);
    }
    return out;
  }
  function eraseStroke(st, centres, r) {
    const k = st.kind, pts = st.points, reach = r + (st.width || 6) / 2;
    if (k === 'ref') return null;
    const near = (p, d) => centres.some(c => hyp(p[0] - c[0], p[1] - c[1]) <= d);
    if (k === 'pen' || k === 'highlight') {
      if (pts.length === 1) return near(pts[0], reach) ? [] : null;
      const dense = densify(pts); const keep = dense.map(p => !near(p, reach));
      if (keep.every(Boolean)) return null;
      const pieces = []; let run = [];
      for (let i = 0; i <= dense.length; i++) {
        if (i < dense.length && keep[i]) { run.push(dense[i]); continue; }
        if (run.length) {
          let len = 0; for (let j = 1; j < run.length; j++) len += hyp(run[j][0] - run[j - 1][0], run[j][1] - run[j - 1][1]);
          if (len >= 3) { const piece = Object.assign({}, st, { points: run, _id: ++S.ids }); delete piece.closed; delete piece._hit; pieces.push(piece); }
          run = [];
        }
      }
      return pieces;
    }
    let hit = false;
    if (k === 'box' || k === 'cloud') {
      const [x, y, w, h] = rectOf(st); const c4 = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
      hit = centres.some(c => [0, 1, 2, 3].some(i => segDist(c, c4[i], c4[(i + 1) % 4]) <= reach));
    } else if (k === 'ellipse') {
      const [x, y, w, h] = rectOf(st); const rx = w / 2 || 1, ry = h / 2 || 1;
      hit = centres.some(c => { const dx = (c[0] - x - rx) / rx, dy = (c[1] - y - ry) / ry; return Math.abs(Math.sqrt(dx * dx + dy * dy) - 1) * Math.min(rx, ry) <= reach; });
    } else if (k === 'arrow' || k === 'measure') hit = centres.some(c => segDist(c, pts[0], pts[pts.length - 1]) <= reach + (st.width || 6));
    else if (k === 'pin') hit = near(pts[0], r + pinRadius(st));
    else if (k === 'match') hit = near(pts[0], r + 10);
    else if (k === 'text') { const b = bounds(st); hit = centres.some(c => hyp(c[0] - clamp(c[0], b[0], b[2]), c[1] - clamp(c[1], b[1], b[3])) <= r); }
    else return null;
    return hit ? [] : null;
  }
  function eraseAlong(a, b) {
    const r = ERASER_R[WIDTHS[S.width].px];
    const n = Math.max(1, Math.floor(hyp(b[0] - a[0], b[1] - a[1]) / (0.4 * r)));
    const centres = []; for (let k = 0; k <= n; k++) centres.push([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n]);
    const out = []; let any = false;
    for (const st of S.strokes) { const left = eraseStroke(st, centres, r); if (left === null) out.push(st); else { out.push.apply(out, left); any = true; } }
    if (any) {
      if (!S.drag.changed) { snapshot(); S.drag.changed = true; }
      S.strokes = out; if (S.sel && !out.includes(S.sel)) S.sel = null; changed();
    }
  }

  // ---------------------------------------------------------------- photo match
  function refByLabel(label) { return S.strokes.find(st => st.kind === 'ref' && st.label === label) || null; }
  // while the user places model points the photo cards step aside (an outline, clicked through): a card over the
  // part the user is matching used to hide it, and a click on it opened the photo instead of placing the point
  function ghostRefs() { return S.tool === 'match' && !S.photo; }
  function openPhoto(label) {
    commitText(); const ref = refByLabel(label); if (!ref || !S.refImgs[label]) return;
    S.photo = S.photoLast = label; S.sel = null;
    const im = S.refImgs[label]; resetView(S.pview, im.naturalWidth, im.naturalHeight);
    setTool('match'); renderBanners(); zoomLabel(); dirty();
  }
  function closePhoto() { S.photo = null; renderBanners(); zoomLabel(); dirty(); }
  function modelMatches() { return S.strokes.filter(st => st.kind === 'match'); }
  function addMatch(p) {
    if (S.photo) {
      const [iw, ih] = dims(); snapshot(); const ref = refByLabel(S.photo);
      (ref.matches = ref.matches || []).push([Math.round(p[0] / iw * 1e5) / 1e5, Math.round(p[1] / ih * 1e5) / 1e5]);
      const n = ref.matches.length, have = modelMatches().length;
      toast(`Photo point ${n}. ` + (have < n ? 'Tab: back to the model to click it there' : `${n} pairs so far`), 'match');
      changed(); renderBanners(); return;
    }
    const st = newStroke('match', p); st.colour = 'cyan'; st.rgb = MATCH_RGB.slice();
    st.label = 1 + Math.max(0, ...modelMatches().map(m => m.label || 0)); st.ref = S.photoLast;
    snapshot(); S.strokes.push(st); changed();
    const ref = refByLabel(S.photoLast); const n = ref && ref.matches ? ref.matches.length : 0, k = st.label;
    if (!S.photoLast) toast(`Model point ${k}. Open a reference photo first (double-click it) to pair it`, 'match');
    else if (k < n) toast(`Model point ${k}. ${n - k} photo point${n - k === 1 ? '' : 's'} still to pair`, 'match');
    else if (k === n) toast(`${k} pairs. Aim for 6 or more, spread out and not all on one plane`, 'match');
    else toast(`Model point ${k} has no photo point yet. Tab opens the photo`, 'match');
  }
  function removePhotoPoint(p, radius) {
    const [iw, ih] = dims(); const ref = refByLabel(S.photo); const pts = (ref && ref.matches) || [];
    const near = pts.map((uv, k) => hyp(uv[0] * iw - p[0], uv[1] * ih - p[1]) <= radius ? k : -1).filter(k => k >= 0);
    if (!near.length) return false;
    snapshot(); ref.matches = pts.filter((uv, k) => !near.includes(k)); changed(); renderBanners(); return true;
  }

  // ---------------------------------------------------------------- text notes (a real text box over the canvas)
  const ed = $('#text-editor');
  function beginText(st, p) {
    commitText();
    if (!st) {
      st = newStroke('text', p); st.text = ''; st.font_px = TEXT_PX[st.width]; measureText(st);
      S.editing = { st, isNew: true };
    } else {
      S.editing = { st, isNew: false, before: clone(strip(S.strokes)), orig: clone(st) };
    }
    S.sel = null; placeEditor(); ed.classList.remove('hidden'); ed.value = st.text || ''; autosizeEditor();
    setTimeout(() => { ed.focus(); ed.setSelectionRange(ed.value.length, ed.value.length); }, 0); dirty();
  }
  function placeEditor() {
    if (!S.editing) return;
    const st = S.editing.st, t = T(), L = textLayout(st, t.s), [x, y] = toScreen(st.points[0]);
    Object.assign(ed.style, { left: x + 'px', top: y + 'px', font: L.font, lineHeight: L.lh + 'px', padding: `${L.pady}px ${L.padx}px`,
      color: css(st.rgb), borderRadius: Math.min(L.lh / 2 + L.pady, Math.max(3, 0.3 * L.px)) + 'px' });
    ed.style.background = lum(st.rgb) < 0.2 ? 'rgba(255,255,255,0.92)' : 'rgba(18,18,22,0.84)';
  }
  function autosizeEditor() {
    if (!S.editing) return;
    const st = S.editing.st, t = T(); const probe = Object.assign({}, st, { text: ed.value || ' ' }); const L = textLayout(probe, t.s);
    ed.style.width = (L.tw + 2 * L.padx + 6) + 'px'; ed.style.height = (L.th + 2 * L.pady) + 'px';
  }
  ed.addEventListener('input', autosizeEditor);
  function commitText() {
    const e = S.editing; if (!e) return; S.editing = null; ed.classList.add('hidden');
    const st = e.st; st.text = ed.value.replace(/\s+$/, '').replace(/^\n+/, ''); measureText(st); keepInside(st);
    if (e.isNew) { if (st.text) commitNew(st); }
    else if (JSON.stringify(st) !== JSON.stringify(e.orig)) {
      S.history.push(e.before); S.future = [];
      if (!st.text) S.strokes.splice(S.strokes.indexOf(st), 1);
      changed();
    }
    dirty(); cv.focus();
  }
  function cancelText() {
    const e = S.editing; if (!e) return; S.editing = null; ed.classList.add('hidden');
    if (!e.isNew) { for (const k of Object.keys(e.st)) delete e.st[k]; Object.assign(e.st, e.orig); }
    dirty(); cv.focus();
  }
  function keepInside(st) {
    const [x, y] = st.points[0], [w, h] = st.size || [0, 0];
    st.points = [[r1(clamp(x, 0, Math.max(0, S.iw - w))), r1(clamp(y, 0, Math.max(0, S.ih - h)))]];
  }

  // ---------------------------------------------------------------- comments on marks
  function openComment(st) {
    closeComment(true); if (!st) return;
    const b = bounds(st); const [x1, y0] = toScreen([b[2], b[1]]); const [x0] = toScreen([b[0], b[1]]);
    const box = el(`<div class="glass" id="comment-editor"><div class="ttl">${icon('comment', 's14')}<span>${esc(markTitle(st))}</span></div>
      <textarea rows="2" placeholder="What about it? Claude reads this with the mark"></textarea>
      <div class="hint">Enter saves  ·  Shift+Enter new line  ·  Esc cancels</div></div>`);
    document.body.appendChild(box);
    const ta = box.querySelector('textarea'); ta.value = st.comment || '';
    let left = x1 + 14; if (left + 300 > innerWidth - 12) left = Math.max(12, x0 - 314);
    box.style.left = left + 'px'; box.style.top = clamp(y0 - 6, 70, innerHeight - 200) + 'px';
    S.comment = { st, box, ta, before: st.comment || '' };
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); closeComment(true); }
      else if (e.key === 'Escape') { e.preventDefault(); closeComment(false); }
      e.stopPropagation();
    });
    setTimeout(() => ta.focus(), 0);
  }
  function closeComment(save) {
    const c = S.comment; if (!c) return; S.comment = null;
    const val = c.ta.value.trim();
    if (save && val !== c.before) { snapshot(); if (val) c.st.comment = val; else delete c.st.comment; changed(); }
    c.box.remove(); cv.focus(); miniBar();
  }
  function markTitle(st) {
    const n = st.label != null ? (typeof st.label === 'number' ? st.label + ' · ' : st.label + ' · ') : '';
    return n + (KIND_NAME[st.kind] || st.kind);
  }

  // a small glass bar over the selected mark
  function miniBar() {
    const old = $('#mini-bar'); if (old) old.remove();
    const st = S.sel; if (!st || S.drag || S.comment || S.tool !== 'select') return;
    const b = bounds(st); const [x0, y0] = toScreen([b[0], b[1]]); const [x1] = toScreen([b[2], b[3]]);
    const bar = el(`<div class="glass" id="mini-bar">
      <button class="btn" data-a="comment">${icon('comment', 's16')}<span>${st.comment ? 'Edit comment' : 'Comment'}</span></button>
      <button class="btn" data-a="dup" data-tip="Duplicate">${icon('duplicate', 's16')}</button>
      <button class="btn" data-a="front" data-tip="Bring to front">${icon('front', 's16')}</button>
      <button class="btn" data-a="del" data-tip="Delete" data-key="Del">${icon('trash', 's16')}</button></div>`);
    document.body.appendChild(bar);
    const w = bar.offsetWidth; let left = (x0 + x1) / 2 - w / 2; left = clamp(left, 12, innerWidth - w - 12);
    let top = y0 - 58; if (top < S.safe.y - 10) top = toScreen([0, b[3]])[1] + 14;
    bar.style.left = left + 'px'; bar.style.top = top + 'px';
    bar.addEventListener('pointerdown', e => e.stopPropagation());
    bar.addEventListener('click', e => {
      const a = e.target.closest('[data-a]'); if (!a) return;
      const act = a.dataset.a;
      if (act === 'comment') openComment(st); else if (act === 'dup') duplicateMark(st); else if (act === 'front') toFront(st); else if (act === 'del') removeMark(st);
      miniBar();
    });
  }

  // ---------------------------------------------------------------- pointer input on the canvas
  function setCursor(c) { if (cv.style.cursor !== c) cv.style.cursor = c; }
  cv.addEventListener('pointerdown', e => { try { onDown(e); } catch (err) { report(err, 'pointerdown'); } });
  window.addEventListener('pointermove', e => { try { onMove(e); } catch (err) { report(err, 'pointermove'); } });
  window.addEventListener('pointerup', e => { try { onUp(e); } catch (err) { report(err, 'pointerup'); } });
  cv.addEventListener('pointerleave', () => { S.mouse.in = false; hideHover(); if (S.tool === 'eraser') dirty(); if (S.hoverMark) { S.hoverMark = null; dirty(); } });
  cv.addEventListener('pointerenter', () => { S.mouse.in = true; });

  function onDown(e) {
    closeMenus(); hideTip();
    if (S.comment) closeComment(true);
    if (S.editing) { commitText(); if (e.button === 0 && S.tool === 'text') return; }
    cv.focus(); S.mouse.x = e.clientX; S.mouse.y = e.clientY; S.mouse.in = true; hideHover();
    const t = T(); const p = toImg(e.clientX, e.clientY);
    if (e.button === 1 || (e.button === 0 && S.spaceDown)) {
      S.drag = { type: 'pan', x: e.clientX, y: e.clientY, v: Object.assign({}, vstate()) }; cv.setPointerCapture(e.pointerId); setCursor('grabbing'); e.preventDefault(); return;
    }
    if (e.button !== 0) return;
    cv.setPointerCapture(e.pointerId);
    if (S.photo) {
      if (S.tool === 'eraser') removePhotoPoint(p, ERASER_R[WIDTHS[S.width].px]); else addMatch(p);
      return;
    }
    const hd = handleAt(e.clientX, e.clientY);
    if (hd && S.sel) { S.drag = { type: 'resize', st: S.sel, hd, orig: clone(S.sel), moved: false, start: p }; return; }
    const tool = S.tool;
    if (tool !== 'eraser') {
      const b = hitBadge(p, t);
      if (b) { if (tool === 'select') select(b); startMove(b, p); return; }
      const mv = hitTest(p, 2 / t.s, st => (st.kind === 'ref' && !ghostRefs()) || st.kind === 'text');
      if (mv) {
        if (tool === 'match' && mv.kind === 'ref') { openPhoto(mv.label); return; }
        if (tool === 'text' && mv.kind === 'text') { beginText(mv); return; }
        if (tool === 'select') select(mv);
        startMove(mv, p); return;
      }
    }
    switch (tool) {
      case 'select': {
        const m = hitTest(p, 6 / t.s);
        select(m); if (m) startMove(m, p); else S.drag = { type: 'none' };
        return;
      }
      case 'eraser': S.drag = { type: 'erase', changed: false, last: p }; eraseAlong(p, p); return;
      case 'pin': commitNew(newStroke('pin', p)); return;
      case 'text': beginText(null, p); return;
      case 'match': addMatch(p); return;
      default: {
        const kind = KIND_OF_TOOL[tool] || tool;
        S.live = newStroke(kind, p);
        if (['box', 'ellipse', 'cloud', 'arrow', 'measure'].includes(kind)) S.live.points = [p, p];
        S.drag = { type: 'draw' }; S.shiftBase = null; dirty();
      }
    }
  }
  function startMove(st, p) { S.drag = { type: 'move', st, start: p, orig: clone(st), moved: false }; }
  // while a mark is being drawn or moved, the glass drops its lens so every frame goes to the stroke
  cv.addEventListener('pointerdown', () => { if (S.drag && S.drag.type !== 'none') document.body.classList.add('busy'); });
  window.addEventListener('pointerup', () => document.body.classList.remove('busy'));

  function onMove(e) {
    S.mouse.x = e.clientX; S.mouse.y = e.clientY;
    const overCanvas = e.target === cv;
    const p = toImg(e.clientX, e.clientY);
    const t = T();
    if (!S.drag) {
      if (!overCanvas) return;
      S.mouse.in = true;
      if (S.spaceDown) { setCursor('grab'); return; }
      if (S.photo) { setCursor(S.tool === 'eraser' ? 'none' : 'crosshair'); if (S.tool === 'eraser') dirty(); return; }
      const hd = handleAt(e.clientX, e.clientY);
      let hover = null;
      if (hd) setCursor(hd.end != null ? 'move' : (hd.font || hd.corner ? 'nwse-resize' : ((hd.i === 1) ? 'ns-resize' : (hd.j === 1 ? 'ew-resize' : ((hd.i === hd.j) ? 'nwse-resize' : 'nesw-resize')))));
      else if (S.tool === 'eraser') { setCursor('none'); dirty(); }
      else if (hitBadge(p, t)) { setCursor('move'); hover = hitBadge(p, t); }
      else {
        const mv = hitTest(p, 2 / t.s, st => (st.kind === 'ref' && !ghostRefs()) || st.kind === 'text');
        if (S.tool === 'select') { hover = hitTest(p, 6 / t.s); setCursor(hover ? 'move' : 'default'); }
        else if (mv) { setCursor(S.tool === 'text' && mv.kind === 'text' ? 'text' : (S.tool === 'match' && mv.kind === 'ref' ? 'zoom-in' : 'move')); hover = mv; }
        else setCursor(S.tool === 'text' ? 'text' : 'crosshair');
      }
      if (hover !== S.hoverMark) { S.hoverMark = hover; dirty(); }
      scheduleHover(e.clientX, e.clientY, p);
      return;
    }
    const d = S.drag;
    if (d.type === 'pan') {
      const v = vstate(); v.cx = d.v.cx - (e.clientX - d.x) / t.s; v.cy = d.v.cy - (e.clientY - d.y) / t.s; clampView(); dirty(); placeEditor(); return;
    }
    if (d.type === 'move') {
      if (!d.moved) { if (hyp(p[0] - d.start[0], p[1] - d.start[1]) * t.s < 3) return; snapshot(); d.moved = true; const mb = $('#mini-bar'); if (mb) mb.remove(); }
      const b0 = bounds(d.orig); let dx = p[0] - d.start[0], dy = p[1] - d.start[1];
      dx = clamp(dx, -b0[0] - (b0[2] - b0[0]) * 0.5, S.iw - b0[2] + (b0[2] - b0[0]) * 0.5);
      dy = clamp(dy, -b0[1] - (b0[3] - b0[1]) * 0.5, S.ih - b0[3] + (b0[3] - b0[1]) * 0.5);
      translate(d.st, dx, dy, d.orig); dirty(); return;
    }
    if (d.type === 'resize') {
      if (!d.moved) { snapshot(); d.moved = true; const mb = $('#mini-bar'); if (mb) mb.remove(); }
      applyResize(d, p, e.shiftKey); dirty(); return;
    }
    if (d.type === 'erase') { eraseAlong(d.last, p); d.last = p; dirty(); return; }
    if (d.type === 'draw' && S.live) {
      const st = S.live, pts = st.points, k = st.kind;
      if (k === 'pen' || k === 'highlight') {
        if (e.shiftKey) { if (!S.shiftBase) S.shiftBase = pts.slice(); st.points = S.shiftBase.concat([p]); }
        else { if (S.shiftBase) S.shiftBase = null; const last = pts[pts.length - 1]; if (hyp(p[0] - last[0], p[1] - last[1]) * t.s >= 1.2) pts.push(p); }
      } else if (k === 'arrow' || k === 'measure') {
        let q = p; const a = pts[0];
        if (e.shiftKey) { const ang = Math.round(Math.atan2(p[1] - a[1], p[0] - a[0]) / (Math.PI / 12)) * (Math.PI / 12); const dd = hyp(p[0] - a[0], p[1] - a[1]); q = [r1(a[0] + dd * Math.cos(ang)), r1(a[1] + dd * Math.sin(ang))]; }
        st.points = [a, q];
      } else {
        let q = p; const a = pts[0];
        if (e.shiftKey) { const side = Math.max(Math.abs(p[0] - a[0]), Math.abs(p[1] - a[1])); q = [r1(a[0] + Math.sign(p[0] - a[0] || 1) * side), r1(a[1] + Math.sign(p[1] - a[1] || 1) * side)]; }
        st.points = [a, q];
      }
      dirty();
    }
  }
  function applyResize(d, p, shift) {
    const st = d.st, o = d.orig, hd = d.hd;
    if (['box', 'ellipse', 'cloud'].includes(st.kind)) {
      let [x, y, w, h] = rectOf(o); let x0 = x, y0 = y, x1 = x + w, y1 = y + h;
      if (hd.i === 0) x0 = Math.min(p[0], x1 - 6); if (hd.i === 2) x1 = Math.max(p[0], x0 + 6);
      if (hd.j === 0) y0 = Math.min(p[1], y1 - 6); if (hd.j === 2) y1 = Math.max(p[1], y0 + 6);
      st.points = [[r1(x0), r1(y0)], [r1(x1), r1(y1)]];
    } else if (st.kind === 'arrow' || st.kind === 'measure') {
      const pts = o.points.map(q => q.slice()); pts[hd.end ? pts.length - 1 : 0] = p; st.points = [pts[0], pts[pts.length - 1]];
      if (st.kind === 'measure') { delete st.measure; }
    } else if (st.kind === 'ref') {
      const [cx, cy] = o.points[0], [w, h] = o.size;
      const k = Math.max(Math.abs(p[0] - cx) / (w / 2), Math.abs(p[1] - cy) / (h / 2));
      const nw = w * k, nh = h * k; if (Math.min(nw, nh) >= 40 && Math.max(nw, nh) <= 0.95 * Math.max(S.iw, S.ih)) st.size = [r1(nw), r1(nh)];
    } else if (st.kind === 'text') {
      const b = bounds(o); const k = Math.max(0.2, hyp(p[0] - b[0], p[1] - b[1]) / (hyp(b[2] - b[0], b[3] - b[1]) || 1));
      st.font_px = r1(clamp((o.font_px || 30) * k, 12, 0.25 * S.ih)); measureText(st);
    }
  }
  function onUp(e) {
    const d = S.drag; if (!d) return; S.drag = null;
    try { cv.releasePointerCapture(e.pointerId); } catch (x) { /* already released */ }
    if (d.type === 'pan') { setCursor(S.spaceDown ? 'grab' : 'crosshair'); return; }
    if (d.type === 'move') {
      if (d.moved) { changed(); if (d.st.kind === 'measure') { delete d.st.measure; runMeasure(d.st); } else if (NUMBERED.has(d.st.kind)) lookUnder(d.st); }
      miniBar(); return;
    }
    if (d.type === 'resize') { if (d.moved) { changed(); if (d.st.kind === 'measure') runMeasure(d.st); else if (NUMBERED.has(d.st.kind)) lookUnder(d.st); } miniBar(); return; }
    if (d.type === 'erase') { dirty(); return; }
    if (d.type === 'draw' && S.live) {
      const st = S.live; S.live = null; S.shiftBase = null;
      if (['arrow', 'box', 'ellipse', 'cloud', 'measure'].includes(st.kind)) {
        const a = st.points[0], b = st.points[st.points.length - 1];
        if (hyp(b[0] - a[0], b[1] - a[1]) * T().s < 5) { dirty(); return; }
      }
      commitNew(st);
    }
  }
  cv.addEventListener('dblclick', e => {
    const p = toImg(e.clientX, e.clientY); const t = T(); if (S.photo) return;
    const m = hitBadge(p, t) || hitTest(p, 6 / t.s, ghostRefs() ? (st => st.kind !== 'ref') : null);
    if (!m) return;
    S.drag = null;
    if (m.kind === 'text') beginText(m); else if (m.kind === 'ref') openPhoto(m.label); else { select(m); openComment(m); }
  });
  cv.addEventListener('contextmenu', e => {
    e.preventDefault(); if (S.drag) return;
    const p = toImg(e.clientX, e.clientY); const t = T();
    if (S.photo) { if (!removePhotoPoint(p, 25 / t.s)) undo(); return; }
    const m = hitBadge(p, t) || hitTest(p, 6 / t.s);
    if (m) { select(m); markMenu(m, e.clientX, e.clientY); } else canvasMenu(e.clientX, e.clientY);
  });
  cv.addEventListener('wheel', e => {
    e.preventDefault(); hideHover();
    const p = toImg(e.clientX, e.clientY); const t = T();
    if (!e.ctrlKey && !S.photo) {
      const it = hitTest(p, 2 / t.s, st => st.kind === 'ref' || st.kind === 'text');
      if (it) { wheelResize(it, e.deltaY < 0 ? 1.1 : 1 / 1.1); return; }
    }
    if (e.shiftKey && !e.ctrlKey) { const v = vstate(); v.cx += e.deltaY / t.s; clampView(); dirty(); return; }
    const step = e.deltaMode === 1 ? e.deltaY * 30 : e.deltaY;
    zoomAt(Math.exp(-step * (e.ctrlKey ? 0.006 : 0.0018)), e.clientX, e.clientY); placeEditor();
  }, { passive: false });
  let wheelItem = null, wheelAt = 0;
  function wheelResize(it, k) {
    if (wheelItem !== it || performance.now() - wheelAt > 800) snapshot();
    wheelItem = it; wheelAt = performance.now();
    if (it.kind === 'text') { const f = (it.font_px || 30) * k; if (f >= 12 && f <= 0.25 * S.ih) { it.font_px = r1(f); measureText(it); } }
    else { const [w, h] = it.size; if (Math.min(w, h) * k >= 40 && Math.max(w, h) * k <= 0.95 * Math.max(S.iw, S.ih)) it.size = [r1(w * k), r1(h * k)]; }
    changed();
  }

  // ---------------------------------------------------------------- what is under the cursor (Rhino's pick)
  const tag = $('#hover-tag');
  function scheduleHover(x, y, p) {
    const hp = S.hoverPick; clearTimeout(hp.timer);
    if (!S.settings.hover_inspect || hp.paused || S.photo || S.drag || !insideImage(x, y)) { hideHover(); return; }
    hp.timer = setTimeout(() => {
      const seq = ++hp.seq; const t0 = performance.now();
      Bridge.call('pick', { x: p[0], y: p[1] }).then(r => {
        const ms = performance.now() - t0;
        if (ms > 450) { if (++hp.slow >= 3) { hp.paused = true; toast('The model is too heavy to look under the cursor live, so that is paused', 'warn'); } } else hp.slow = 0;
        if (seq !== hp.seq || S.drag || !S.mouse.in) return;
        showHover(r && r.hit, x, y);
      }).catch(() => { });
    }, 140);
  }
  function showHover(hit, x, y) {
    if (!hit || hit.error) { hideHover(); return; }
    tag.querySelector('.h1').textContent = hit.name ? hit.name : hit.type;
    tag.querySelector('.h2').textContent = (hit.name ? hit.type + '  ·  ' : '') + (hit.layer || 'no layer');
    let lx = x + 18, ly = y + 18; const w = tag.offsetWidth || 220;
    if (lx + w > innerWidth - 10) lx = x - w - 14; if (ly + 50 > innerHeight - 10) ly = y - 56;
    tag.style.left = lx + 'px'; tag.style.top = ly + 'px'; tag.classList.add('show');
  }
  function hideHover() { clearTimeout(S.hoverPick.timer); S.hoverPick.seq++; tag.classList.remove('show'); }

  // ---------------------------------------------------------------- keyboard
  window.addEventListener('keydown', e => { try { onKey(e); } catch (err) { report(err, 'keydown'); } }, true);
  window.addEventListener('keyup', e => { if (e.key === ' ') { S.spaceDown = false; if (!S.drag) setCursor('crosshair'); } });
  function onKey(e) {
    if (helpKey && e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeHelp(); return; }
    const tgt = e.target, k = e.key, ctrl = e.ctrlKey || e.metaKey;
    const typing = tgt && (tgt.tagName === 'TEXTAREA' || tgt.tagName === 'INPUT');
    const layer = $('#layer');
    if (layer.children.length) {
      if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenus(); return; }
      const sheet = layer.querySelector('.sheet');
      if (sheet && k === 'Enter' && !typing) { const pr = sheet.querySelector('.btn.primary, .btn.danger'); if (pr) { e.preventDefault(); pr.click(); } return; }
      if (!sheet && !typing) {
        const items = [...layer.querySelectorAll('.menu .mi')]; if (items.length && (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter')) {
          e.preventDefault(); let i = items.findIndex(x => x.classList.contains('focus'));
          if (k === 'Enter') { if (i >= 0) items[i].click(); return; }
          if (i >= 0) items[i].classList.remove('focus'); i = (i + (k === 'ArrowDown' ? 1 : -1) + items.length) % items.length; items[i].classList.add('focus'); return;
        }
      }
      if (sheet) return;
    }
    if (engPop && k === 'Escape') { e.preventDefault(); closeEnginePop(); return; }
    if (typing) {
      if (tgt.id === 'note') {
        if (k === 'Enter' && ctrl && e.shiftKey) { e.preventDefault(); send('queue'); }
        else if (k === 'Enter' && !e.shiftKey) { e.preventDefault(); send('send'); }
        else if (k === 'Escape') { e.preventDefault(); tgt.blur(); cv.focus(); }
        else if (k === 'ArrowUp' && !tgt.value.trim()) { e.preventDefault(); recall(1); }
        else if (k === 'ArrowDown' && S.recallIndex >= 0) { e.preventDefault(); recall(-1); }
      } else if (tgt.id === 'text-editor') {
        if (k === 'Enter' && !e.shiftKey) { e.preventDefault(); commitText(); }
        else if (k === 'Escape') { e.preventDefault(); cancelText(); }
        e.stopPropagation();
      }
      return;
    }
    if (k === ' ') { S.spaceDown = true; setCursor('grab'); e.preventDefault(); return; }
    if (ctrl && e.shiftKey && k === 'Enter') send('queue');
    else if (ctrl && (k === 'z' || k === 'Z')) { if (e.shiftKey) redo(); else undo(); }
    else if (ctrl && (k === 'y' || k === 'Y')) redo();
    else if (ctrl && (k === 's' || k === 'S')) saveAs();
    else if (ctrl && (k === 'c' || k === 'C')) copyImage();
    else if (ctrl && (k === 'v' || k === 'V')) pasteImage();
    else if (ctrl && (k === 'f' || k === 'F')) examples();
    else if (ctrl && k === '0') fitView();
    else if (ctrl && k === '1') actualSize();
    else if (ctrl && (k === '=' || k === '+')) zoomBy(1.25);
    else if (ctrl && k === '-') zoomBy(0.8);
    else if (ctrl && (k === 'a' || k === 'A')) { $('#note').focus(); }
    else if (ctrl && k === 'Delete') clearAll();
    else if (k === 'Enter') send('send');
    else if (k === 'Escape') escape();
    else if (k === 'Delete' || k === 'Backspace') {
      const target = S.sel || (S.mouse.in ? hitTest(toImg(S.mouse.x, S.mouse.y), 6 / T().s) : null);
      if (target) removeMark(target);
    }
    else if (k === 'Tab') { if (S.photo) closePhoto(); else if (S.photoLast) openPhoto(S.photoLast); else return; }
    else if (S.options.length && (k === 'ArrowLeft' || k === 'ArrowRight')) flipOption(S.optIndex + (k === 'ArrowRight' ? 1 : -1));
    else if (S.sel && k.startsWith('Arrow')) {
      const st = S.sel, n = e.shiftKey ? 10 : 1, dx = k === 'ArrowLeft' ? -n : k === 'ArrowRight' ? n : 0, dy = k === 'ArrowUp' ? -n : k === 'ArrowDown' ? n : 0;
      snapshot(); translate(st, dx, dy, clone(st)); changed(); miniBar();
    }
    else if (!ctrl && !e.altKey && (k === 'h' || k === 'H') && S.claude.length) toggleClaude();
    else if (!ctrl && !e.altKey && k === 'M') setTool('match');
    else if (!ctrl && !e.altKey && TOOLS.some(x => x.key === k)) setTool(TOOLS.find(x => x.key === k).id);
    else if (!ctrl && /^[1-7]$/.test(k)) setColour(+k - 1);
    else if (k === '[') setWidth(S.width - 1);
    else if (k === ']') setWidth(S.width + 1);
    else if (k === '?' || k === 'F1') helpSheet();
    else return;
    e.preventDefault();
  }
  function escape() {
    if (S.live) { S.live = null; S.drag = null; dirty(); return; }
    if (S.comment) { closeComment(false); return; }
    if (S.sel) { select(null); return; }
    if (S.photo) { closePhoto(); return; }
    cancel();
  }

  // ---------------------------------------------------------------- toolbar
  function buildToolbar() {
    const tools = $('#tools');
    for (const tl of TOOLS) {
      const b = el(`<button class="btn" data-tool="${tl.id}" data-tip="${esc(tl.name)}" data-sub="${esc(tl.sub)}" data-key="${tl.key === 'M' ? 'Shift+M' : tl.key.toUpperCase()}">${icon(tl.icon)}</button>`);
      b.addEventListener('click', () => setTool(tl.id));
      tools.appendChild(b);
    }
    const more = el(`<button class="btn" id="more-tools" data-tip="More tools">${icon('more')}</button>`);
    more.addEventListener('click', () => moreToolsMenu(more));
    tools.appendChild(more);
    const sw = $('#swatches');
    COLOURS.forEach((c, i) => {
      const b = el(`<button class="btn swatch" style="--c:${css(c.rgb)}" data-tip="${c.name[0].toUpperCase() + c.name.slice(1)}" data-key="${i + 1}"><i></i></button>`);
      b.addEventListener('click', () => setColour(i)); sw.appendChild(b);
    });
    const ws = $('#widths');
    WIDTHS.forEach((w, i) => {
      const b = el(`<button class="btn" data-tip="${w.name}" data-sub="Line width, text size and eraser size" data-key="${i === 0 ? '[' : i === 2 ? ']' : ''}"><span class="wline" style="height:${[1.6, 3.4, 6][i]}px"></span></button>`);
      b.addEventListener('click', () => setWidth(i)); ws.appendChild(b);
    });
    $('#colour-pick').addEventListener('click', e => colourMenu(e.currentTarget));
    $('#width-pick').addEventListener('click', e => widthMenu(e.currentTarget));
    $('#undo-btn').addEventListener('click', undo);
    $('#redo-btn').addEventListener('click', redo);
  }
  function setTool(id) {
    if (S.editing && id !== 'text') commitText();
    if (id !== 'select') { S.sel = null; const mb = $('#mini-bar'); if (mb) mb.remove(); }
    S.tool = id; S.settings.tool = id; saveSettings(); updateToolbar(); renderBanners(); dirty(); cv.focus();
    setCursor(id === 'text' ? 'text' : id === 'select' ? 'default' : id === 'eraser' ? 'none' : 'crosshair');
  }
  function setColour(i) {
    S.colour = clamp(i, 0, COLOURS.length - 1); S.settings.colour_name = COLOURS[S.colour].name; saveSettings();
    if (S.editing) { S.editing.st.colour = COLOURS[S.colour].name; S.editing.st.rgb = COLOURS[S.colour].rgb.slice(); placeEditor(); }
    else if (S.sel && S.sel.kind !== 'ref' && S.sel.kind !== 'match') recolour(S.sel, S.colour);
    updateToolbar();
  }
  function setWidth(i) {
    S.width = clamp(i, 0, WIDTHS.length - 1); S.settings.width = S.width; saveSettings();
    const px = WIDTHS[S.width].px;
    if (S.editing) { const st = S.editing.st; st.width = px; st.font_px = TEXT_PX[px]; placeEditor(); autosizeEditor(); }
    else if (S.sel && !['ref', 'match'].includes(S.sel.kind)) {
      snapshot(); const st = S.sel;
      if (st.kind === 'text') { st.width = px; st.font_px = TEXT_PX[px]; measureText(st); }
      else st.width = st.kind === 'highlight' ? MARKER_W[px] : px;
      changed();
    }
    updateToolbar(); dirty();
  }
  function moveThumb(thumb, btn) {
    if (!btn || btn.offsetParent === null) { thumb.style.opacity = 0; return; }
    thumb.style.opacity = 1; thumb.style.width = btn.offsetWidth + 'px'; thumb.style.transform = `translateX(${btn.offsetLeft}px)`;
  }
  function updateToolbar() {
    const btns = [...document.querySelectorAll('#tools [data-tool]')];
    btns.forEach(b => b.classList.toggle('on', b.dataset.tool === S.tool));
    const active = btns.find(b => b.dataset.tool === S.tool && !b.classList.contains('hidden'));
    const more = $('#more-tools');
    more.classList.toggle('on', !active);
    moveThumb($('#tool-thumb'), active || more);
    [...document.querySelectorAll('#swatches .swatch')].forEach((b, i) => b.classList.toggle('on', i === S.colour));
    const wb = [...document.querySelectorAll('#widths .btn')]; wb.forEach((b, i) => b.classList.toggle('on', i === S.width));
    moveThumb($('#width-thumb'), wb[S.width]);
    $('#colour-pick').style.setProperty('--c', css(COLOURS[S.colour].rgb));
    $('#width-pick .wline').style.height = [1.6, 3.4, 6][S.width] + 'px';
    $('#undo-btn').disabled = !S.history.length && !S.editing; $('#redo-btn').disabled = !S.future.length;
  }
  // make the toolbar fit the window: tuck rare tools, then colours and widths, into menus
  function layoutChrome() {
    // what goes first when the window is narrow: the document pill, rare tools, the colour row, widths, then shape tools
    const tb = $('#toolbar'); const level = n => {
      const hideTier = n >= 6 ? 3 : n >= 5 ? 2 : n >= 2 ? 1 : 0;
      document.querySelectorAll('#tools [data-tool]').forEach(b => { const tl = TOOLS.find(x => x.id === b.dataset.tool); b.classList.toggle('hidden', !!(tl.more && tl.more <= hideTier)); });
      const cw = n >= 3; $('#swatches').classList.toggle('hidden', cw); $('#colour-pick').classList.toggle('hidden', !cw);
      const ww = n >= 4; $('#widths').classList.toggle('hidden', ww); $('#width-pick').classList.toggle('hidden', !ww);
      $('#sep-b').classList.toggle('hidden', ww && cw);
      $('#doc-info').classList.toggle('hidden', n >= 1);
      $('#more-tools').classList.toggle('hidden', hideTier === 0);
    };
    // centred in the window when it fits there, else centred in the gap between the corner clusters
    for (let n = 0; n <= 6; n++) {
      level(n);
      const tl = $('.cluster.tl').getBoundingClientRect(), tr = $('.cluster.tr').getBoundingClientRect();
      const w = tb.offsetWidth, lo = tl.right + 12, hi = tr.left - 12;
      const centred = innerWidth / 2 - w / 2;
      if (centred >= lo && centred + w <= hi) { tb.style.left = '50%'; break; }
      if (w <= hi - lo || n === 6) { tb.style.left = (lo + Math.max(0, (hi - lo - w) / 2) + w / 2) + 'px'; break; }
    }
    $('#more-tools').classList.toggle('hidden', !TOOLS.some(x => { const b = document.querySelector(`#tools [data-tool="${x.id}"]`); return b && b.classList.contains('hidden'); }));
    requestAnimationFrame(updateToolbar);
  }
  function moreToolsMenu(anchor) {
    const hidden = TOOLS.filter(x => { const b = document.querySelector(`#tools [data-tool="${x.id}"]`); return b && b.classList.contains('hidden'); });
    menu(anchor, hidden.map(x => ({ icon: x.icon, label: x.name, key: x.key === 'M' ? 'Shift+M' : x.key.toUpperCase(), check: S.tool === x.id, run: () => setTool(x.id) })));
  }
  function colourMenu(anchor) {
    const m = menu(anchor, [{ html: `<div class="dots">${COLOURS.map((c, i) => `<button class="btn swatch ${i === S.colour ? 'on' : ''}" style="--c:${css(c.rgb)}" data-i="${i}"><i></i></button>`).join('')}</div>` }]);
    m.addEventListener('click', e => { const b = e.target.closest('[data-i]'); if (b) { setColour(+b.dataset.i); closeMenus(); } });
  }
  function widthMenu(anchor) { menu(anchor, WIDTHS.map((w, i) => ({ label: w.name, check: i === S.width, run: () => setWidth(i) }))); }

  // ---------------------------------------------------------------- top bar right
  function buildTopRight() {
    $('#zoom-btn').addEventListener('click', () => { const v = vstate(); if (Math.abs(v.zoom - 1) < 0.002) actualSize(); else fitView(); });
    $('#inspect-btn').addEventListener('click', () => {
      S.settings.hover_inspect = !S.settings.hover_inspect; S.hoverPick.paused = false; S.hoverPick.slow = 0; saveSettings(); updateTopRight();
      toast(S.settings.hover_inspect ? 'Showing what is under the cursor' : 'Hover inspector off', 'target');
    });
    $('#marks-btn').addEventListener('click', () => togglePanel('marks'));
    $('#settings-btn').addEventListener('click', settingsSheet);
    $('#help-btn').addEventListener('click', helpSheet);
    $('#sidebar-btn').addEventListener('click', () => togglePanel('sidebar'));
    $('#sidebar-close').addEventListener('click', () => togglePanel('sidebar', false));
    $('#marks-close').addEventListener('click', () => togglePanel('marks', false));
  }
  function updateTopRight() {
    $('#inspect-btn').classList.toggle('on', !!S.settings.hover_inspect);
    $('#marks-btn').classList.toggle('on', !$('#marks-panel').classList.contains('closed'));
    $('#sidebar-btn').classList.toggle('on', !$('#sidebar').classList.contains('closed'));
  }
  let insetTween = 0; const insetGoal = { L: 0, R: 0 };   // where each panel's inset is heading (S.insetL/R ease towards it)
  function togglePanel(which, force) {
    const p = which === 'marks' ? $('#marks-panel') : $('#sidebar');
    const open = force == null ? p.classList.contains('closed') : force;
    if (which !== 'marks' && !open) { closeHelp(); closeEnginePop(); }
    p.classList.toggle('closed', !open);
    const fromL = S.insetL, fromR = S.insetR;
    if (which === 'marks') { insetGoal.R = open ? 300 + 12 : 0; S.settings.marks_open = open; }
    else { insetGoal.L = open ? $('#sidebar').offsetWidth + 12 : 0; S.settings.sidebar_open = open; }
    document.documentElement.style.setProperty('--left-inset', insetGoal.L + 'px');
    document.documentElement.style.setProperty('--right-inset', insetGoal.R + 'px');
    S.insetL = insetGoal.L; S.insetR = insetGoal.R;
    saveSettings(); updateTopRight();
    // the capture eases to its new fit while the panel slides (7 Nov 2026: it used to jump there in one frame),
    // then the canvas reflows once the composer has moved. Reduce motion keeps the jump.
    const toL = insetGoal.L, toR = insetGoal.R, tok = ++insetTween, dur = S.settings.reduce_motion ? 1 : 480;
    const t0 = performance.now(); const step = () => {
      if (tok !== insetTween) return;
      const k = Math.min(1, (performance.now() - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      S.insetL = fromL + (toL - fromL) * e; S.insetR = fromR + (toR - fromR) * e;
      computeSafe(); dirty(); placeEditor();
      if (performance.now() - t0 < 600) requestAnimationFrame(step); else { S.insetL = toL; S.insetR = toR; layoutChrome(); }
    };
    S.insetL = fromL; S.insetR = fromR;
    requestAnimationFrame(step);
  }
  function docInfo() {
    const i = S.boot.info || {};
    $('#doc-info').innerHTML = `<b title="${esc(i.path || '')}">${esc(i.doc || 'Untitled')}</b><span class="dot"></span><span>${esc(i.view || '')}</span>` +
      (i.display ? `<span class="dot"></span><span>${esc(i.display)}</span>` : '');
    $('#doc-info').dataset.tip = `${i.doc || 'Untitled'}`; $('#doc-info').dataset.sub = `${i.view || ''} · ${i.display || ''} · ${S.iw} x ${S.ih} px` + (i.selected ? ` · ${i.selected} selected` : '');
  }

  // ---------------------------------------------------------------- sidebar: the job, what it runs in, how, its options
  // Each task says what it runs in and how it works, and has options (remembered per task), saved setups, a library
  // where it has one (materials, HDRIs), the images on this capture with the part each one plays, the user’s standing
  // instructions, and the exact brief Claude gets. The brief goes into meta.json (task_options, task_brief) and to the
  // hook as the first preset, so Claude reads it the way it read presets before.
  const ENV = () => S.env || {};
  const runIn = (ic, t, sub) => ({ ic, t, sub });
  const rhinoName = () => ENV().rhino || 'Rhino';
  const vrayName = () => ENV().vray || 'V-Ray';
  const MCP = () => runIn('terminal', 'Rhino MCP', 'Claude\'s link to Rhino');
  const shortPath = p => { const parts = String(p || '').split(/[\\/]/).filter(Boolean); return parts.length > 3 ? '...\\' + parts.slice(-2).join('\\') : String(p || ''); };
  const note_ = k => ((S.boot && S.boot.notes) || {})[k] || '';
  const SAVE_FIRST = 'Save the user’s .3dm before changing anything.';
  const CLAY = 'Clay: prefer V-Ray\'s material override (matte white, albedo about 0.8, no reflection) with glass excluded and given a light grey translucent material, so the user’s own materials stay assigned. If the override can\'t be scripted, tell the user the one toggle.';
  const SKETCH = 'Propose three genuinely different options for what the user marked: different principles, not variants of one, each with a one-line idea. Do NOT model them in Rhino. Write each as a few massing primitives in world coordinates (schema in tools/cm_sketch.py), anchored on the user’s marks with pick()/snap() from tools/cm_core.py; keep each option to what conveys its idea. Take one depth snapshot (depth_from_meta in tools/cm_core.py), render with python tools/cm_sketch.py render <capture> <options.json> <out> --depth <depth.bin>, look at the board yourself, then post it (cm_sketch.py post) and tell the user to type ClaudeReply in Rhino to flip through and pick one. Model only the option the user picks, with the user’s marked changes, and only after the user picks.';
  const MONTAGE = 'Montage the design into the site photo (the reference marked as the site photo; if there is none, ask for one). The user double-clicks it and places Photo match points (Shift+M) on the photo and then the same points on the model (6 or more, spread out, not all on one plane). The objects selected at capture are the proposal; if none, ask which. Snap the model points and solve the photo\'s camera (tools/cm_photomatch.py), then show the user the model edges overlaid on the photo before rendering. Set the sun from the photo\'s EXIF date and time at the site, render the proposal alone in V-Ray from the solved camera (notes/RHINO-VRAY-NOTES.md section vray-render-over-mcp) and composite it over the photo (tools/cm_post.py montage). Report the reprojection error per point.';
  const BOARD = 'Make a style board from the reference look the user dropped (the reference marked as a look to match; if none, ask for one): follow \'Render styles\' in tools/TOOLS.md. Read the reference (cm_style.py read) and look at it yourself, then write three styles that differ in principle: A matches the reference as closely as V-Ray physically can, B and C each change one big thing about the light (time, sky, haze), not just the grade. Snapshot the user’s settings (style_begin), render a real V-Ray preview of each from this capture\'s camera (about a minute each), fit A\'s grade to the reference, build and post the board, restore the user’s settings, and tell the user to type ClaudeReply to pick. Say plainly what the reference does that V-Ray can\'t (a sky colour, an impossible sun at the user’s site).';
  const POST_REPLY = 'post_reply() in tools/cm_reply.py';
  function site() {
    const s = ENV().sun;
    return s && s.lat != null && s.set !== false ? `the site (Rhino's sun location, ${s.lat}, ${s.lon}: check that it is the real site, and ask if it is not)`
      : 'the site (the model\'s Rhino sun location; ask for it if that isn\'t set)';
  }
  const SIZES = [
    ['capture', 'Same as the capture'], ['720p', '1280 x 720', [1280, 720]], ['hd', 'HD, 1920 x 1080', [1920, 1080]],
    ['4k', '4K, 3840 x 2160', [3840, 2160]], ['a3', 'A3 at 300 dpi', [4961, 3508]], ['a2', 'A2 at 300 dpi', [7016, 4961]],
    ['square', 'Square, 2048', [2048, 2048]], ['insta', 'Instagram 4:5', [1080, 1350]], ['custom', 'Custom size'],
  ];
  function sizeWH(o) {
    if (o.size === 'capture') return [S.iw, S.ih];
    if (o.size === 'custom') return [Math.round(+o.w || S.iw), Math.round(+o.h || S.ih)];
    const s = SIZES.find(x => x[0] === o.size); const wh = s && s[2] ? s[2].slice() : [1920, 1080];
    if (o.orient === 'port' && wh[0] > wh[1]) wh.reverse();
    return wh;
  }
  const QUALITY = {
    draft: { l: 'Draft', hint: 'About a minute: noise threshold 0.1', text: 'noise threshold 0.1, about a 1 min time limit', passes: 60 },
    medium: { l: 'Medium', hint: 'Noise threshold 0.05: a few minutes', text: 'noise threshold 0.05', passes: 150 },
    high: { l: 'High', hint: 'Noise threshold 0.02', text: 'noise threshold 0.02', passes: 400 },
    final: { l: 'Final', hint: 'Noise threshold 0.01, no time limit: the slowest', text: 'noise threshold 0.01, no time limit', passes: 1000 },
  };
  const LIGHT_TIMES = {
    golden: 'the sun about 8 to 10 degrees up in the evening', morning: 'mid-morning, the sun about 25 degrees up in the east',
    midday: 'about 1:00 pm local time, the sun near its highest', afternoon: 'late afternoon, the sun about 20 degrees up in the west',
    blue: 'the sun just below the horizon (blue hour): a blue twilight sky',
  };
  const SEASONS = { summer: 'on a summer day (21 December south of the equator, 21 June north)', equinox: 'at an equinox (21 March or 21 September)', winter: 'on a winter day (21 June south of the equator, 21 December north)' };
  const SKIES = { clear: 'Clear sky (turbidity about 2.5).', hazy: 'Hazy sky (turbidity about 5 to 6).', overcast: 'Overcast: the V-Ray Sky model CIE Overcast, the sun\'s shadows off, low contrast, no hard shadows.' };
  const TURNS = { behind_left: 'behind the camera, to the left', behind_right: 'behind the camera, to the right', side: 'to one side of the view', backlit: 'in front of the camera (backlit)' };
  const ANALYSES = { section: 'Section along the line', sun: 'Sun hours on the marked area', shadow: 'Shadow study, hourly', isovist: 'Isovist from each pin', eye: 'Eye-level view from each pin' };
  const DATES = { jun: '21 June', mar: '21 March', dec: '21 December' };
  const listText = a => a.length < 2 ? (a[0] || '') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];

  // Model: levels of detail as BIM uses them, and the parts that can be modelled in full
  const LOD = {
    '100': { hint: 'Massing: volumes at the right size and place', short: 'massing only', brief: 'massing only: volumes at the right size and place, no openings or elements' },
    '200': { hint: 'Generic elements: walls, floors, openings and stairs at about the right size', short: 'generic elements', brief: 'generic elements: walls, floors, roofs, openings and stairs at approximate sizes, thicknesses and positions' },
    '300': { hint: 'Real elements: true sizes and thicknesses, frames and glass, stair treads', short: 'real elements at true size', brief: 'every element at its true size, thickness, shape and position, openings framed and glazed, stairs with treads and balustrades' },
    '350': { hint: 'Real elements and how they meet: build-ups, junctions, supports, flashings', short: 'real elements and their junctions', brief: 'every element at its true size plus how it meets the others: build-ups, junctions, supports, flashings and connections' },
    '400': { hint: 'Fabrication: every component as built, down to fixings and hardware', short: 'every component as built', brief: 'fabrication detail: every component as it would be built and installed, down to fixings, hardware and joints' },
  };
  const PARTS = [['buildups', 'Build-ups'], ['openings', 'Windows and doors'], ['stairs', 'Stairs and rails'], ['structure', 'Structure'], ['roof', 'Roof edges'],
    ['facade', 'Facade and cladding'], ['interior', 'Interior finishes'], ['fixtures', 'Fixtures and services'], ['site', 'Landscape']];
  const PARTS_SHORT = { buildups: 'build-ups', openings: 'windows and doors', stairs: 'stairs and rails', structure: 'structure', roof: 'roof edges', facade: 'facade and cladding', interior: 'interior finishes', fixtures: 'fixtures and services', site: 'landscape' };
  const LOD_PARTS = { '100': [], '200': [], '300': ['openings', 'stairs', 'buildups'], '350': ['openings', 'stairs', 'buildups', 'structure', 'roof', 'facade'], '400': PARTS.map(p => p[0]) };
  const LOD_MIN = { '100': '100', '200': '100', '300': '20', '350': '5', '400': '1' };
  const PART_TEXT = {
    buildups: 'Walls, floors and roofs as their real build-ups, each layer its own solid at its real thickness (external wall: cladding, battens and cavity, wrap, insulation, studs or masonry, lining; floor: finish, underlay or screed, slab or joists, ceiling; roof: covering, battens, membrane, insulation, rafters or purlins, ceiling). Use the design\'s thicknesses, else typical Australian construction, and list what you assumed.',
    openings: 'Every window and door as a full assembly: frames with real sightlines and depths (aluminium about 50 to 70 mm, timber 90 to 140 mm), sashes, double glazing as two panes and a spacer (6, 12, 6 mm unless told), mullions and transoms, sills with a fall and a drip, head and jamb reveals and flashings; door leaves at real thickness (35 to 40 mm inside, 40 to 50 mm outside) with frames, thresholds and handles.',
    stairs: 'Stairs with treads, risers, nosings, stringers and landings; balustrades with posts, balusters or infill panels, handrails and their fixings.',
    structure: 'Columns, beams and bracing at their real section profiles and sizes (UB, UC, PFC, SHS, RHS, CHS, glulam, LVL), with base plates, cleats and bolts.',
    roof: 'Roof edges: gutters, downpipes, fascias, barges, cappings, flashings and parapet copings, with real profiles and falls.',
    facade: 'Cladding as real boards, sheets or modules with their joints and gaps; curtain walls as mullions, transoms, glazing and spandrels with real profiles; sunshades and their brackets.',
    interior: 'Interior finishes: skirtings, architraves, cornices, ceilings with bulkheads and grids, and joinery (benches, shelves, wardrobes).',
    fixtures: 'Fixtures and services: light fittings, switches and outlets, vents, sprinkler heads, sanitary fixtures and taps, at real sizes and mounting heights.',
    site: 'Landscape: kerbs, edges, paving with its joints, steps, grates, planters and retaining walls.',
  };
  function detailBrief(o) {
    const L = [`Level of detail: LOD ${o.lod}, ${LOD[o.lod].brief}.`];
    if ((o.parts || []).length) L.push('Model these in full, down to their real components: ' + o.parts.map(p => PART_TEXT[p]).join(' '));
    L.push(`Model every feature ${o.min} mm or larger; leave out anything smaller.`);
    L.push({ marked: 'Detail the elements under the user’s marks.', view: 'Detail every element visible in this view.', all: 'Detail the whole model, one element type at a time; say roughly how long it will take and check with the user before starting.' }[o.dscope]);
    if (o.copies) L.push('Make each repeated component a block definition with every copy an instance, like a Revit family, so a change to one updates them all; where the user’s model already has copies of what you detail, detail every copy the same way.');
    if (o.revit) L.push('Organise it like Revit: layers by category (Walls, Floors, Roofs, Ceilings, Windows, Doors, Curtain wall, Stairs, Railings, Structure, Finishes, Fixtures, Site), every object named by element and type (like "Window W03, aluminium awning 1200 x 900"), and its category, type, material and thickness as Attribute User Text.');
    if (o.code) L.push('Follow the NCC and the Australian Standards: stair risers 115 to 190 mm and goings 240 to 355 mm (2R + G between 550 and 700), balustrades 1 m high where the fall is over 1 m (865 mm above the nosings on a stair) with no gap over 125 mm, handrails 865 to 1000 mm high, and door and corridor widths for access. Say where the design breaks one.');
    L.push('Keep the file workable: blocks for repeats, and tell the user the object count and file size before and after.');
    return L;
  }

  // Fix: what it looks for, and the brief
  const FIX_LOOK = {
    gaps: { l: 'Gaps and holes', short: 'gaps and holes', brief: 'open edges and gaps (naked edges, slivers, surfaces that stop short of their neighbour)' },
    glass: { l: 'Missing glass', short: 'missing glass', brief: 'frames with no glass, or glass that doesn\'t fill its frame' },
    clash: { l: 'Overlaps and clashes', short: 'overlaps', brief: 'overlaps and clashes (solids running into each other, duplicates on top of each other)' },
    odd: { l: 'Odd ones out', short: 'odd ones out', brief: 'copies that differ from their neighbours (a window without the frame detail the others have, a panel at the wrong size)' },
    stray: { l: 'Stray objects', short: 'stray objects', brief: 'stray objects that belong to nothing (a floating panel, a leftover surface, a random light)' },
    detail: { l: 'Missing detail', short: 'missing detail', brief: 'missing detail compared with the same element elsewhere in the model' },
  };
  function fixBrief(o) {
    const L = ['Each mark is a modelling error to fix; the user’s note may only say "gap", "hole" or "this".'];
    const look = (o.look || []).map(k => FIX_LOOK[k].brief);
    if (o.where === 'all') L.push('Sweep the whole model first: list every naked edge, open polysurface, clash and stray object by layer and place, show the user the list, and fix what the user picks.');
    else L.push(`${{ marked: 'At each mark', around: 'At each mark and within about a metre of it', view: 'Everywhere in this view' }[o.where]}, look for: ${look.join('; ') || 'anything wrong'}.` + (o.where === 'marked' ? ' Change nothing outside the marks.' : ''));
    L.push({ match: 'Fix each one by matching the parts around it: take the profile, detail, thickness, layer and material from the good neighbour or the same element elsewhere, rather than patching it with a generic surface.',
      simple: 'Close each one with the simplest clean surface or part, joined into what it fixes, on its layer and with its material.',
      ask: 'Before changing anything, list what you found at each mark and how you would fix it, and wait for the user’s OK.' }[o.by]);
    L.push(`Close gaps up to ${o.gap} mm; anything wider is an opening unless the user marked it. Join fixed parts into their neighbours' polysurfaces where they belong, and keep names and layers.`);
    if (o.save_first) L.push(SAVE_FIRST);
    if (o.backup) L.push('Before changing any of the user’s objects, copy them to a hidden backup layer.');
    if (o.check) L.push('After fixing, check each fixed object: closed where it should be (no naked edges), no clashes with its neighbours, no duplicates. Report anything you couldn\'t close.');
    if (o.before_after) L.push('Then capture this view again from the same camera and show the user the before and after.');
    return L;
  }

  // Render: each engine, with the settings that are its own (they pop up when the engine is picked)
  const DENOISERS = { off: 'no denoiser', vray: 'the V-Ray denoiser', nvidia: 'the NVIDIA AI denoiser', intel: 'the Intel Open Image denoiser' };
  const PY_LAYERS = { lines: 'linework', white: 'a white model', shadows: 'shadows', ao: 'ambient occlusion' };
  const ENGINE = {
    vray: {
      l: 'V-Ray', title: () => `${vrayName()} settings`,
      runs: () => [runIn('camera', vrayName(), `in ${rhinoName()}`), MCP()],
      fields: [
        { k: 'quality', t: 'seg', l: 'Quality', o: Object.keys(QUALITY).map(q => [q, QUALITY[q].l]), d: 'high', hint: v => QUALITY[v].hint },
        { k: 'vr_limit', t: 'num', l: 'Time limit', unit: 'min', d: 0, min: 0, max: 600, step: 1, hint: '0 for no limit' },
        { k: 'vr_sampler', t: 'seg', l: 'Sampler', o: [['progressive', 'Progressive'], ['bucket', 'Bucket']], d: 'progressive',
          hint: { progressive: 'The whole frame refines at once, until the noise threshold or the time limit', bucket: 'Tile by tile: steadier for big final renders' } },
        { k: 'vr_gi', t: 'menu', l: 'Global illumination', o: [['lc', 'Brute force + light cache'], ['bf', 'Brute force twice']], d: 'lc',
          hint: v => v === 'lc' ? 'Fast and clean: the usual for architecture' : 'Slower; the most accurate in tight interiors' },
        { k: 'vr_denoiser', t: 'menu', l: 'Denoiser', o: [['off', 'Off'], ['vray', 'V-Ray'], ['nvidia', 'NVIDIA AI'], ['intel', 'Intel Open Image']], d: 'nvidia' },
        { k: 'vr_elements', t: 'chips', l: 'Render elements', o: [['alpha', 'Alpha'], ['zdepth', 'Z-depth'], ['matid', 'Material ID'], ['lighting', 'Lighting'], ['reflection', 'Reflection']], d: [], hint: 'Saved beside the render, for post' },
        { k: 'vr_lens', t: 'toggle', l: 'Lens effects', hint: 'Bloom and glare on bright spots', d: false },
        { k: 'vr_vfb', t: 'toggle', l: 'Keep my VFB colour corrections', hint: 'Off: a clean render without your VFB layers', d: true },
      ],
      summary: o => [QUALITY[o.quality].l, o.vr_denoiser === 'off' ? 'no denoiser' : { vray: 'V-Ray', nvidia: 'NVIDIA', intel: 'Intel' }[o.vr_denoiser] + ' denoiser', o.vr_limit ? o.vr_limit + ' min limit' : '', (o.vr_elements || []).length ? (o.vr_elements || []).length + ' elements' : ''].filter(Boolean).join(', '),
    },
    raytraced: {
      l: 'Raytraced', title: () => 'Raytraced settings',
      runs: () => [runIn('camera', 'Raytraced (Cycles)', `${rhinoName()}'s own, no V-Ray`), MCP()],
      fields: [
        { k: 'quality', t: 'seg', l: 'Quality', o: Object.keys(QUALITY).map(q => [q, QUALITY[q].l]), d: 'high', hint: v => `About ${QUALITY[v].passes} passes` },
        { k: 'rt_bounces', t: 'seg', l: 'Light bounces', o: [['4', '4'], ['8', '8'], ['32', '32']], d: '8', hint: 'More: brighter, truer interiors, but slower' },
        { k: 'rt_denoiser', t: 'menu', l: 'Denoiser', o: [['off', 'Off'], ['intel', 'Intel'], ['nvidia', 'NVIDIA']], d: 'nvidia' },
        { k: 'rt_caustics', t: 'toggle', l: 'Caustics', hint: 'Light through glass and water; slower', d: false },
      ],
      note: 'Raytraced shows V-Ray materials as Rhino\'s closest match.',
      summary: o => `About ${QUALITY[o.quality].passes} passes, ${o.rt_bounces} bounces` + (o.rt_denoiser === 'off' ? '' : ', denoised'),
    },
    python: {
      l: 'Python script', title: () => 'Python script settings',
      runs: o => [runIn('terminal', 'Python script', 'reads the saved .3dm with rhino3dm'), o.py_renderer === 'blender' ? runIn('cube', 'Blender 4.5', 'Cycles, headless') : runIn('cube', 'moderngl', 'on your GPU, about half a second'), runIn('check', 'Rhino stays free', 'nothing runs inside it')],
      fields: [
        { k: 'py_renderer', t: 'seg', l: 'Renderer', o: [['moderngl', 'moderngl'], ['blender', 'Blender']], d: 'moderngl',
          hint: { moderngl: 'Fast and graphic: linework, a white model, shadows', blender: 'Photoreal Cycles in Blender, headless; slower' } },
        { k: 'py_layers', t: 'chips', l: 'Draw', o: Object.entries(PY_LAYERS).map(([k, v]) => [k, v[0].toUpperCase() + v.slice(1).replace(/^a white/, 'white')]), d: ['lines', 'white', 'shadows'], min1: true, when: o => o.py_renderer === 'moderngl' },
        { k: 'py_edges', t: 'seg', l: 'Edges', o: [['outline', 'Outlines and creases'], ['all', 'Every edge']], d: 'outline', when: o => o.py_renderer === 'moderngl' && (o.py_layers || []).includes('lines') },
        { k: 'py_weight', t: 'seg', l: 'Line weight', o: [['fine', 'Fine'], ['medium', 'Medium'], ['bold', 'Bold']], d: 'medium', when: o => o.py_renderer === 'moderngl' && (o.py_layers || []).includes('lines') },
        { k: 'py_sun', t: 'seg', l: 'Shadows from', o: [['rhino', 'Rhino\'s sun'], ['overhead', 'Overhead'], ['lighting', 'My Lighting']], d: 'rhino', when: o => o.py_renderer === 'moderngl' && (o.py_layers || []).includes('shadows') },
        { k: 'py_ss', t: 'seg', l: 'Supersampling', o: [['1', '1x'], ['2', '2x'], ['4', '4x']], d: '2', when: o => o.py_renderer === 'moderngl', hint: 'Smoother lines; 4x takes longer' },
        { k: 'py_samples', t: 'num', l: 'Samples', d: 128, min: 16, max: 4096, step: 16, when: o => o.py_renderer === 'blender', hint: 'Adaptive, OptiX denoised' },
      ],
      note: 'Only what has been shaded in Rhino has a render mesh in the saved file; Claude counts what is missing.',
      summary: o => o.py_renderer === 'blender' ? `Blender, ${o.py_samples} samples` : listText((o.py_layers || []).map(k => PY_LAYERS[k].replace(/^a /, ''))) + `, ${o.py_ss}x`,
    },
  };
  const ENGINE_FIELDS = () => Object.values(ENGINE).reduce((a, E) => a.concat(E.fields), []);
  function renderCam(o) {
    const cam = String(o.camera);
    return { capture: 'this capture\'s exact camera, saved as a named view', two_point: `a two-point view at eye level with a ${o.lens} mm lens`, axo: 'an axonometric from the best corner' }[cam] || `your named view "${cam.slice(3)}"`;
  }
  function renderHow(o) {
    const [w, h] = sizeWH(o), L = [];
    if (o.engine === 'python') {
      L.push('Claude saves your file: the script reads the saved .3dm, so Rhino stays free to use.');
      L.push('A Python script reads the render meshes with rhino3dm (about a minute for a big model).');
      L.push(o.py_renderer === 'blender' ? `Blender renders them with Cycles at ${o.py_samples} samples, from ${renderCam(o)}, at ${w} x ${h} px.`
        : `It draws ${listText((o.py_layers || []).map(k => PY_LAYERS[k]))} from ${renderCam(o)} on your GPU, at ${w} x ${h} px, ${o.py_ss}x supersampled.`);
    } else {
      L.push(`Claude sets up ${renderCam(o)}.`);
      L.push(o.engine === 'raytraced' ? `It switches the view to Raytraced, lets it run about ${QUALITY[o.quality].passes} passes with ${o.rt_bounces} light bounces, and captures it at ${w} x ${h} px.`
        : `It sets V-Ray to ${w} x ${h} px at ${QUALITY[o.quality].l.toLowerCase()} quality with ${DENOISERS[o.vr_denoiser]}; your other V-Ray settings stay as they are.`);
      const first = [o.look === 'clay' ? 'everything clay' : '', o.people !== 'none' ? 'people placed' : '', o.light === 'lighting' ? 'your Lighting options set' : ''].filter(Boolean);
      if (first.length) L.push('First it gets ' + listText(first) + '.');
    }
    L.push(`It saves it as ${String(o.format).toUpperCase()} to ${o.save === 'doc' ? 'a Renders folder next to your .3dm' : 'your folder'}${o.open ? ' and opens it for you' : ''}.`);
    return L;
  }

  // Critique: who reviews it
  const WHO = {
    critic: { l: 'Critic', hint: 'One sceptic: what must be true for your claim, and where it breaks',
      runs: () => [runIn('crit', 'The critic agent', 'attacks your claim')], how: 'The critic attacks the claim: what must be true for it, and where that is unproven or false.' },
    council: { l: 'Architectural council', hint: 'Seven architects, chaired by Whitmore (version 3.1)',
      runs: o => [runIn('crit', 'Architectural council 3.1', 'seven architects, Whitmore chairs'), runIn('chart', 'Cost', councilCost('council', o))],
      how: 'Seven architects review it (Kovács parametricist, Tanaka phenomenologist, Rossi typologist, Metzger tectonic rationalist, Okonkwo critical regionalist, Chen social ecologist, Berg speculative futurist), argue it out, and Whitmore draws it together: a status, a risk register and a roadmap.' },
    engineering: { l: 'Engineering council', hint: 'Ten engineers in five fields, chaired by Chen, against the NCC (version 2.1.1)',
      runs: o => [runIn('crit', 'Engineering council 2.1.1', 'ten engineers, Chen chairs'), runIn('chart', 'Cost', councilCost('engineering', o))],
      how: 'Ten engineers in five fields review it against the NCC and the Australian Standards (Khan structure, Balmond geometry, Brunel buildability, Maillart concrete, Fuller systems, Sobek envelope and carbon, Carrier moisture and plant, Arup fire and egress, Rice connections, Otto roofs), with rough numbers, up to three details and a fix order; Chen chairs.' },
    both: { l: 'Both councils', hint: 'The architects first, then the engineers, in one reply',
      runs: o => [runIn('crit', 'Both councils', 'architectural 3.1, then engineering 2.1.1'), runIn('chart', 'Cost', councilCost('both', o))],
      how: 'The architectural council reviews it first, then the engineering council; one reply carries both, numbered on.' },
    brief: { l: 'Against the brief', hint: 'Where the design delivers your project brief, and where they part',
      runs: () => [runIn('crit', 'Claude', 'with your project brief')], how: 'It tests the design against your project brief.' },
  };
  function councilCost(who, o) {
    const a = o.length === 'quick' ? 11 : (o.image ? 79 : 16), e = o.image ? 62 : 26;
    return `about ${who === 'council' ? a : who === 'engineering' ? e : a + e}k tokens`;
  }
  function councilText(which, o) {
    if (which === 'council') return 'Run the architectural council (Skill tool: architectural-council, version 3.1: the arch-council agent in one pass, text only, no files). Write its brief from the user’s note (the claim), the user’s marks and what Rhino found under them, and meta.json; state the site facts the board needs (the hemisphere, which side the sun is on, the compass faces, real dimensions from the model), because it gets them wrong when they are left out.' +
      (o.length === 'quick' ? ' Ask for the Quick length (about 500 words).' : '') +
      (o.image ? ' Give the board marked.png as well.' : ' Send the brief as text: describe what the view shows rather than attaching it (the council reviews arguments, not images).') +
      (o.confirm ? ' Check the brief with the user before the board sits.' : '') + ' Fact-check the report against the model and the capture before relaying it.';
    return 'Run the engineering council (Skill tool: engineering-council, version 2.1.1: the eng-council agent, text only, no files). Write its brief from the user’s note, the user’s marks and meta.json before opening the image (the brief comes before the drawings): the structure, envelope and services the view shows, and what isn\'t known, listed as unknown.' +
      (o.image ? ' Then give the council marked.png as the drawing.' : '') + ' It reviews against the NCC (cited by Section and topic) and the Australian Standards.' +
      (o.confirm ? ' Check the brief with the user before the council sits.' : '') + ' Fact-check the report against the model and the capture before relaying it.';
  }

  const TASKS = {
    general: {
      label: 'General', icon: 'sparkle', blurb: 'Ask anything about this view, or have Claude change it.',
      runs: () => [runIn('terminal', 'Claude Code', 'your session'), runIn('cube', rhinoName(), 'when it needs the model')],
      how: o => [
        'Claude reads your marks, what Rhino found under each one, and the exact camera.',
        { answer: 'It answers in your Claude Code session and leaves the model alone.', plan: 'It says what it would change and waits for your OK.', go: 'It makes the change in your Rhino over the MCP.' }[o.may],
        o.may !== 'answer' && o.show_result ? 'It captures this view afterwards so you can check it.' : '',
      ],
      fields: [
        { k: 'may', t: 'seg', l: 'Claude may', o: [['answer', 'Just answer'], ['plan', 'Ask first'], ['go', 'Go ahead']], d: 'go',
          hint: { answer: 'Answers only: it won\'t touch the model or your files', plan: 'It says what it would change, then waits for your OK', go: 'It makes the change, then shows you what changed' } },
        { k: 'length', t: 'seg', l: 'Answer', o: [['short', 'Short'], ['normal', 'Normal'], ['detailed', 'Detailed']], d: 'normal' },
        { k: 'save_first', t: 'toggle', l: 'Save the .3dm before changes', d: true, when: o => o.may !== 'answer' },
        { k: 'show_result', t: 'toggle', l: 'Show me a capture after', hint: 'Of this view, once it is done', d: true, when: o => o.may !== 'answer' },
      ],
      brief: o => [
        { answer: 'Answer only: do not change the model or any files.', plan: 'Before changing anything, say what you would change and wait for the user’s OK.', go: '' }[o.may],
        { short: 'Keep the answer short.', normal: '', detailed: 'Answer in detail, with your reasoning.' }[o.length],
        o.may !== 'answer' && o.save_first ? SAVE_FIRST : '',
        o.may !== 'answer' && o.show_result ? 'When it is done, capture this view and show the user.' : '',
      ],
      tag: () => '',
    },

    model: {
      label: 'Model', icon: 'cube', blurb: 'Build or change geometry in your Rhino file, to the level of detail you set.',
      runs: () => [runIn('cube', rhinoName(), ENV().units ? 'in ' + ENV().units.toLowerCase() : ''), MCP()],
      how: o => o.approach === 'sketch' ? [
        'Claude reads your marks, the objects under them and the camera.',
        'It sketches three different options over this view, without modelling anything.',
        'You flip through them with ClaudeReply and mark changes on the one you pick.',
        'Only then does it model that one.',
      ] : [
        'Claude reads your marks, the objects under them and the camera.',
        o.save_first && o.backup ? 'It saves your file and copies what it will change to a hidden backup layer.'
          : o.save_first ? 'It saves your file first.' : o.backup ? 'It copies what it will change to a hidden backup layer.' : 'It goes straight to the model.',
        `It models at LOD ${o.lod}: ${(o.parts || []).length ? listText(o.parts.map(p => PARTS_SHORT[p])) + ' in full' : LOD[o.lod].short}, down to ${o.min} mm${o.copies ? ', with repeated parts as blocks' : ''}.`,
        o.revit ? 'Everything goes on layers by category, named, with its type and material on the object.' : `New objects go on ${{ new: 'a ClaudeMark layer', current: 'the current layer', touch: 'the layer of what they touch' }[o.layer]}.`,
        'It captures the view so you can check it.',
      ],
      fields: [
        { k: 'approach', t: 'seg', l: 'Approach', o: [['model', 'Model it'], ['sketch', 'Sketch 3 options first']], d: 'model',
          hint: { model: 'Straight into your file', sketch: 'Three options drawn on this view; nothing is modelled until you pick' } },
        { k: 'geometry', t: 'menu', l: 'Geometry', o: [['nurbs', 'NURBS (breps, extrusions)'], ['mesh', 'Meshes'], ['subd', 'SubD'], ['match', 'Match what is there']], d: 'nurbs', when: o => o.approach === 'model' },
        { k: 'layer', t: 'menu', l: 'New objects go on', o: [['new', 'A ClaudeMark layer'], ['current', 'The current layer'], ['touch', 'The layer of what they touch']], d: 'new', when: o => o.approach === 'model' && !o.revit },
        { k: 'backup', t: 'toggle', l: 'Back up what changes', hint: 'A copy on a hidden layer before any edit', d: true, when: o => o.approach === 'model' },
        { k: 'real_sizes', t: 'toggle', l: 'Real product sizes', hint: 'Reads a named product\'s spec before modelling it', d: true },
        { k: 'save_first', t: 'toggle', l: 'Save the .3dm first', d: true, when: o => o.approach === 'model' },
        { t: 'group', l: 'Detail', when: o => o.approach === 'model' },
        { k: 'lod', t: 'seg', l: 'Level of detail (LOD)', o: Object.keys(LOD).map(k => [k, k]), d: '300', when: o => o.approach === 'model', hint: v => LOD[v].hint },
        { k: 'parts', t: 'chips', l: 'Model in full', o: PARTS, d: LOD_PARTS['300'], when: o => o.approach === 'model', hint: 'Each one down to its real components. The level sets these; change any' },
        { k: 'min', t: 'seg', l: 'Smallest detail', o: [['1', '1 mm'], ['5', '5 mm'], ['20', '20 mm'], ['100', '100 mm']], d: '20', when: o => o.approach === 'model', hint: v => `Every feature ${v} mm or larger; anything smaller is left out` },
        { k: 'dscope', t: 'menu', l: 'Apply detail to', o: [['marked', 'What I marked'], ['view', 'Everything in this view'], ['all', 'The whole model']], d: 'marked', when: o => o.approach === 'model' },
        { k: 'copies', t: 'toggle', l: 'Every copy, as blocks', hint: 'Detail one and every copy of it follows, like a Revit family', d: true, when: o => o.approach === 'model' },
        { k: 'revit', t: 'toggle', l: 'Organise like Revit', hint: 'Layers by category; each part named, with its type, material and thickness', d: true, when: o => o.approach === 'model' },
        { k: 'code', t: 'toggle', l: 'To the NCC', hint: 'Stairs, balustrades, doors and glazing sized to Australian rules', d: true, when: o => o.approach === 'model' },
      ],
      setups: [
        { id: 'b:massing', name: 'Massing', summary: 'LOD 100: volumes only', o: { approach: 'model', lod: '100', parts: [], min: '100' } },
        { id: 'b:design', name: 'Design model', summary: 'LOD 300: true sizes, frames and glass', o: { approach: 'model', lod: '300', parts: LOD_PARTS['300'], min: '20' } },
        { id: 'b:construction', name: 'Construction detail', summary: 'LOD 350: build-ups, junctions, structure', o: { approach: 'model', lod: '350', parts: LOD_PARTS['350'], min: '5' } },
        { id: 'b:revit', name: 'Revit-grade', summary: 'LOD 400: every component, fixings and all', o: { approach: 'model', lod: '400', parts: LOD_PARTS['400'], min: '1', copies: true, revit: true } },
      ],
      brief: o => o.approach === 'sketch' ? [SKETCH, o.real_sizes ? 'Size anything named after its real spec.' : ''] : [
        { nurbs: 'Build new geometry as NURBS breps or extrusions, never meshes.', mesh: 'Build new geometry as meshes.', subd: 'Build new geometry as SubD.', match: 'Build new geometry of the same type as what it attaches to.' }[o.geometry],
        o.backup ? 'Before changing any of the user’s objects, copy them to a hidden backup layer.' : '',
        o.revit ? '' : { new: 'Put new objects on a layer named "ClaudeMark" (make it if needed).', current: `Put new objects on the current layer${ENV().layer ? ' (' + ENV().layer + ')' : ''}.`, touch: 'Put new objects on the layer of the object they attach to.' }[o.layer] + ' Name everything new.',
        o.real_sizes ? 'If a product or precedent is named or linked, read its spec and photos first and model at true size. Say what scale you assumed if the document units disagree.' : '',
        o.save_first ? SAVE_FIRST : '',
      ].concat(detailBrief(o), ['When it is done, capture the view and show the user.']),
      tag: o => o.approach === 'sketch' ? 'Model: 3 options first' : `Model: LOD ${o.lod}`,
    },

    fix: {
      label: 'Fix', icon: 'fix', blurb: 'Fix modelling errors: gaps, holes, missing glass, clashes, odd ones out. A word is enough.',
      runs: o => [runIn('cube', rhinoName(), 'over the Rhino MCP'), runIn('target', 'Gap check', o.check ? 'open edges and clashes, after the fix' : 'off')],
      how: o => [
        o.where === 'all' ? 'Claude sweeps the whole model and lists every open edge, clash and stray object by layer and place.'
          : `Claude looks ${{ marked: 'at each mark', around: 'at each mark and about a metre round it', view: 'at everything in this view' }[o.where]} for ${listText((o.look || []).map(k => FIX_LOOK[k].short)) || 'anything wrong'}.`,
        o.save_first && o.backup ? 'It saves your file and copies what it will change to a hidden backup layer.' : o.save_first ? 'It saves your file first.' : o.backup ? 'It copies what it will change to a hidden backup layer.' : '',
        { match: 'It fixes each one to match the parts around it: the same profile, detail, layer and material as the good copies next to it.', simple: 'It closes each one with the simplest clean surface or part, joined into what it fixes.', ask: 'It tells you what it found and how it would fix each one, and waits for your OK.' }[o.by],
        [o.check ? 'it checks each fixed part is closed and clear of its neighbours' : '', o.before_after ? 'captures this view again so you see before and after' : ''].filter(Boolean).length
          ? 'Then ' + [o.check ? 'it checks each fixed part is closed and clear of its neighbours' : '', o.before_after ? 'captures this view again so you see before and after' : ''].filter(Boolean).join(', and ') + '.' : '',
      ],
      fields: [
        { k: 'look', t: 'chips', l: 'Look for', o: Object.keys(FIX_LOOK).map(k => [k, FIX_LOOK[k].l]), d: ['gaps', 'glass', 'clash', 'odd'] },
        { k: 'where', t: 'menu', l: 'Where', o: [['marked', 'Only what I marked'], ['around', 'Around my marks'], ['view', 'Everything in this view'], ['all', 'The whole model (a sweep)']], d: 'marked',
          hint: v => v === 'all' ? 'Finds everything first and shows you the list' : '' },
        { k: 'by', t: 'menu', l: 'Fix by', o: [['match', 'Matching what is around it'], ['simple', 'The simplest clean surface'], ['ask', 'Asking me first']], d: 'match' },
        { k: 'gap', t: 'num', l: 'Close gaps up to', unit: 'mm', d: 50, min: 0.1, max: 2000, step: 1, hint: 'Bigger is an opening, unless you marked it' },
        { k: 'backup', t: 'toggle', l: 'Back up what changes', hint: 'A copy on a hidden layer before any edit', d: true },
        { k: 'save_first', t: 'toggle', l: 'Save the .3dm first', d: true },
        { k: 'check', t: 'toggle', l: 'Check it is closed after', hint: 'No open edges or clashes left in what it fixed', d: true },
        { k: 'before_after', t: 'toggle', l: 'Before and after', hint: 'The same view, captured again', d: true },
      ],
      setups: [
        { id: 'b:quick', name: 'Quick fix', summary: 'Only what I marked, no checks', o: { where: 'marked', by: 'match', check: false, before_after: false } },
        { id: 'b:careful', name: 'Careful fix', summary: 'Backed up, checked, before and after', o: { where: 'marked', by: 'match', backup: true, save_first: true, check: true, before_after: true } },
        { id: 'b:sweep', name: 'Sweep the model', summary: 'Find every gap and clash, report first', o: { where: 'all', by: 'ask', check: true } },
      ],
      brief: o => fixBrief(o),
      tag: o => 'Fix' + (o.where === 'all' ? ': sweep the model' : ''),
    },

    materials: {
      label: 'Materials', icon: 'swatch', blurb: 'Put V-Ray materials on the surfaces you mark.',
      runs: () => [runIn('swatch', vrayName(), 'materials'), runIn('layers', 'Chaos Cosmos', `${(ENV().cosmos || {}).materials || 0} materials downloaded`), MCP()],
      how: (o, c) => [
        `Claude finds ${{ marked: 'the surfaces under your marks', selected: 'the objects you selected', layers: 'everything on the layers of what you marked', all: 'every surface in the model' }[o.apply]}.`,
        c.picks.length ? `It puts on the ${c.picks.length === 1 ? 'material' : c.picks.length + ' materials'} you picked, each where you said.`
          : { cosmos: 'With nothing picked, it finds the closest Cosmos material to each reference photo.', build: 'With nothing picked, it builds a V-Ray material from each reference photo.', ask: 'With nothing picked, it asks you which material first.' }[o.fallback],
        `It imports each .vrmat and assigns it at ${o.scale === 'real' ? 'real-world scale' : 'a scale that fits each object'}${o.grain ? ', with the grain along each member' : ''}.`,
        o.rest === 'clay' ? 'Everything else goes clay, and it lists what went where.' : 'It lists what went where.',
      ],
      fields: [
        { k: 'apply', t: 'menu', l: 'Apply to', o: [['marked', 'What I marked'], ['selected', 'What is selected'], ['layers', 'Whole layers of what I marked'], ['all', 'Everything']], d: 'marked' },
        { k: 'fallback', t: 'menu', l: 'With nothing picked', o: [['cosmos', 'Closest Cosmos match'], ['build', 'Build from the photo'], ['ask', 'Ask me']], d: 'cosmos' },
        { k: 'scale', t: 'seg', l: 'Scale', o: [['real', 'Real-world'], ['fit', 'Fit each object']], d: 'real' },
        { k: 'grain', t: 'toggle', l: 'Grain along each member', hint: 'Box mapping on the long axis; Z up for posts', d: true },
        { k: 'rest', t: 'seg', l: 'Everything else', o: [['keep', 'Leave as is'], ['clay', 'Clay']], d: 'keep' },
      ],
      library: 'materials',
      brief: (o, c) => [
        `Materials go on ${{ marked: 'the surfaces under or inside the user’s marks', selected: 'the selected objects', layers: 'every object on the layers of what the user marked', all: 'the whole model' }[o.apply]}.`,
        c.picks.length ? 'Use these materials: ' + c.picks.map(pickText).join('; ') + '. Assign each with obj.RenderMaterial.'
          : { cosmos: `For each reference image on the capture, pick the closest Chaos Cosmos material already downloaded (${(ENV().cosmos || {}).root || 'the user’s Cosmos Packages folder'}\\Materials), import it with -_vrayImportMaterial and assign it with obj.RenderMaterial to the objects under or inside the matching marks, or the selection. If nothing is close, build a V-Ray material from the photo's colour and roughness.`,
              build: 'Build a V-Ray material from each reference photo (colour, pattern scale, roughness) and apply it to the objects under it or to the marked ones.',
              ask: 'If the user’s note does not name a material, ask the user which before assigning anything.' }[o.fallback],
        o.scale === 'real' ? 'Use real-world scale (the tile size in each Cosmos package name).' : 'Fit the mapping to each object.',
        o.grain ? 'Run any grain along each member\'s long axis (box mapping, Z up for vertical members).' : '',
        o.rest === 'clay' ? 'Everything else: ' + CLAY : '',
        'List what went where.',
      ],
      tag: (o, c) => 'Materials' + (c.picks.length ? ': ' + c.picks.map(p => p.name).join(', ') : ''),
    },

    lighting: {
      label: 'Lighting', icon: 'sun', blurb: 'Light the scene: sun and sky, an HDRI, or a studio.',
      runs: o => [runIn('sun', vrayName(), { sun: 'Sun and Sky', hdri: 'dome light with an HDRI', studio: 'soft dome light', keep: 'your lights as they are' }[o.light]),
        runIn('pin', 'Site', ENV().sun && ENV().sun.lat != null && ENV().sun.set !== false ? `${ENV().sun.lat}, ${ENV().sun.lon} from Rhino` : 'not set in Rhino: Claude asks'), MCP()],
      how: o => [
        o.light === 'keep' ? 'Claude leaves your lights alone.' : o.light === 'sun' ? 'Claude reads the site from Rhino\'s sun settings (and asks if there is none).' : 'Claude turns the V-Ray sun off.',
        { sun: o.time === 'exact' ? `It sets the V-Ray Sun and Sky for ${o.date || 'the date'} at ${o.clock || 'the time'}.` : `It sets the V-Ray Sun and Sky for ${{ golden: 'golden hour', morning: 'the morning', midday: 'midday', afternoon: 'late afternoon', blue: 'blue hour' }[o.time]}, ${{ summer: 'in summer', equinox: 'at an equinox', winter: 'in winter' }[o.season]}.`,
          hdri: 'It lights the scene with a dome light and the HDRI you picked, turned so the sun sits where you said.', studio: 'It sets a soft white dome and a ground that catches contact shadows.', keep: '' }[o.light],
        o.light !== 'keep' ? (o.interior ? 'It lights the rooms inside and sets the exposure.' : 'It sets the exposure.') : '',
        o.preview ? 'It renders a one-minute V-Ray preview from this camera and shows it to you.' : '',
      ],
      fields: [
        { k: 'light', t: 'seg', l: 'Light', o: [['sun', 'Sun'], ['hdri', 'HDRI'], ['studio', 'Studio'], ['keep', 'Keep mine']], d: 'sun',
          hint: { sun: 'V-Ray Sun and Sky at your site', hdri: 'A dome light with an HDRI from your library', studio: 'No sun: a soft white dome, for clay and model shots', keep: 'Your lights stay exactly as they are' } },
        { k: 'time', t: 'menu', l: 'Time of day', o: [['golden', 'Golden hour'], ['morning', 'Morning'], ['midday', 'Midday'], ['afternoon', 'Late afternoon'], ['blue', 'Blue hour'], ['exact', 'An exact date and time']], d: 'golden', when: o => o.light === 'sun' },
        { k: 'season', t: 'seg', l: 'Season', o: [['summer', 'Summer'], ['equinox', 'Equinox'], ['winter', 'Winter']], d: 'summer', when: o => o.light === 'sun' && o.time !== 'exact' },
        { k: 'date', t: 'date', l: 'Date', d: '2026-12-21', when: o => o.light === 'sun' && o.time === 'exact' },
        { k: 'clock', t: 'time', l: 'Time', d: '17:30', when: o => o.light === 'sun' && o.time === 'exact' },
        { k: 'sky', t: 'seg', l: 'Sky', o: [['clear', 'Clear'], ['hazy', 'Hazy'], ['overcast', 'Overcast']], d: 'clear', when: o => o.light === 'sun' },
        { k: 'turn', t: 'menu', l: 'Turn it so the sun is', o: Object.entries(TURNS).map(([k, v]) => [k, v[0].toUpperCase() + v.slice(1)]), d: 'behind_left', when: o => o.light === 'hdri' },
        { k: 'backdrop', t: 'seg', l: 'Background', o: [['white', 'White'], ['grey', 'Grey'], ['black', 'Black']], d: 'white', when: o => o.light === 'studio' },
        { k: 'interior', t: 'toggle', l: 'Light the rooms inside', hint: 'A glow behind the glazing', d: false, when: o => o.light !== 'keep' },
        { k: 'kelvin', t: 'seg', l: 'Inside light', o: [['3000', 'Warm 3000 K'], ['4000', '4000 K'], ['5500', 'Cool 5500 K']], d: '3000', when: o => o.light !== 'keep' && o.interior },
        { k: 'exposure', t: 'seg', l: 'Expose for', o: [['sky', 'The sky'], ['building', 'The building']], d: 'sky', when: o => o.light !== 'keep',
          hint: { sky: 'The sky keeps its colour', building: 'The facade reads; the sky may go bright' } },
        { k: 'preview', t: 'toggle', l: 'Test render after', hint: 'A one-minute V-Ray preview from this camera', d: true },
      ],
      library: 'hdris',
      setups: [
        { id: 'b:golden', name: 'Golden hour', summary: 'Low warm sun on a summer evening', sw: [[255, 170, 90], [236, 104, 120]], o: { light: 'sun', time: 'golden', season: 'summer', sky: 'clear' } },
        { id: 'b:midday', name: 'Midday, summer', summary: 'High crisp sun, short shadows', sw: [[120, 180, 245], [235, 244, 255]], o: { light: 'sun', time: 'midday', season: 'summer', sky: 'clear' } },
        { id: 'b:overcast', name: 'Overcast', summary: 'Soft light, no hard shadows', sw: [[150, 158, 168], [220, 224, 228]], o: { light: 'sun', sky: 'overcast' } },
        { id: 'b:blue', name: 'Blue hour, lit inside', summary: 'Twilight sky, warm rooms', sw: [[32, 52, 110], [250, 170, 90]], o: { light: 'sun', time: 'blue', interior: true, kelvin: '3000', exposure: 'building' } },
        { id: 'b:studio', name: 'Studio white', summary: 'No sun; a soft white dome', sw: [[255, 255, 255], [200, 202, 206]], o: { light: 'studio', backdrop: 'white' } },
      ],
      brief: (o, c) => lightingBrief(o, c, true),
      tag: o => 'Lighting: ' + ({ sun: o.sky === 'overcast' ? 'overcast' : o.time === 'exact' ? `sun, ${o.date} ${o.clock}` : `sun, ${{ golden: 'golden hour', morning: 'morning', midday: 'midday', afternoon: 'late afternoon', blue: 'blue hour' }[o.time]}`, hdri: 'HDRI', studio: 'studio', keep: 'keep' }[o.light]),
    },

    render: {
      label: 'Render', icon: 'camera', blurb: 'Render this view as a finished image.',
      runs: o => o.make === 'render' ? (ENGINE[o.engine] || ENGINE.vray).runs(o)
        : [runIn('camera', vrayName(), `in ${rhinoName()}`), o.make === 'montage' ? runIn('image', 'Photo match', 'tools/cm_photomatch.py') : runIn('image', 'Style board', 'tools/cm_style.py'), MCP()],
      how: o => {
        if (o.make === 'montage') return ['Drop your site photo, double-click it, and place 6 or more Photo match points on it, then the same points on the model.',
          'Claude solves the photo\'s camera and shows you the model edges over the photo first.', 'It sets the sun from the photo\'s date and time.', 'It renders the proposal alone in V-Ray and composites it into the photo.'];
        if (o.make === 'board') return ['Drop a render whose look you like (set it as a look to match).', 'Claude renders three different looks from this camera: real one-minute V-Ray previews.',
          'You flip through them with ClaudeReply and pick one, with tweaks marked on it.', 'It sets that look up for real in your V-Ray settings.'];
        return renderHow(o);
      },
      fields: [
        { k: 'make', t: 'seg', l: 'Make', o: [['render', 'Render'], ['montage', 'Photo montage'], ['board', 'Style board']], d: 'render',
          hint: { render: 'One finished image of this view', montage: 'The design rendered into a site photo you drop on the capture', board: 'Three looks from a render you like; pick one and it is set up for real' } },
        { k: 'engine', t: 'seg', l: 'Engine', o: [['vray', 'V-Ray'], ['raytraced', 'Raytraced'], ['python', 'Python script']], d: 'vray', when: o => o.make === 'render',
          hint: { vray: 'Production quality, with your V-Ray materials and lights. The slowest', raytraced: 'Rhino\'s own Cycles renderer. Leaves your V-Ray settings alone', python: 'A script draws the saved model outside Rhino: linework, a white model, shadows' } },
        { k: 'engset', t: 'engine', when: o => o.make === 'render' },
        { k: 'size', t: 'menu', l: 'Size', o: () => SIZES.map(s => [s[0], s[0] === 'capture' ? `Same as the capture, ${S.iw} x ${S.ih}` : s[1]]), d: 'hd', when: o => o.make === 'render',
          hint: (v, o) => { const [w, h] = sizeWH(o); const s = SIZES.find(x => x[0] === v); return v === 'custom' || (s && s[1].includes(`${w} x ${h}`)) ? '' : `${w} x ${h} px`; } },
        { k: 'orient', t: 'seg', l: 'Orientation', o: [['land', 'Landscape'], ['port', 'Portrait']], d: 'land', when: o => o.make === 'render' && ['720p', 'hd', '4k', 'a3', 'a2'].includes(o.size) },
        { k: 'w', k2: 'h', t: 'wh', l: 'Width and height', d: [2400, 1600], when: o => o.make === 'render' && o.size === 'custom' },
        { k: 'camera', t: 'menu', l: 'Camera', d: 'capture', when: o => o.make === 'render',
          o: () => [['capture', 'This capture\'s view'], ['two_point', 'Two-point, eye level'], ['axo', 'Axonometric']].concat((ENV().named_views || []).map(n => ['nv:' + n, 'Named view: ' + n])) },
        { k: 'lens', t: 'seg', l: 'Lens', o: [['24', '24 mm'], ['28', '28 mm'], ['35', '35 mm']], d: '24', when: o => o.make === 'render' && o.camera === 'two_point' },
        { k: 'light', t: 'menu', l: 'Light', o: [['keep', 'Keep what is set'], ['lighting', 'Use my Lighting options']], d: 'keep', when: o => o.make === 'board' || (o.make === 'render' && o.engine !== 'python'),
          hint: v => v === 'lighting' ? 'As set under Lighting: ' + TASKS.lighting.tag(opts('lighting')).replace(/^Lighting: /, '') : '' },
        { k: 'look', t: 'seg', l: 'Look', o: [['materials', 'Materials'], ['clay', 'Clay']], d: 'materials', when: o => o.make === 'render' && o.engine !== 'python' },
        { k: 'people', t: 'seg', l: 'People', o: [['none', 'None'], ['few', 'A few'], ['busy', 'Busy']], d: 'none', when: o => o.make === 'montage' || (o.make === 'render' && o.engine !== 'python'),
          hint: v => v === 'none' ? '' : (ENV().cosmos && !ENV().cosmos.people ? 'No Cosmos people are downloaded yet: Claude asks before downloading any' : 'Your downloaded Cosmos people. Pins say where, arrows which way') },
        { k: 'bg', t: 'seg', l: 'Background', o: [['sky', 'Sky'], ['white', 'White'], ['alpha', 'Transparent']], d: 'sky', when: o => o.make === 'render' },
        { k: 'format', t: 'seg', l: 'Format', o: o => [['png', 'PNG'], ['jpg', 'JPG']].concat(o && o.engine === 'python' ? [] : [['exr', 'EXR']]), d: 'png', when: o => o.make === 'render' },
        { k: 'save', t: 'menu', l: 'Save to', d: 'doc', o: o => [['doc', 'Next to the .3dm'], ['choose', o && o.folder ? 'Folder: ' + o.folder.split(/[\\/]/).pop() : 'Choose a folder...']],
          hint: (v, o) => v === 'doc' ? (ENV().render_dir ? shortPath(ENV().render_dir) : 'The file isn\'t saved yet, so Claude asks where') : shortPath(o.folder || '') },
        { k: 'open', t: 'toggle', l: 'Open it when done', d: true },
      ],
      setups: [
        { id: 'b:quick', name: 'Quick preview', summary: 'V-Ray, 1280 x 720, about a minute', o: { make: 'render', engine: 'vray', size: '720p', orient: 'land', quality: 'draft' } },
        { id: 'b:final4k', name: 'Final 4K', summary: 'V-Ray, 3840 x 2160, clean', o: { make: 'render', engine: 'vray', size: '4k', orient: 'land', quality: 'final' } },
        { id: 'b:a3', name: 'A3 board', summary: '300 dpi landscape, for print', o: { make: 'render', engine: 'vray', size: 'a3', orient: 'land', quality: 'final' } },
        { id: 'b:clay', name: 'Clay study', summary: 'White model, HD', o: { make: 'render', engine: 'vray', look: 'clay', size: 'hd', quality: 'high' } },
        { id: 'b:script', name: 'Script render', summary: 'Python: linework, white model and shadows in seconds', o: { make: 'render', engine: 'python', py_renderer: 'moderngl', size: 'capture' } },
      ],
      brief: (o, c) => renderBrief(o, c),
      tag: o => o.make === 'montage' ? 'Render: photo montage' : o.make === 'board' ? 'Render: style board'
        : `Render: ${(ENGINE[o.engine] || ENGINE.vray).l}, ${sizeWH(o).join(' x ')}${o.engine === 'python' ? '' : ', ' + QUALITY[o.quality].l.toLowerCase()}`,
    },

    analyse: {
      label: 'Analyse', icon: 'chart', blurb: 'Get a diagram out of the model.',
      runs: () => [runIn('chart', 'ClaudeMark tools', 'cm_analyse.py, drawn by cm_post.py'), runIn('cube', rhinoName()), MCP()],
      how: o => [
        { section: 'Draw a straight line across the view: that is where it cuts.', sun: 'Draw a box or a loop round the area to test.', shadow: 'The study is of this exact view; no mark is needed.', isovist: 'Drop a pin where you stand.', eye: 'Drop a pin where you stand; an arrow says which way to look.' }[o.kind],
        `Claude runs ${ANALYSES[o.kind].toLowerCase()} on the model.`,
        o.on_view ? 'It draws the result over your view' + (o.svg && ['section', 'isovist'].includes(o.kind) ? ', and as an SVG and a PNG.' : '.') : 'It saves the result as an image.',
        'It puts your view and sun back as they were.',
      ],
      fields: [
        { k: 'kind', t: 'menu', l: 'Analysis', o: Object.entries(ANALYSES), d: 'section' },
        { k: 'look', t: 'seg', l: 'Look', o: [['away', 'Away from the camera'], ['toward', 'Towards it']], d: 'away', when: o => o.kind === 'section' },
        { k: 'beyond', t: 'toggle', l: 'Linework beyond', hint: 'Graded by depth', d: true, when: o => o.kind === 'section' },
        { k: 'dates', t: 'chips', l: 'Dates', o: Object.entries(DATES), d: ['jun', 'dec'], when: o => o.kind === 'sun' || o.kind === 'shadow' },
        { k: 'from', t: 'time', l: 'From', d: '08:00', when: o => o.kind === 'shadow' },
        { k: 'to', t: 'time', l: 'To', d: '18:00', when: o => o.kind === 'shadow' },
        { k: 'step', t: 'seg', l: 'Every', o: [['1', '1 hour'], ['2', '2 hours']], d: '1', when: o => o.kind === 'shadow' },
        { k: 'eye_h', t: 'num', l: 'Eye height', unit: 'm', d: 1.6, min: 0.3, max: 3, step: 0.05, when: o => o.kind === 'isovist' || o.kind === 'eye' },
        { k: 'rays', t: 'seg', l: 'Rays', o: [['360', '360'], ['720', '720'], ['1440', '1440']], d: '720', when: o => o.kind === 'isovist', hint: 'More rays: finer edges, slower' },
        { k: 'lens', t: 'seg', l: 'Lens', o: [['24', '24 mm'], ['28', '28 mm'], ['35', '35 mm']], d: '24', when: o => o.kind === 'eye' },
        { k: 'on_view', t: 'toggle', l: 'Draw it on my view', d: true },
        { k: 'svg', t: 'toggle', l: 'Also as SVG', hint: 'Editable linework for Illustrator', d: true, when: o => o.kind === 'section' || o.kind === 'isovist' },
      ],
      brief: o => analyseBrief(o),
      tag: o => 'Analyse: ' + ANALYSES[o.kind].toLowerCase(),
    },

    critique: {
      label: 'Critique', icon: 'crit', blurb: 'Get a crit from the critic or the councils, marked on your view.',
      runs: o => WHO[o.who].runs(o).concat([runIn('reply', 'ClaudeReply', o.marks ? 'findings come back on your view' : 'findings in chat only')]),
      how: o => [
        'Claude builds a brief from your view, your marks and your note. Your note is the claim it tests.',
        o.confirm && isCouncil(o) ? 'It checks the brief with you before the review starts.' : '',
        WHO[o.who].how,
        isCouncil(o) ? 'It fact-checks the report against your model before passing it on.' : '',
        o.marks ? 'Findings come back on your view as C1, C2 and so on: type ClaudeReply in Rhino to answer them.' : 'Findings come back in chat, numbered.',
      ],
      fields: [
        { k: 'who', t: 'menu', l: 'Who reviews it', o: Object.keys(WHO).map(k => [k, k === 'critic' ? 'Critic: what is wrong with this' : WHO[k].l]), d: 'critic', hint: v => WHO[v].hint },
        { k: 'length', t: 'seg', l: 'Length', o: [['quick', 'Quick'], ['full', 'Full']], d: 'full', when: o => o.who === 'council' || o.who === 'both',
          hint: v => v === 'quick' ? 'About 500 words from the architects' : 'The whole report' },
        { k: 'image', t: 'toggle', l: 'Show it the image', d: false, when: o => isCouncil(o),
          hint: v => v ? 'The council reads marked.png too: more tokens, see the cost above' : 'Claude describes the view in the brief instead: far cheaper' },
        { k: 'confirm', t: 'toggle', l: 'Check the brief with me first', d: true, when: o => isCouncil(o) },
        { k: 'focus', t: 'text', l: 'Focus on', ph: 'e.g. the canopy and the street edge', d: '' },
        { k: 'marks', t: 'toggle', l: 'Marks on my view', hint: 'Findings come back as a ClaudeMark reply', d: true },
      ],
      setups: [
        { id: 'b:quickarch', name: 'Quick council', summary: 'Architects, about 500 words, about 11k tokens', o: { who: 'council', length: 'quick', image: false } },
        { id: 'b:fullboth', name: 'Full review', summary: 'Both councils, in one reply', o: { who: 'both', length: 'full' } },
      ],
      brief: o => critiqueBrief(o),
      tag: o => 'Critique: ' + WHO[o.who].l.toLowerCase(),
    },
  };
  const TASK_ORDER = ['general', 'model', 'fix', 'materials', 'lighting', 'render', 'analyse', 'critique'];

  function lightingBrief(o, c, own) {
    const L = [];
    if (o.light === 'keep') L.push('Keep the user’s lights as they are.');
    else if (o.light === 'sun') {
      const when = o.time === 'exact' ? `${o.date} at ${o.clock} local time` : `${LIGHT_TIMES[o.time]} ${SEASONS[o.season]} (find the date and time that give it there, and say which)`;
      L.push(`V-Ray Sun and Sky at ${site()}: ${when}.`, SKIES[o.sky]);
    } else if (o.light === 'hdri') {
      const p = (S.picks.lighting || [])[0];
      L.push(p ? `No sun. A V-Ray dome light with this HDRI: ${p.file}` + (/\.vrmat$/i.test(p.file) ? ' (a ready dome light: import it).' : '.') : 'No sun. A V-Ray dome light with an HDRI that suits the view (none is picked: ask the user, or offer a Cosmos HDRI, name and size first, before downloading).',
        `Turn it so the sun is ${TURNS[o.turn]}.`);
    } else if (o.light === 'studio') L.push(`No sun. A soft white dome light (or a studio HDRI) and a ground plane that catches soft contact shadows; a ${o.backdrop} background.`);
    if (o.light !== 'keep' && o.interior) L.push(`Light the occupied rooms (${o.kelvin} K): build Rhino rectangular lights, then _-vrayLight _Convert (notes/RHINO-VRAY-NOTES.md section vray-lights-by-script). The glazing should glow.`);
    if (o.light !== 'keep') L.push(o.exposure === 'sky' ? 'Expose so the sky keeps its colour.' : 'Expose for the building; the sky may go a little bright.');
    if (own && o.preview) L.push('Then render a one-minute V-Ray preview from this capture\'s camera and show it to the user. ' + note_('render_note'));
    return L;
  }
  function pyLayersText(o) {
    const ls = o.py_layers || [], parts = [];
    if (ls.includes('white')) parts.push('a white model');
    if (ls.includes('lines')) parts.push(`linework (${o.py_edges === 'all' ? 'every edge' : 'outlines and creases'}, ${o.py_weight} weight)`);
    if (ls.includes('shadows')) parts.push(`shadows from ${o.py_sun === 'rhino' ? 'Rhino\'s sun at the site' : o.py_sun === 'overhead' ? 'an overhead light' : 'the sun in the user’s Lighting options'}`);
    if (ls.includes('ao')) parts.push('ambient occlusion');
    return listText(parts);
  }
  function renderBrief(o, c) {
    const L = [];
    if (o.make === 'montage') { L.push(MONTAGE); if (o.people !== 'none') L.push(peopleText(o)); L.push(saveText(o, 'png')); return L; }
    if (o.make === 'board') { L.push(BOARD); if (o.light === 'lighting') L.push('Start each look from the user’s Lighting options: ' + lightingBrief(opts('lighting'), c, false).join(' ')); return L; }
    const [w, h] = sizeWH(o);
    const fmt = o.engine === 'python' && o.format === 'exr' ? 'png' : o.format;
    if (o.engine === 'python') {
      if (o.py_renderer === 'blender') L.push(`Render this view with a Python script in Blender, not V-Ray: save the .3dm first (the script reads the saved file, so Rhino stays free), read its render meshes with rhino3dm, and build the scene in Blender 4.5 headless (blender.exe on PATH, or say where it is in your standing instructions) with the Rhino layer or material colours. Light it from Rhino's sun at the site and render with Cycles (OptiX) at ${o.py_samples} adaptive samples, denoised, ${w} x ${h} px.`);
      else L.push(`Render this view with a Python script outside Rhino, not V-Ray: save the .3dm first (the script reads the saved file, so Rhino stays free), read the render meshes cached in it with rhino3dm (face.GetMesh(rhino3dm.MeshType.Any), Extrusion.GetMesh; blocks through their definitions and transforms; objects never shaded have no mesh, so count them and say so), and draw them with moderngl at ${w} x ${h} px, ${o.py_ss}x supersampled: ${pyLayersText(o)}.`);
    } else if (o.engine === 'raytraced') {
      L.push(`Render this view with Rhino's own Raytraced (Cycles) display mode, not V-Ray: set the viewport to Raytraced with ${o.rt_bounces} light bounces, caustics ${o.rt_caustics ? 'on' : 'off'} and ${o.rt_denoiser === 'off' ? 'no denoiser' : 'the ' + (o.rt_denoiser === 'intel' ? 'Intel' : 'NVIDIA') + ' denoiser'}, let it reach about ${QUALITY[o.quality].passes} passes, then capture it with -_ViewCaptureToFile at ${w} x ${h} px. Leave the user’s V-Ray settings alone.`);
    } else L.push(`Render this view with ${vrayName()} in the user’s Rhino over the MCP.`);
    const cam = String(o.camera);
    L.push(cam === 'capture' ? 'Camera: this capture\'s camera exactly (meta.json camera: location, target, up, lens, frustum)' + (o.engine === 'python' ? '.' : '; save it as a named view and restore it before rendering.')
      : cam === 'two_point' ? `Camera: two-point perspective at 1.6 m eye height, lens ${o.lens} mm, verticals vertical, keeping the user’s framing from the capture${o.engine === 'python' ? '' : '; save it as a named view'}.`
        : cam === 'axo' ? 'Camera: a parallel projection looking down from the corner that best shows the marked area' + (o.engine === 'python' ? '.' : ', saved as a named view.')
          : `Camera: the user’s named view "${cam.slice(3)}"` + (o.engine === 'python' ? ' (read it from the saved file).' : '; restore it before rendering.'));
    if (o.engine === 'vray') {
      L.push(`Output ${w} x ${h} px. Quality: ${QUALITY[o.quality].text}${o.vr_limit ? `, at most ${o.vr_limit} min` : ''}; the ${o.vr_sampler} image sampler; global illumination ${o.vr_gi === 'lc' ? 'brute force with the light cache' : 'brute force for both bounces'}; ${DENOISERS[o.vr_denoiser]}.` +
        ((o.vr_elements || []).length ? ` Save these render elements beside the image: ${listText(o.vr_elements.map(k => ({ alpha: 'alpha', zdepth: 'Z-depth', matid: 'material ID', lighting: 'lighting', reflection: 'reflection' })[k]))}.` : '') +
        (o.vr_lens ? ' Lens effects on (bloom and glare).' : '') + (o.vr_vfb ? '' : ' Turn the user’s VFB colour corrections off for this render, then back on.'));
    }
    if (o.engine !== 'python') {
      if (o.light === 'lighting') L.push('Light it with the user’s Lighting options: ' + lightingBrief(opts('lighting'), c, false).join(' '));
      if (o.look === 'clay') L.push(CLAY);
      if (o.people !== 'none') L.push(peopleText(o));
    }
    if (o.bg === 'white') L.push('Background: plain white' + (o.engine === 'python' ? '.' : ' (an environment override, or composite it on white).'));
    else if (o.bg === 'alpha') L.push('Background: transparent; save with an alpha channel.');
    else if (o.engine === 'python') L.push('Background: a soft sky gradient.');
    L.push(saveText(o, fmt));
    if (o.engine === 'vray') L.push(note_('render_note'));
    return L;
  }
  function peopleText(o) {
    const cos = ENV().cosmos || {};
    return `Populate the view with ${o.people === 'busy' ? 'a busy crowd' : 'a few people'} from the user’s downloaded Chaos Cosmos library (${cos.root ? cos.root + '\\3D_Models\\People' : 'Cosmos 3D_Models\\People'}: walking, standing and sitting, singles and pairs; check their Previews to pick). Each pin is one person or pair, an arrow is a walker heading from tail to tip, and a loop or box is an area to populate. With no marks, put them where the camera sees use: walkers on routes, standers at edges and entries, sitters on seating. Place with -_vrayCosmos 1 <package uuid> and w-prefixed points so they arrive at true size, then Esc out of the placing loop (notes/RHINO-VRAY-NOTES.md section vray-cosmos-proxies-scripting). Stand each on the surface below it, check heights read 1.5 to 1.9 m, keep them clear of geometry, turn them to their heading or towards each other, and don't repeat a person close by. Layer "People (Cosmos)".` +
      (cos.people ? ' Use downloaded assets only; ask before downloading more (name and size first).' : ' None are downloaded yet: ask the user before downloading any (name and size first).');
  }
  function saveText(o, fmt) {
    const where = o.save === 'choose' && o.folder ? `"${o.folder}"` : ENV().render_dir ? `"${ENV().render_dir}" (make the folder if needed)` : 'a Renders folder beside the .3dm (if the file is unsaved, ask the user where)';
    return `Save it as ${String(fmt).toUpperCase()} in ${where}, with a dated name.` + (o.open ? ' Then open it so the user sees it.' : '');
  }
  function analyseBrief(o) {
    const dates = listText((o.dates && o.dates.length ? o.dates : ['jun', 'dec']).map(d => DATES[d]));
    const on = o.on_view;
    return [{
      section: `Cut a vertical section along the marked line (the first straight pen line or arrow). It looks ${o.look === 'away' ? 'away from' : 'towards'} the camera unless the user’s note says otherwise. Run section() in tools/cm_analyse.py, then tools/cm_post.py section: poche cut${o.beyond ? ', linework beyond graded by depth' : ', no linework beyond'} (notes/RHINO-VRAY-NOTES.md section section-perspective-pipeline), as ${o.svg ? 'SVG and PNG' : 'PNG'}${on ? ', plus the cut line drawn on the user’s view' : ''}.`,
      sun: `Hours of direct sun on the surfaces inside each loop or box, from the user’s camera's view of them, on ${dates} at ${site()}. Run sun_hours() in tools/cm_analyse.py (chunked, it can be slow), then tools/cm_post.py sun: ${on ? 'a heatmap over the user’s view' : 'a heatmap'} with a legend, one panel per date, and the area-weighted mean.`,
      shadow: `Shadow study from the user’s exact capture camera: Rendered captures with the sun stepped every ${o.step === '2' ? '2 hours' : 'hour'} from ${o.from} to ${o.to} on ${dates} at ${site()}. Run shadow_study() in tools/cm_analyse.py, then tools/cm_post.py shadows for the labelled contact sheet. Restore the user’s sun and view afterwards.`,
      isovist: `For each pin: the isovist at ${o.eye_h} m eye height from where the pin lands, by ${o.rays} horizontal rays against the visible model. Run isovist() in tools/cm_analyse.py, then tools/cm_post.py isovist: a plan with the obstacles cut at eye height, the visible field and its area${o.svg ? ' (SVG and PNG)' : ''}${on ? ', plus the field drawn on the user’s view' : ''}.`,
      eye: `For each pin: a two-point named view at ${o.eye_h} m eye height, lens ${o.lens} mm, looking along the arrow if there is one, else toward the next pin, else the capture's own direction. Run eye_level() in tools/cm_analyse.py; it saves the named views and a Rendered capture of each, and puts the user’s view back.`,
    }[o.kind]];
  }
  const isCouncil = o => o.who === 'council' || o.who === 'engineering' || o.who === 'both';
  function critiqueBrief(o) {
    const post = o.marks ? ` Then post the points it lands on as a ClaudeMark reply with ${POST_REPLY}: one numbered mark (C1, C2...) on the element each point is about, with a short label, the full argument in chat under the same numbers. Tell the user to type ClaudeReply in Rhino.`
      : ' Give the findings in chat only, numbered C1, C2...; do not post a ClaudeMark reply.';
    const L = [];
    if (o.who === 'critic') L.push('Run the critic agent on this capture: marked.png, meta.json and the user’s note as the claim. If the note asserts nothing, ask the user what the user is claiming first.' + post);
    else if (o.who === 'council' || o.who === 'engineering') L.push(councilText(o.who, o) + post);
    else if (o.who === 'both') L.push('Run both councils: the architectural council first, then the engineering council. ' + councilText('council', o) + ' ' + councilText('engineering', o) +
      (o.marks ? ` Then post both in one ClaudeMark reply with ${POST_REPLY}: number the architectural points C1, C2... and carry the numbering on for the engineering ones, each on the element it is about with a short label, the full arguments in chat under the same numbers. Tell the user to type ClaudeReply in Rhino.`
        : ' Give the findings in chat only, numbered on from the architects to the engineers; do not post a ClaudeMark reply.'));
    else L.push('Test the marked design against the project brief (from CLAUDE.md or memory; if you don\'t have it, ask for it first): what does the brief ask for, where does the design deliver it, and where do they part?' + post);
    if ((o.focus || '').trim()) L.push(`Focus on: ${o.focus.trim()}.`);
    return L;
  }

  // ---------------------------------------------------------------- the options model: defaults, the user’s values, setups
  function taskDef(id) { return TASKS[id] || TASKS.general; }
  function defaults(id) {
    const o = {};
    for (const f of taskDef(id).fields.concat(id === 'render' ? ENGINE_FIELDS() : [])) {
      if (!f.k || f.t === 'engine' || f.k in o) continue;
      if (f.t === 'wh') { o[f.k] = f.d[0]; o[f.k2] = f.d[1]; } else o[f.k] = Array.isArray(f.d) ? f.d.slice() : f.d;
    }
    return o;
  }
  function opts(id) {
    id = id || S.task; const o = Object.assign(defaults(id), (S.settings.task_opts || {})[id] || {});
    if (id === 'render' && !ENGINE[o.engine]) o.engine = o.engine === 'viewport' ? 'python' : 'vray';   // the first 4.1 build had a Rendered viewport engine
    return o;
  }
  function setOpts(id, o) { if (!S.settings.task_opts) S.settings.task_opts = {}; S.settings.task_opts[id] = o; saveSettings(); }
  function setOpt(k, v) {
    const o = opts(S.task); o[k] = v;
    if (k === 'size' && v === 'custom' && !(S.settings.task_opts && S.settings.task_opts.render && S.settings.task_opts.render.w)) { const wh = sizeWH(opts('render')); o.w = wh[0]; o.h = wh[1]; }
    if (S.task === 'model' && k === 'lod') { o.parts = LOD_PARTS[v].slice(); o.min = LOD_MIN[v]; }   // the level sets what is modelled in full; any can be changed after
    setOpts(S.task, o); refreshSidebar();
    if (S.task === 'render' && k === 'engine') openEnginePop();   // its own settings pop up
  }
  const optList = (f, o) => (typeof f.o === 'function' ? f.o(o) : f.o) || [];
  const hintOf = (f, v, o) => typeof f.hint === 'function' ? f.hint(v, o) : (f.hint && typeof f.hint === 'object' ? f.hint[v] : f.hint);
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function setupsFor(id) {
    const hidden = S.settings.hidden_setups || [];
    const built = (taskDef(id).setups || []).filter(s => !hidden.includes(s.id)).map(s => Object.assign({ builtin: true }, s));
    const mine = (S.allPresets[id] || []).filter(p => p.kind === 'setup' && p.options).map(p => ({ id: p.id, name: p.name, summary: p.summary || '', o: p.options, builtin: false }));
    return built.concat(mine);
  }
  function setupOn(s, o) { return Object.keys(s.o).every(k => same(s.o[k], o[k])); }
  function applySetup(s) {
    setOpts(S.task, Object.assign(opts(S.task), clone(s.o))); refreshSidebar();
    toast(`${s.name}: options set`, 'check', 1600);
  }
  function ctxFor(id) { return { picks: S.picks[id] || [] }; }
  function briefFor(id) {
    id = id || S.task; const def = taskDef(id), o = opts(id), c = ctxFor(id);
    const L = [].concat(def.brief(o, c) || []);
    for (const st of S.strokes.filter(x => x.kind === 'ref')) L.push(roleText(st, id));
    const mine = ((S.settings.task_notes || {})[id] || '').trim();
    if (mine) L.push(`The user’s standing instructions for ${def.label.toLowerCase()} jobs: ${mine}`);
    return L.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  }
  function tagFor(id) { id = id || S.task; const d = taskDef(id); return d.tag ? d.tag(opts(id), ctxFor(id)) : ''; }

  // ---------------------------------------------------------------- the images on this capture, and what each is for
  const ROLES = {
    general: [['context', 'Context'], ['like', 'Make it like this'], ['precedent', 'A precedent']],
    fix: [['like', 'Make it like this'], ['context', 'Context']],
    model: [['like', 'Make it like this'], ['context', 'Context'], ['precedent', 'A precedent']],
    materials: [['material', 'Material to match'], ['context', 'Context']],
    lighting: [['light', 'Light to match'], ['context', 'Context']],
    render: [['look', 'Look to match'], ['site', 'Site photo'], ['context', 'Context']],
    analyse: [['style', 'Drawing style to copy'], ['context', 'Context']],
    critique: [['precedent', 'Precedent to test against'], ['context', 'Context']],
  };
  function defaultRole(id) { id = id || S.task; if (id === 'render' && opts('render').make === 'montage') return 'site'; return (ROLES[id] || ROLES.general)[0][0]; }
  function roleOf(st, id) { id = id || S.task; const list = ROLES[id] || ROLES.general; return st.role && list.some(r => r[0] === st.role) ? st.role : defaultRole(id); }
  function roleLabel(r, id) { const x = (ROLES[id || S.task] || ROLES.general).find(y => y[0] === r); return x ? x[1] : r; }
  function refName(st) {
    const s = String(st.source || ''); if (!s || /^pasted/i.test(s)) return 'Pasted image';
    if (/^dropped image /i.test(s)) return s.slice(14);
    if (/^https?:/i.test(s)) { try { const u = new URL(s); return u.hostname.replace(/^www\./, '') + ' image'; } catch (e) { return 'Web image'; } }
    return s.split(/[\\/]/).pop();
  }
  function roleText(st, id) {
    const r = roleOf(st, id), n = `${st.label} (${refName(st)})`;
    return {
      context: `${n} is context: use it to understand what the user means.`,
      like: `${n}: make what it sits over like this image.`,
      precedent: id === 'critique' ? `${n} is the precedent to test the marked design against: where the design does what it does, and where it doesn't.` : `${n} is a precedent: read what it does before you design.`,
      material: `${n}: match its material on the surfaces under it.`,
      light: `${n}: match the light in this image: the time of day, the sun's direction, the colour temperature and the contrast.`,
      look: `${n}: match this image's look: framing, lens, exposure and post.`,
      site: `${n} is the site photo for the montage; its Photo match points pair with the model's.`,
      style: `${n}: draw the analysis in this image's graphic style: line weights, tones, colour and how it is labelled.`,
    }[r] || '';
  }

  // ---------------------------------------------------------------- the library: Cosmos downloads and the user’s own
  function mineLib(kind) {
    const cat = kind === 'hdris' ? 'lighting' : 'materials';
    return (S.allPresets[cat] || []).filter(p => !p.builtin && p.kind !== 'setup' && (p.kind === 'library' || (p.files && p.files.length)));
  }
  function libItems(kind) {
    const L = S.lib[kind]; const cosmos = L ? L.items : [];
    const mine = mineLib(kind).map(p => ({ id: 'preset:' + p.id, name: p.name, group: 'Yours', file: (p.files || [])[0] || '', src: 'mine', pid: p.id }));
    return mine.concat(cosmos.map(x => Object.assign({ src: 'cosmos' }, x)));
  }
  function pickText(p) {
    const f = p.file || '';
    const what = /\.(png|jpe?g|webp|bmp|tiff?|heic)$/i.test(f) ? `${p.name}: build a V-Ray material that matches the photo ${f} (colour, pattern scale, roughness)`
      : `${p.name}: import ${f} with -_vrayImportMaterial`;
    return `${what}, on ${onText(p.on)}`;
  }
  function onText(on) {
    if (on === 'sel') return 'the selected objects';
    if (on === 'rest') return 'everything not given another material';
    if (/^#\d+$/.test(on || '')) return `the surfaces under or inside mark ${on.slice(1)}`;
    return 'the surfaces the user’s note gives it (else the marked ones)';
  }
  function onLabel(on) { return on === 'sel' ? 'The selection' : on === 'rest' ? 'Everything else' : /^#\d+$/.test(on || '') ? 'Mark ' + on.slice(1) : 'As my note says'; }
  function numberedMarks() { return S.strokes.filter(st => NUMBERED.has(st.kind) && st.label != null).sort((a, b) => a.label - b.label); }
  function loadLibrary(kind) {
    if (S.lib['loading_' + kind]) return; S.lib['loading_' + kind] = true;
    Bridge.call('library', { kind }).then(r => { S.lib[kind] = { items: (r && r.items) || [], root: r && r.root }; })
      .catch(e => { S.lib[kind] = { items: [], error: e.message }; })
      .finally(() => { S.lib['loading_' + kind] = false; refreshSidebar(); });
  }
  function libGridHTML(kind) {
    const all = libItems(kind); const q = (S.lib.q || '').toLowerCase().trim(); const g = S.lib.g || '';
    const list = all.filter(x => (!g || x.group === g) && (!q || (x.name + ' ' + x.group).toLowerCase().includes(q)));
    const picked = new Set((S.picks[S.task] || []).map(p => p.id));
    const shown = list.slice(0, S.lib.limit || 48);
    if (!list.length) return `<div class="lib-empty">${all.length ? 'Nothing matches that.' : kind === 'hdris' ? 'No HDRIs yet: none are downloaded in Chaos Cosmos. Add your own .hdr or .exr below.' : 'No materials found in your Cosmos folder.'}</div>`;
    return shown.map(x => {
      const u = x.src === 'cosmos' ? (x.thumb || S.lib.thumbs[x.id]) : null;   // the user’s own never show the user’s photo: an icon instead
      return `<button class="mt ${picked.has(x.id) ? 'on' : ''}" data-lib="${esc(x.id)}" data-tip="${esc(x.name)}" data-sub="${esc(x.group + (x.tile ? ', ' + x.tile + ' tile' : ''))}"><span class="im">${u ? thumbImg(u) : icon(x.src === 'mine' ? (kind === 'hdris' ? 'sun' : 'image') : 'swatch')}</span><span class="nm">${esc(x.name)}</span><span class="ck">${icon('check', 's14')}</span></button>`;
    }).join('') + (list.length > shown.length ? `<button class="linkbtn more" data-libmore="1">Show all ${list.length}</button>` : '');
  }
  function libraryHTML(kind) {
    const picks = S.picks[S.task] || [];
    const isMat = kind === 'materials';
    let h = `<div class="sect"><span class="grow">${isMat ? 'Materials to use' : 'HDRI'}</span>${picks.length ? `<span class="sub">${picks.length} picked</span>` : ''}${helpDot('library', 'the library')}</div>`;
    if (picks.length) h += '<div class="grp using">' + picks.map((p, i) => {
      const u = p.src === 'cosmos' ? (p.thumb || S.lib.thumbs[p.id]) : null;
      return `<div class="row"><span class="th" data-lib-th="${esc(p.id)}">${u ? thumbImg(u) : icon(p.src === 'mine' ? 'image' : 'swatch', 's16')}</span><span class="rl">${esc(p.name)}<small>${esc(p.group || '')}</small></span>` +
        (isMat ? `<button class="pick" data-on="${i}"><span>${esc(onLabel(p.on))}</span>${icon('chevron', 's14')}</button>` : '') + `<button class="btn x" data-unpick="${i}" data-tip="Don't use it">${icon('x', 's14')}</button></div>`;
    }).join('') + '</div>';
    else h += `<div class="lib-hint">${isMat ? 'None picked, so Claude follows "With nothing picked" above. Pick from your library to choose them yourself.' : 'None picked.'}</div>`;
    const L = S.lib[kind]; const open = !!S.ui['lib_' + kind];
    h += `<details class="lib" data-det="lib_${kind}" data-dropzone="lib" data-kind="${kind}" ${open ? 'open' : ''}><summary>${icon('chevron', 's14')}<span>${isMat ? 'Your materials library' : 'Your HDRIs'}</span><span class="cnt">${L ? libItems(kind).length : ''}</span></summary>`;
    if (open) {
      if (!L) { h += '<div class="lib-empty">Loading your library...</div>'; loadLibrary(kind); }
      else {
        const groups = {}; for (const x of libItems(kind)) groups[x.group] = (groups[x.group] || 0) + 1;
        h += `<input class="txt lib-q" type="search" placeholder="Search ${isMat ? 'materials' : 'HDRIs'}" value="${esc(S.lib.q || '')}" spellcheck="false">`;
        if (Object.keys(groups).length > 1) h += `<div class="lib-groups"><button class="chip ${!S.lib.g ? 'on' : ''}" data-g="">All</button>${Object.keys(groups).map(g => `<button class="chip ${S.lib.g === g ? 'on' : ''}" data-g="${esc(g)}">${esc(g)}</button>`).join('')}</div>`;
        h += `<div class="lib-grid" id="lib-grid">${libGridHTML(kind)}</div>`;
      }
      h += `<button class="linkbtn" data-libadd="${kind}">${icon('plus', 's14')}Add ${isMat ? 'a .vrmat or a photo' : 'an .hdr or .exr'} to the library...</button>`;
    }
    return h + '</details>';
  }
  // thumbnails: made in Python on demand, a few at a time, for the tiles that scroll into sight
  let thumbIO = null; const thumbWant = new Set(); let thumbBusy = false;
  function observeThumbs() {
    if (!window.IntersectionObserver) return;
    if (!thumbIO) thumbIO = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting) { const id = e.target.dataset.lib; if (id && id.startsWith('cosmos:') && !S.lib.thumbs[id]) thumbWant.add(id); thumbIO.unobserve(e.target); }
      pumpThumbs();
    }, { root: $('#sidebar-body'), rootMargin: '160px' });
    for (const t of document.querySelectorAll('#sidebar-body .mt[data-lib^="cosmos:"]')) if (!t.querySelector('img')) thumbIO.observe(t);
    for (const p of [].concat(S.picks.materials || [], S.picks.lighting || [])) if (p.src === 'cosmos' && !p.thumb && !S.lib.thumbs[p.id]) thumbWant.add(p.id);
    pumpThumbs();
  }
  const thumbImg = u => `<img src="${esc(u)}" loading="lazy" decoding="async" alt="" draggable="false">`;
  function pumpThumbs() {
    if (thumbBusy || !thumbWant.size) return;
    const ids = [...thumbWant].slice(0, 12); ids.forEach(i => thumbWant.delete(i)); thumbBusy = true;
    Bridge.call('thumbs', { ids }).then(r => {
      Object.assign(S.lib.thumbs, (r && r.urls) || {});
      const done = new Set((r && r.done) || ids);
      for (const id of ids) if (!done.has(id)) thumbWant.add(id);   // Python stops after a moment; ask again for the rest
      for (const id of ids) {
        const u = S.lib.thumbs[id]; if (!u) continue;
        for (const t of document.querySelectorAll(`#sidebar-body [data-lib="${CSS.escape(id)}"] .im, #sidebar-body [data-lib-th="${CSS.escape(id)}"]`)) t.innerHTML = thumbImg(u);
      }
    }).catch(() => { }).finally(() => { thumbBusy = false; setTimeout(pumpThumbs, 30); });
  }
  function togglePick(id) {
    const kind = taskDef(S.task).library; if (!kind) return;
    const list = S.picks[S.task] || (S.picks[S.task] = []);
    const i = list.findIndex(p => p.id === id);
    if (i >= 0) list.splice(i, 1);
    else {
      const x = libItems(kind).find(y => y.id === id); if (!x) return;
      const p = { id: x.id, name: x.name, group: x.group, file: x.file, src: x.src };
      if (x.thumb) p.thumb = x.thumb;
      if (kind === 'hdris') list.length = 0;   // one dome light
      else p.on = 'note';
      list.push(p);
    }
    saveSettings(); refreshSidebar(); renderChips(); saveDraft();
  }

  // ---------------------------------------------------------------- drawing the sidebar
  S.ui = S.ui || {}; S.lib = { thumbs: {}, q: '', g: '', limit: 48 }; S.picks = S.picks || {};
  const helpDot = (key, what) => `<span class="help" data-help="${esc(key)}" role="button" aria-label="${esc('What ' + what + ' does')}">${icon('help')}</span>`;
  function fieldHTML(f, o) {
    const v = o[f.k]; const hint = hintOf(f, v, o);
    const long = hint && hint.length > (f.t === 'menu' ? 16 : 30);   // a long hint gets its own line, so the label column keeps its width
    const small = hint && !long ? `<small>${esc(hint)}</small>` : '';
    const below = long ? `<div class="rh full" title="${esc(f.k === 'save' ? (v === 'doc' ? ENV().render_dir || '' : o.folder || '') : hint)}">${esc(hint)}</div>` : '';
    switch (f.t) {
      case 'seg': {
        const list = optList(f, o);
        const btns = list.map(([val, lab]) => `<button data-v="${esc(val)}" class="${String(v) === String(val) ? 'on' : ''}">${esc(lab)}</button>`).join('');
        if (!hint && list.length <= 3 && list.reduce((n, x) => n + x[1].length, 0) <= 18) return `<div class="row"><span class="rl">${esc(f.l)}</span><div class="segctl sm inl" data-k="${f.k}">${btns}</div></div>`;
        return `<div class="row col"><div class="rl">${esc(f.l)}</div><div class="segctl sm" data-k="${f.k}">${btns}</div>${hint ? `<div class="rh">${esc(hint)}</div>` : ''}</div>`;
      }
      case 'menu': { const cur = optList(f, o).find(x => String(x[0]) === String(v)); return `<div class="row ${long ? 'wrap' : ''}"><span class="rl">${esc(f.l)}${small}</span><button class="pick" data-menu="${f.k}"><span>${esc(cur ? cur[1] : String(v))}</span>${icon('chevron', 's14')}</button>${below}</div>`; }
      case 'toggle': return `<div class="row ${long ? 'wrap' : ''}"><span class="rl">${esc(f.l)}${small}</span><button class="switch ${v ? 'on' : ''}" data-tog="${f.k}" aria-label="${esc(f.l)}"></button>${below}</div>`;
      case 'num': return `<div class="row"><span class="rl">${esc(f.l)}${small}</span><input class="num" type="number" data-num="${f.k}" value="${esc(v)}" min="${f.min}" max="${f.max}" step="${f.step || 1}"><span class="unit">${esc(f.unit || '')}</span></div>`;
      case 'wh': return `<div class="row"><span class="rl">${esc(f.l)}</span><input class="num" type="number" data-num="${f.k}" value="${esc(v)}" min="64" max="16000" step="1"><span class="unit">x</span><input class="num" type="number" data-num="${f.k2}" value="${esc(o[f.k2])}" min="64" max="16000" step="1"><span class="unit">px</span></div>`;
      case 'text': return `<div class="row col"><div class="rl">${esc(f.l)}</div><input class="txt" type="text" data-text="${f.k}" value="${esc(v)}" placeholder="${esc(f.ph || '')}" spellcheck="true">${hint ? `<div class="rh">${esc(hint)}</div>` : ''}</div>`;
      case 'time': case 'date': return `<div class="row"><span class="rl">${esc(f.l)}</span><input class="tm" type="${f.t}" data-text="${f.k}" value="${esc(v)}"></div>`;
      case 'engine': { const E = ENGINE[o.engine] || ENGINE.vray; return `<button class="row engrow ${engPop ? 'open' : ''}" data-engpop="1"><span class="rl">${esc(E.title())}<small>${esc(E.summary(o))}</small></span><span class="engbtn">${icon('sliders', 's16')}</span></button>`; }
      case 'chips': return `<div class="row col"><div class="rl">${esc(f.l)}</div><div class="chips" data-k="${f.k}">${optList(f, o).map(([val, lab]) => `<button class="chip ${(v || []).includes(val) ? 'on' : ''}" data-chip="${esc(val)}">${esc(lab)}</button>`).join('')}</div>${hint ? `<div class="rh">${esc(hint)}</div>` : ''}</div>`;
    }
    return '';
  }
  function refsHTML() {
    const rs = S.strokes.filter(st => st.kind === 'ref');
    let h = `<div class="sect"><span class="grow">Images on this capture</span>${rs.length ? `<span class="sub">${rs.length}</span>` : ''}${helpDot('refs', 'images on this capture')}</div>`;
    if (!rs.length) h += '<div class="lib-hint">None. Drop an image onto the capture where it applies, or paste one (Ctrl+V). Images go with this request only.</div>';
    else h += '<div class="grp refs">' + rs.map(st => `<div class="row"><span class="ref-lab">${esc(st.label)}</span><span class="rl" data-tip="${esc(refName(st))}" data-sub="${esc(String(st.source || ''))}">${esc(refName(st))}</span><button class="pick" data-role="${esc(st.label)}"><span>${esc(roleLabel(roleOf(st)))}</span>${icon('chevron', 's14')}</button><button class="btn x" data-unref="${esc(st.label)}" data-tip="Remove ${esc(st.label)}">${icon('x', 's14')}</button></div>`).join('') + '</div>';
    return h + `<div class="acts-row"><button class="linkbtn" data-addref="1">${icon('image', 's14')}From a file</button><button class="linkbtn" data-pasteref="1">${icon('paste', 's14')}Paste</button><button class="linkbtn" data-findref="1">${icon('search', 's14')}Find on the web</button></div>`;
  }
  function card(p) {   // one of the user’s own presets of the old kind: standing instructions, toggled per capture
    const on = S.presets.includes(p.id);
    return `<button class="card ${on ? 'on' : ''}" data-preset="${esc(p.id)}"><span class="th">${icon(p.icon || 'edit')}</span>
      <span class="tx"><div class="nm">${esc(p.name)}</div><div class="sm">${esc(p.summary || p.instructions || '')}</div></span><span class="ck">${icon('check', 's14')}</span></button>`;
  }
  function renderSidebar() {
    closeHelp();   // its anchor is about to be replaced
    const body = $('#sidebar-body'); if (!body) return;
    const top = body.scrollTop;
    const id = S.task, def = taskDef(id), o = opts(id), c = ctxFor(id);
    let h = '<div class="tasks">' + TASK_ORDER.map(t => { const d = taskDef(t); const n = (S.picks[t] || []).length;
      return `<button class="tk ${t === id ? 'on' : ''}" data-task="${t}">${icon(d.icon)}<span>${esc(d.label)}</span>${n ? `<i class="n">${n}</i>` : ''}</button>`; }).join('') + '</div>';
    const runs = (def.runs(o, c) || []).filter(Boolean);
    h += `<div class="job"><div class="job-t">${esc(def.label)}</div><div class="job-d">${esc(def.blurb)}</div>
      <div class="runs-h">Runs in</div><div class="runs">${runs.map(r => `<span class="run">${icon(r.ic)}<b>${esc(r.t)}</b>${r.sub ? `<i>${esc(r.sub)}</i>` : ''}</span>`).join('')}</div>
      <details class="how" data-det="how" ${S.ui.how ? 'open' : ''}><summary>${icon('chevron', 's14')}<span>How it works</span></summary><ol>${def.how(o, c).filter(Boolean).map(s => `<li>${esc(s)}</li>`).join('')}</ol></details></div>`;
    const sets = setupsFor(id), on = sets.filter(s => setupOn(s, o));
    h += `<div class="sect"><span class="grow">Setups</span>${!on.length && sets.length ? '<span class="sub">custom</span>' : ''}${helpDot('setups', 'setups')}</div><div class="setups">` +
      sets.map(s => `<button class="su ${on.includes(s) ? 'on' : ''} ${s.builtin ? '' : 'mine'}" data-setup="${esc(s.id)}" data-tip="${esc(s.name)}" data-sub="${esc(s.summary || '')}">${s.sw ? `<i class="sw" style="background:linear-gradient(180deg, ${css(s.sw[0])}, ${css(s.sw[1])})"></i>` : ''}${esc(s.name)}</button>`).join('') +
      `<button class="su add" data-savesetup="1">${icon('plus', 's14')}${sets.length ? 'Save these' : 'Save these options'}</button></div>`;
    const changedOpts = !same(o, defaults(id));
    h += `<div class="sect"><span class="grow">Options</span>${changedOpts ? '<button class="linkbtn sm" data-reset="1">Reset</button>' : ''}</div>`;
    const groups = [{ l: '', f: [] }];
    for (const f of def.fields.filter(x => !x.when || x.when(o))) { if (f.t === 'group') groups.push({ l: f.l, f: [] }); else groups[groups.length - 1].f.push(f); }
    for (const g of groups) if (g.f.length) h += (g.l ? `<div class="sect sub2"><span class="grow">${esc(g.l)}</span></div>` : '') + '<div class="grp">' + g.f.map(f => fieldHTML(f, o)).join('') + '</div>';
    if (def.library) h += libraryHTML(def.library);
    const mine = (S.allPresets[id] || []).filter(p => !p.builtin && !p.kind && !(p.files && p.files.length && def.library));
    if (mine.length) h += `<div class="sect"><span class="grow">Your presets</span><span class="sub">click to send with this capture</span></div><div class="cards">${mine.map(card).join('')}</div>`;
    h += refsHTML();
    const notes = (S.settings.task_notes || {})[id] || '';
    h += `<div class="sect"><span class="grow">Your instructions</span>${helpDot('mine', 'your instructions')}</div><textarea class="notes" data-notes="1" rows="2" spellcheck="true" placeholder="${esc('Anything Claude should always do for ' + def.label.toLowerCase() + ' jobs')}">${esc(notes)}</textarea>`;
    h += `<details class="told" data-det="told" ${S.ui.told ? 'open' : ''}><summary>${icon('chevron', 's14')}<span>What Claude gets</span></summary><div class="brief" id="brief-text">${esc(briefFor(id) || 'Nothing beyond your note and your marks.')}</div><div class="brief-foot">Plus your note, every mark with what Rhino found under it, the camera and the images.</div></details>`;
    h += `<div class="grp keep"><div class="row"><span class="rl">Keep for the next capture${helpDot('keep', 'Keep for the next capture')}<small>Otherwise each capture starts on General. Options are always remembered.</small></span><button class="switch ${S.settings.keep_task ? 'on' : ''}" data-keep="1"></button></div></div>`;
    body.innerHTML = h;
    body.scrollTop = top;
    observeThumbs();
    sideSig = sidebarSig();
    if (engPop) fillEnginePop();
  }
  const refreshSidebar = () => { renderSidebar(); renderChips(); };
  // the sidebar lists the images and the numbered marks, so it redraws when those change (not on every stroke)
  let sideSig = '';
  function sidebarSig() { return S.strokes.filter(st => st.kind === 'ref' || (NUMBERED.has(st.kind) && st.label != null)).map(st => st.kind + st.label + (st.role || '')).join('|'); }
  function syncSidebar() { if (sidebarSig() !== sideSig) renderSidebar(); }

  // the pictures: one small card model, drawn the same way each time, showing what the job does to it
  function helpArt(kind) {
    const out = [], defs = [];
    const P = (x, y, z) => [(x - y) * 0.866, (x + y) * 0.5 - z];
    const n = v => (+v).toFixed(2);
    const d = ps => 'M' + ps.map(p => n(p[0]) + ' ' + n(p[1])).join('L') + 'Z';
    const poly = (ps, cls) => out.push(`<path class="${cls}" d="${d(ps)}"/>`);
    const faces = (x, y, z, w, dd, h) => {
      const t = z + h;
      return { l: [P(x, y + dd, z), P(x + w, y + dd, z), P(x + w, y + dd, t), P(x, y + dd, t)],
        r: [P(x + w, y, z), P(x + w, y + dd, z), P(x + w, y + dd, t), P(x + w, y, t)],
        t: [P(x, y, t), P(x + w, y, t), P(x + w, y + dd, t), P(x, y + dd, t)] };
    };
    const box = (b, c) => { c = c || {}; const f = faces(...b); poly(f.l, 'l ' + (c.l || '')); poly(f.r, 'r ' + (c.r || '')); poly(f.t, 't ' + (c.t || '')); };
    const ground = cls => poly([P(-3, -3, 0), P(10, -3, 0), P(10, 8, 0), P(-3, 8, 0)], 'g ' + (cls || ''));
    const MAIN = [0, 0, 0, 4, 3, 3.2], WING = [4, 0.6, 0, 3, 2.2, 1.6];
    const hull = pts => {
      pts = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
      const lo = [], up = [];
      for (const p of pts) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
      for (const p of pts.slice().reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
      return lo.slice(0, -1).concat(up.slice(0, -1));
    };
    // the sun is up to the right, so shadows fall left and a little back
    const shadow = b => { const [x, y, , w, dd, h] = b; const s = h * 0.8; const base = [[x, y], [x + w, y], [x + w, y + dd], [x, y + dd]];
      return hull(base.concat(base.map(([a, c]) => [a - s, c + s * 0.45])).map(([a, c]) => P(a, c, 0))); };
    const badge = (p, txt, fill) => out.push(`<circle cx="${n(p[0])}" cy="${n(p[1])}" r=".62" fill="${fill}" class="bd"/><text x="${n(p[0])}" y="${n(p[1] + .27)}" class="bt">${txt}</text>`);
    const check = (p, fill) => out.push(`<circle cx="${n(p[0])}" cy="${n(p[1])}" r=".62" fill="${fill}" class="bd"/><path class="ckm" d="M${n(p[0] - .27)} ${n(p[1] + .02)}L${n(p[0] - .06)} ${n(p[1] + .24)}L${n(p[0] + .29)} ${n(p[1] - .2)}"/>`);
    const tag = (x, y, txt, col) => { const w = txt.length * .52 + 1.0; out.push(`<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="1.6" rx=".45" class="tg" stroke="${col}"/><text x="${n(x + .5)}" y="${n(y + 1.12)}" class="tt" fill="${col}">${esc(txt)}</text>`); };
    const bounds = ps => { const xs = ps.map(p => p[0]), ys = ps.map(p => p[1]); return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; };
    const materials = () => {
      defs.push('<pattern id="hp-timber" patternUnits="userSpaceOnUse" width=".44" height="2"><rect width=".44" height="2" fill="#C08A55"/><rect width=".06" height="2" fill="#93612E"/></pattern>');
      defs.push('<pattern id="hp-concrete" patternUnits="userSpaceOnUse" width="1" height="1"><rect width="1" height="1" fill="#ADADA9"/><circle cx=".22" cy=".3" r=".05" fill="#8C8C88"/><circle cx=".7" cy=".72" r=".045" fill="#8C8C88"/><circle cx=".74" cy=".2" r=".035" fill="#CACAC6"/><circle cx=".3" cy=".82" r=".03" fill="#CACAC6"/></pattern>');
      defs.push('<linearGradient id="hp-glass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#CFEAF8"/><stop offset="1" stop-color="#5C97BF"/></linearGradient>');
    };
    const dressed = () => {
      box(MAIN, { l: 'm-timber', r: 'm-concrete', t: 'm-roof' }); box(WING, { l: 'm-glass', r: 'm-glass', t: 'm-roof' });
      out.push(`<path class="m-gleam" d="${d([P(4.6, 2.8, .3), P(5.6, 2.8, 1.45), P(6.15, 2.8, 1.45), P(5.15, 2.8, .3)])}"/>`);
    };
    const RED = '#FF453A', ORANGE = 'var(--claude)', SUN = '#FFB340';
    switch (kind) {
      case 'general': {   // a numbered box round what the user means, and the user’s question
        ground(); box(MAIN); box(WING);
        const [x0, y0, x1, y1] = bounds(faces(...MAIN).l); const m = .4;
        out.push(`<rect x="${n(x0 - m)}" y="${n(y0 - m)}" width="${n(x1 - x0 + 2 * m)}" height="${n(y1 - y0 + 2 * m)}" rx=".3" class="mk" stroke="${RED}"/>`);
        badge([x0 - m, y0 - m], '1', RED);
        tag(-8.4, -4.55, 'too heavy here?', RED);
        break;
      }
      case 'model': {   // the storey the user asked for, added on the wing
        ground(); box(MAIN); box(WING);
        const f = faces(4, 0.6, 1.6, 3, 2.2, 1.5); poly(f.l, 'add'); poly(f.r, 'add'); poly(f.t, 'add');
        badge(P(7, 0.6, 3.1), '+', 'var(--accent)');
        tag(-8.4, -4.55, 'add a storey here', 'var(--accent)');
        break;
      }
      case 'materials': { materials(); ground(); dressed(); break; }
      case 'lighting': {
        ground('lt'); poly(shadow(MAIN), 'sh'); poly(shadow(WING), 'sh');
        box(MAIN, { l: 'shade', r: 'sunlit', t: 'sunlit' }); box(WING, { l: 'shade', r: 'sunlit', t: 'sunlit' });
        const c = [9.4, -3.3];
        out.push(`<circle cx="${c[0]}" cy="${c[1]}" r=".95" fill="${SUN}"/>`);
        for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4; out.push(`<line x1="${n(c[0] + Math.cos(a) * 1.35)}" y1="${n(c[1] + Math.sin(a) * 1.35)}" x2="${n(c[0] + Math.cos(a) * 1.85)}" y2="${n(c[1] + Math.sin(a) * 1.85)}" class="ray" stroke="${SUN}"/>`); }
        break;
      }
      case 'render': {   // the same view as a clay model and as a render, split down the middle
        materials();
        defs.push('<linearGradient id="hp-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6FA6DD"/><stop offset=".55" stop-color="#C9DDF0"/><stop offset="1" stop-color="#F3D9B8"/></linearGradient>');
        defs.push('<clipPath id="hp-half"><rect x="-8.9" y="-5" width="10.5" height="11"/></clipPath>');
        out.push('<rect x="-8.9" y="-5" width="20.6" height="11" fill="url(#hp-sky)"/>');
        ground('grass'); poly(shadow(MAIN), 'sh'); poly(shadow(WING), 'sh'); dressed();
        out.push('<g clip-path="url(#hp-half)"><rect x="-8.9" y="-5" width="20.6" height="11" class="clay-bg"/>');
        ground(); box(MAIN); box(WING);
        out.push('</g><line x1="1.6" y1="-5" x2="1.6" y2="6" class="split"/><circle cx="1.6" cy="0.6" r=".62" class="knob"/>');
        break;
      }
      case 'analyse': {   // a section: the model cut along the user’s line, the cut face hatched
        defs.push('<pattern id="hp-hatch" patternUnits="userSpaceOnUse" width=".32" height=".32" patternTransform="rotate(45)"><rect width=".32" height=".32" class="cutf"/><rect width=".07" height=".32" class="cuth"/></pattern>');
        ground();
        box([0, 0, 0, 4, 1.6, 3.2], { l: 'cut' }); box([4, 0.6, 0, 3, 1.0, 1.6], { l: 'cut' });
        for (const b of [[0, 1.6, 0, 4, 1.4, 3.2], [4, 1.6, 0, 3, 1.2, 1.6]]) { const f = faces(...b); poly(f.l, 'ghost'); poly(f.r, 'ghost'); poly(f.t, 'ghost'); }
        const a = P(-2.4, 1.6, 0), b = P(9.4, 1.6, 0), dir = [0.866, -0.5], nr = [0.5, 0.866];
        out.push(`<line x1="${n(a[0])}" y1="${n(a[1])}" x2="${n(b[0])}" y2="${n(b[1])}" class="sec" stroke="${RED}"/>`);
        for (const e of [a, b]) out.push(`<path d="${d([[e[0] + dir[0] * .95, e[1] + dir[1] * .95], [e[0] + nr[0] * .4, e[1] + nr[1] * .4], [e[0] - nr[0] * .4, e[1] - nr[1] * .4]])}" fill="${RED}" style="stroke:none"/>`);
        break;
      }
      case 'critique': {   // the critic's findings come back on the view, numbered
        ground(); box(MAIN); box(WING);
        const pin = (p, label) => { const hx = p[0], hy = p[1] - 1.55;
          out.push(`<path class="bd" fill="${ORANGE}" d="M${n(p[0])} ${n(p[1])}L${n(hx - .45)} ${n(hy + .52)}A.69 .69 0 1 1 ${n(hx + .45)} ${n(hy + .52)}Z"/><text x="${n(hx)}" y="${n(hy + .22)}" class="bt sm">${label}</text>`); };
        pin(P(2, 0, 3.2), 'C1'); pin(P(7, 1.7, 1.6), 'C2');
        tag(-8.4, -4.55, 'C1: too tall?', ORANGE);
        break;
      }
      case 'presets': {   // three presets, one picked
        [['#FF9F43', '#FFD9A6'], ['#8E9AAF', '#CDD5E1'], ['#F2F2F4', '#D6D9DF']].forEach((g, i) => {
          const y = -4.3 + i * 3.25, on = i === 1;
          defs.push(`<linearGradient id="hp-p${i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${g[0]}"/><stop offset="1" stop-color="${g[1]}"/></linearGradient>`);
          out.push(`<rect x="-4.6" y="${n(y)}" width="12" height="2.75" rx=".7" class="pc${on ? ' on' : ''}"/><rect x="-4.2" y="${n(y + .35)}" width="2.05" height="2.05" rx=".45" fill="url(#hp-p${i})"/>`);
          out.push(`<rect x="-1.6" y="${n(y + .66)}" width="${on ? 4.8 : 5.6}" height=".42" rx=".21" class="bar1"/><rect x="-1.6" y="${n(y + 1.46)}" width="${on ? 6.6 : 4.4}" height=".32" rx=".16" class="bar2"/>`);
          if (on) check([6.55, y + .75], 'var(--accent)');
        });
        break;
      }
      case 'add': {   // a photo dropped into the "Add your own" card
        out.push('<rect x="-3.4" y="0.9" width="11.6" height="3.7" rx=".85" class="addc"/>');
        out.push('<line x1="-2.35" y1="2.75" x2="-1.15" y2="2.75" class="pls"/><line x1="-1.75" y1="2.15" x2="-1.75" y2="3.35" class="pls"/>');
        out.push('<rect x="-0.3" y="1.9" width="5.2" height=".42" rx=".21" class="bar1"/><rect x="-0.3" y="2.75" width="7" height=".32" rx=".16" class="bar2"/>');
        out.push('<g transform="rotate(-9 -4 -2.4)"><rect x="-6.6" y="-4.4" width="5.2" height="3.9" rx=".35" class="ph"/><path d="M-6.2 -1.0L-4.6 -2.9L-3.5 -1.8L-2.8 -2.5L-1.8 -1.0Z" fill="#8FB08A" style="stroke:none"/><circle cx="-2.6" cy="-3.5" r=".42" fill="#FFB340"/></g>');
        out.push('<path class="arw" d="M-1.0 -2.3C1.4 -2.4 2.6 -1.4 2.9 0.4"/><path d="M2.9 0.75L2.45 -0.05L3.4 0.0Z" fill="var(--accent)" style="stroke:none"/>');
        break;
      }
      case 'refs': {   // a photo card, R1, dropped on the capture by the part it is about
        materials(); ground(); box(MAIN); box(WING, { l: 'm-timber' });
        out.push('<g transform="rotate(-7 -5.6 -2.5)"><rect x="-8.3" y="-4.5" width="5.4" height="4" rx=".35" class="ph"/><rect x="-7.95" y="-4.15" width="4.7" height="3.3" fill="url(#hp-timber)" style="stroke:none"/></g>');
        tag(-8.2, 0.05, 'R1', 'var(--accent)');
        out.push('<path class="arw" d="M-2.6 -2.7C0.2 -3.5 2.2 -1.4 2.2 1.2"/><path d="M2.2 1.65L1.75 0.8L2.7 0.85Z" fill="var(--accent)" style="stroke:none"/>');
        break;
      }
      case 'keep': {   // two captures in a row; the job carries into the second
        for (const x of [-7.4, 3.6]) {
          out.push(`<rect x="${x}" y="-2.6" width="7.2" height="5.4" rx=".55" class="fr"/><rect x="${x + .45}" y="-2.15" width="6.3" height="3.2" rx=".3" class="clay-bg" style="stroke:none"/>`);
          out.push(`<rect x="${x + .9}" y="1.45" width="5.4" height="1.1" rx=".55" class="chip"/><text x="${x + 3.6}" y="2.27" class="chipt">Materials</text>`);
        }
        out.push('<path class="arw" d="M0.4 0.1L2.7 0.1"/><path d="M3.15 0.1L2.5 -0.35L2.5 0.55Z" fill="var(--accent)" style="stroke:none"/>');
        out.push('<rect x="0.35" y="-4.55" width="2.3" height="1.3" rx=".65" class="sw"/><circle cx="2.0" cy="-3.9" r=".52" class="swk"/>');
        break;
      }
      default: ground(); box(MAIN); box(WING);
    }
    return `<svg class="art" viewBox="-8.9 -5 20.6 11" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${defs.length ? '<defs>' + defs.join('') + '</defs>' : ''}${out.join('')}</svg>`;
  }
  // ---------------------------------------------------------------- help: what each part of the sidebar does
  const HELP = {
    setups: { t: 'Setups', art: 'presets', d: 'Saved sets of options for this kind of job. Click one to set its options; it stays lit while they match. Change anything, then Save these to keep your own. Right-click one of yours to update, rename or delete it.' },
    library: { t: 'Materials to use', d: 'Pick from your library: the materials you downloaded in Chaos Cosmos, and your own .vrmat files and photos. Each one you pick can go on a numbered mark, on the selection, or wherever your note says.' },
    refs: { t: 'Images on this capture', art: 'refs', d: 'Images you drop or paste onto the capture: R1, R2 and so on. They go with this request only. Say what each one is for, so Claude knows whether to match its material, its look or its light, use it as the site photo for a montage, or just read it for context.' },
    mine: { t: 'Your instructions', d: 'Standing instructions for this kind of job, sent every time: things like "also save a JPG" or "never touch the SITE layer". They stay until you change them.' },
    keep: { t: 'Keep for the next capture', art: 'keep', d: 'On: the next capture opens on this same task, with the same materials picked. Off: every capture starts on General, so a materials job never follows you into an unrelated question. Your options and setups are remembered either way.' },
  };
  let helpKey = null, helpPinned = false, helpTimer = 0;
  function helpFor(key) { const h = HELP[key]; return h ? { t: h.t, d: h.d, art: h.art ? helpArt(h.art) : '' } : null; }
  function openHelp(anchor, pin) {
    const key = anchor.dataset.help; const h = helpFor(key); if (!h) return;
    clearTimeout(helpTimer); closeHelp(); hideTip();
    const pop = el(`<div class="glass help-pop" role="dialog" aria-label="${esc(h.t)}">${h.art ? `<div class="art-box">${h.art}</div>` : ''}<div class="hp-body">
      <div class="hp-t">${esc(h.t)}</div>${h.d ? `<p>${esc(h.d)}</p>` : ''}</div></div>`);
    document.body.appendChild(pop);
    const sb = $('#sidebar').getBoundingClientRect(), ar = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, hh = pop.offsetHeight;
    let left = sb.right + 10; if (left + w > innerWidth - 8) left = Math.max(8, innerWidth - w - 8);
    const top = Math.max(8, Math.min(ar.top + ar.height / 2 - hh / 2, innerHeight - hh - 8));
    pop.style.left = left + 'px'; pop.style.top = top + 'px';
    pop.addEventListener('pointerenter', () => clearTimeout(helpTimer));
    pop.addEventListener('pointerleave', () => { if (!helpPinned) helpTimer = setTimeout(closeHelp, 220); });
    helpKey = key; helpPinned = !!pin; anchor.classList.add('open');
  }
  function closeHelp() {
    clearTimeout(helpTimer);
    const p = $('.help-pop'); if (p) p.remove();
    for (const x of document.querySelectorAll('.help.open')) x.classList.remove('open');
    helpKey = null; helpPinned = false;
  }

  // ---------------------------------------------------------------- sidebar: what each control does
  function fieldOf(k) { return taskDef(S.task).fields.concat(S.task === 'render' ? ENGINE_FIELDS() : []).find(f => f.k === k || f.k2 === k); }
  function updateLibGrid() { const g = $('#lib-grid'); if (g) { g.innerHTML = libGridHTML(taskDef(S.task).library); observeThumbs(); } }
  function fieldMenu(btn) {
    const k = btn.dataset.menu; const f = fieldOf(k); if (!f) return; const o = opts(); const list = optList(f, o);
    menu(btn, list.map(([v, l]) => ({ label: l, check: String(o[k]) === String(v), run: () => { if (k === 'save' && v === 'choose') chooseFolder(); else setOpt(k, v); } })), { wide: list.some(x => x[1].length > 24) });
  }
  // the engine's own settings, in a pop-up beside the sidebar: it opens when an engine is picked, and from its row
  let engPop = null;
  function engineFields(o) { const E = ENGINE[o.engine] || ENGINE.vray; return E.fields.filter(f => !f.when || f.when(o)); }
  function fillEnginePop() {
    if (!engPop) return;
    const anchor = $('#sidebar-body [data-engpop]'), o = opts('render');
    if (!anchor || S.task !== 'render' || o.make !== 'render') { closeEnginePop(); return; }
    const E = ENGINE[o.engine] || ENGINE.vray;
    engPop.querySelector('.ep-t span').textContent = E.title();
    engPop.querySelector('.ep-b').innerHTML = '<div class="grp">' + engineFields(o).map(f => fieldHTML(f, o)).join('') + '</div>' + (E.note ? `<div class="ep-note">${esc(E.note)}</div>` : '');
    placeEnginePop(anchor);
  }
  function placeEnginePop(anchor) {
    if (!engPop) return;
    const sb = $('#sidebar').getBoundingClientRect(), ar = anchor.getBoundingClientRect();
    const w = engPop.offsetWidth, hh = engPop.offsetHeight;
    let left = sb.right + 10; if (left + w > innerWidth - 8) left = Math.max(8, innerWidth - w - 8);
    const top = Math.max(8, Math.min(ar.top - 14, innerHeight - hh - 8));
    engPop.style.left = left + 'px'; engPop.style.top = top + 'px';
  }
  function openEnginePop() {
    closeEnginePop(); closeHelp(); hideTip();
    const pop = el(`<div class="glass eng-pop" role="dialog"><div class="ep-h"><span class="ep-t">${icon('sliders', 's16')}<span></span></span><button class="btn x" data-epclose="1" data-tip="Close" aria-label="Close">${icon('x', 's14')}</button></div><div class="ep-b"></div></div>`);
    document.body.appendChild(pop); engPop = pop;
    pop.addEventListener('click', e => { if (e.target.closest('[data-epclose]')) { closeEnginePop(); return; } fieldControl(s => e.target.closest(s)); });
    pop.addEventListener('change', e => fieldChange(e));
    pop.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('.txt, .num, .tm')) { e.preventDefault(); e.target.blur(); } });
    fillEnginePop();
    const b = $('#sidebar-body [data-engpop]'); if (b) b.classList.add('open');
  }
  function closeEnginePop() {
    if (engPop) { engPop.remove(); engPop = null; }
    for (const x of document.querySelectorAll('[data-engpop].open')) x.classList.remove('open');
  }
  document.addEventListener('pointerdown', e => {
    const t = e.target && e.target.closest ? e.target : null;
    if (engPop && !(t && (t.closest('.eng-pop') || t.closest('[data-engpop]') || t.closest('#layer')))) closeEnginePop();
  }, true);
  // the controls that work the same in the sidebar and in the pop-up
  function fieldControl(q) {
    let x;
    if ((x = q('.segctl[data-k] [data-v]'))) { setOpt(x.closest('.segctl').dataset.k, x.dataset.v); return true; }
    if ((x = q('[data-menu]'))) { fieldMenu(x); return true; }
    if ((x = q('[data-tog]'))) { setOpt(x.dataset.tog, !opts()[x.dataset.tog]); return true; }
    if ((x = q('.chips[data-k] [data-chip]'))) {
      const k = x.closest('.chips').dataset.k, f = fieldOf(k), v = x.dataset.chip, cur = (opts()[k] || []).slice(), i = cur.indexOf(v);
      if (i >= 0) { if (!(f && f.min1) || cur.length > 1) cur.splice(i, 1); } else cur.push(v);
      setOpt(k, cur); return true;
    }
    return false;
  }
  function fieldChange(e) {
    const n = e.target.closest('[data-num]');
    if (n) {
      const k = n.dataset.num, f = fieldOf(k); let v = parseFloat(n.value);
      if (!isFinite(v)) v = opts()[k];
      if (f && f.t === 'wh') v = clamp(Math.round(v), 64, 16000); else if (f && f.min != null) v = clamp(v, +f.min, +f.max);
      setOpt(k, v); return true;
    }
    const t = e.target.closest('[data-text]'); if (t) { setOpt(t.dataset.text, t.value); return true; }
    return false;
  }
  function chooseFolder() {
    const o = opts('render');
    Bridge.call('choose_folder', { title: 'Save renders in', start: o.folder || ENV().render_dir || ENV().doc_dir || '' }).then(r => {
      if (!r || r.cancelled || !r.path) return;
      const n = opts('render'); n.save = 'choose'; n.folder = r.path; setOpts('render', n); refreshSidebar();
    }).catch(e => toast('Couldn\'t choose a folder (' + e.message + ')', 'warn'));
  }
  const markExists = n => numberedMarks().some(st => String(st.label) === String(n));
  function assignMenu(btn, i) {
    const p = (S.picks[S.task] || [])[i]; if (!p) return;
    const setOn = v => { p.on = v; saveSettings(); refreshSidebar(); saveDraft(); };
    const items = [{ head: `Put ${p.name} on` }, { label: 'As my note says', check: !p.on || p.on === 'note', run: () => setOn('note') }, { label: 'The selection', check: p.on === 'sel', run: () => setOn('sel') }];
    const ms = numberedMarks();
    items.push('-');
    if (ms.length) for (const st of ms) items.push({ label: `Mark ${st.label}`, sub: `${KIND_NAME[st.kind]}${st.comment ? ': ' + st.comment : ''}`, check: p.on === '#' + st.label, run: () => setOn('#' + st.label) });
    else items.push({ label: 'Draw a box, ellipse, cloud or pin to pick a mark', disabled: true });
    items.push('-', { label: 'Everything else', check: p.on === 'rest', run: () => setOn('rest') });
    menu(btn, items, { wide: true });
  }
  function roleMenu(btn, label) {
    const st = refByLabel(label); if (!st) return; const cur = roleOf(st);
    menu(btn, [{ head: `${label} is` }].concat((ROLES[S.task] || ROLES.general).map(([v, l]) => ({ label: l, check: v === cur, run: () => { st.role = v; changed(); refreshSidebar(); } }))));
  }
  function addRefFromFile() {
    const p = centreImg();
    Bridge.call('browse_image', { at: p, n: S.refCount + 1 }).then(gotRef).catch(e => toast('Couldn\'t add it (' + e.message + ')', 'warn'));
  }
  function addToLibrary(kind) {
    Bridge.call('preset_browse', { task: kind === 'hdris' ? 'lighting' : 'materials', library: true }).then(r => libAdded(r, kind))
      .catch(err => toast('Couldn\'t add that (' + err.message + ')', 'warn'));
  }
  function libAdded(r, kind) {
    if (!r || r.cancelled) return;
    S.allPresets = r.presets;
    const id = 'preset:' + r.id, list = S.picks[S.task] || (S.picks[S.task] = []);
    if (taskDef(S.task).library === kind && !list.some(p => p.id === id)) {
      const x = libItems(kind).find(y => y.id === id);
      if (x) { if (kind === 'hdris') list.length = 0; const p = { id, name: x.name, group: x.group, file: x.file, src: 'mine' }; if (kind !== 'hdris') p.on = 'note'; list.push(p); }
    }
    S.ui['lib_' + kind] = true; saveSettings(); refreshSidebar(); saveDraft();
    toast(`Added "${r.name}" to your library, and picked it`, 'check');
  }
  function resetOptions() {
    if (S.settings.task_opts) delete S.settings.task_opts[S.task];
    saveSettings(); refreshSidebar(); toast(`${taskDef(S.task).label} options are back to the defaults`, 'check', 1800);
  }
  function saveSetupSheet(existing) {
    const def = taskDef(S.task);
    const sc = sheet({ title: existing ? `Rename "${existing.name}"` : `Save as a ${def.label} setup`,
      text: existing ? '' : 'Keeps every option as it is now under a name, so one click brings them all back.',
      html: `<input class="txt big" id="setup-name" type="text" maxlength="40" spellcheck="false" placeholder="A name, like Board render" value="${esc(existing ? existing.name : '')}">`,
      buttons: [{ label: 'Cancel' }, { label: existing ? 'Rename' : 'Save', primary: true, run: () => go() }] });
    const inp = sc.querySelector('#setup-name');
    function go() {
      const name = inp.value.trim(); if (!name) { toast('It needs a name', 'warn'); return; }
      const call = existing ? Bridge.call('preset_update', { id: existing.id, name })
        : Bridge.call('preset_save', { task: S.task, name, kind: 'setup', options: opts(S.task), summary: tagFor(S.task).replace(/^[^:]+: ?/, '') });
      call.then(r => { S.allPresets = r.presets; refreshSidebar(); toast(existing ? `Renamed it "${name}"` : `Saved "${name}"`, 'check'); })
        .catch(err => toast('Couldn\'t save it (' + err.message + ')', 'warn'));
    }
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); closeMenus(); go(); } else if (e.key === 'Escape') { e.preventDefault(); closeMenus(); } });
    setTimeout(() => { inp.focus(); inp.select(); }, 60);
  }
  function setupMenu(e, s) {
    const items = [{ icon: 'check', label: 'Use these options', run: () => applySetup(s) }];
    if (!s.builtin) {
      items.push({ icon: 'redo', label: 'Update it with the options now', run: () => Bridge.call('preset_update', { id: s.id, options: opts(S.task), summary: tagFor(S.task).replace(/^[^:]+: ?/, '') }).then(r => { S.allPresets = r.presets; refreshSidebar(); toast(`Updated "${s.name}"`, 'check'); }) });
      items.push({ icon: 'edit', label: 'Rename...', run: () => saveSetupSheet(s) });
      items.push('-', { icon: 'trash', label: 'Delete this setup', danger: true, run: () => Bridge.call('preset_remove', { id: s.id }).then(r => { S.allPresets = r.presets; refreshSidebar(); toast(`Deleted "${s.name}"`); }) });
    } else items.push('-', { icon: 'eye-off', label: 'Hide this setup', run: () => { S.settings.hidden_setups = (S.settings.hidden_setups || []).concat([s.id]); saveSettings(); refreshSidebar(); } });
    const hidden = (taskDef(S.task).setups || []).filter(x => (S.settings.hidden_setups || []).includes(x.id));
    if (hidden.length) items.push({ icon: 'eye', label: `Show the ${hidden.length} hidden`, run: () => { S.settings.hidden_setups = (S.settings.hidden_setups || []).filter(x => !hidden.some(y => y.id === x)); saveSettings(); refreshSidebar(); } });
    menu({ x: e.clientX, y: e.clientY }, items);
  }
  function bindSidebar() {
    const body = $('#sidebar-body');
    // the ? : hover a moment to read it, click to keep it open; it never triggers the row it sits in
    body.addEventListener('pointerover', e => {
      const hb = e.target.closest('[data-help]'); if (!hb || helpPinned || hb.contains(e.relatedTarget)) return;
      clearTimeout(helpTimer); helpTimer = setTimeout(() => openHelp(hb, false), helpKey ? 60 : 320);
    });
    body.addEventListener('pointerout', e => {
      const hb = e.target.closest('[data-help]'); if (!hb || helpPinned || hb.contains(e.relatedTarget)) return;
      clearTimeout(helpTimer); helpTimer = setTimeout(() => { if (!helpPinned) closeHelp(); }, 220);
    });
    document.addEventListener('pointerdown', e => {
      const t = e.target && e.target.closest ? e.target : null;
      if (helpKey && !(t && (t.closest('.help-pop') || t.closest('[data-help]')))) closeHelp();
    }, true);
    body.addEventListener('click', e => {
      const q = s => e.target.closest(s);
      const hb = q('[data-help]');
      if (hb) { e.preventDefault(); e.stopPropagation(); if (helpPinned && helpKey === hb.dataset.help) closeHelp(); else openHelp(hb, true); return; }
      let x;
      if ((x = q('[data-task]'))) return setTask(x.dataset.task);
      if (q('[data-engpop]')) { if (engPop) closeEnginePop(); else openEnginePop(); return; }
      if (fieldControl(q)) return;
      if ((x = q('[data-setup]'))) { const s = setupsFor(S.task).find(y => y.id === x.dataset.setup); if (s) applySetup(s); return; }
      if (q('[data-savesetup]')) return saveSetupSheet();
      if (q('[data-reset]')) return resetOptions();
      if ((x = q('[data-lib]'))) return togglePick(x.dataset.lib);
      if ((x = q('[data-g]'))) { S.lib.g = x.dataset.g; S.lib.limit = 48; return renderSidebar(); }
      if (q('[data-libmore]')) { S.lib.limit = 100000; return updateLibGrid(); }
      if ((x = q('[data-on]'))) return assignMenu(x, +x.dataset.on);
      if ((x = q('[data-unpick]'))) { (S.picks[S.task] || []).splice(+x.dataset.unpick, 1); saveSettings(); refreshSidebar(); saveDraft(); return; }
      if ((x = q('[data-libadd]'))) return addToLibrary(x.dataset.libadd);
      if ((x = q('[data-role]'))) return roleMenu(x, x.dataset.role);
      if ((x = q('[data-unref]'))) { const st = refByLabel(x.dataset.unref); if (st) removeMark(st); return; }
      if (q('[data-addref]')) return addRefFromFile();
      if (q('[data-pasteref]')) return pasteImage();
      if (q('[data-findref]')) return examples();
      if ((x = q('[data-preset]'))) return togglePreset(x.dataset.preset);
      if (q('[data-keep]')) { S.settings.keep_task = !S.settings.keep_task; saveSettings(); renderSidebar(); }
    });
    body.addEventListener('change', e => { fieldChange(e); });
    body.addEventListener('input', e => {
      if (e.target.matches('[data-notes]')) {
        if (!S.settings.task_notes) S.settings.task_notes = {};
        S.settings.task_notes[S.task] = e.target.value; saveSettings();
        const b = $('#brief-text'); if (b) b.textContent = briefFor() || 'Nothing beyond your note and your marks.';
        return;
      }
      if (e.target.matches('.lib-q')) { S.lib.q = e.target.value; S.lib.limit = 48; updateLibGrid(); }
    });
    body.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('.txt, .num, .tm')) { e.preventDefault(); e.target.blur(); } });
    body.addEventListener('error', e => {
      const im = e.target; if (!im || im.tagName !== 'IMG') return;
      const host = im.closest('[data-lib], [data-lib-th]'); if (!host) return;
      const id = host.dataset.lib || host.dataset.libTh; if (S.lib.thumbs[id] && im.src.indexOf(S.lib.thumbs[id]) >= 0) return;
      S.lib.previewFailed = (S.lib.previewFailed || 0) + 1; im.remove(); thumbWant.add(id); pumpThumbs();
    }, true);
    // details: remember what is open; the library loads the first time it opens
    body.addEventListener('toggle', e => {
      const d = e.target; if (!d.dataset || !d.dataset.det) return;
      const was = !!S.ui[d.dataset.det]; S.ui[d.dataset.det] = d.open;
      if (d.dataset.det.startsWith('lib_') && d.open && !was) renderSidebar();
    }, true);
    body.addEventListener('dblclick', e => { if (e.target.closest('[data-help]')) return; const c = e.target.closest('[data-preset]'); if (c) Bridge.fire('preset_edit', { id: c.dataset.preset }); });
    body.addEventListener('contextmenu', e => {
      if (e.target.closest('[data-help]')) { e.preventDefault(); return; }
      const su = e.target.closest('[data-setup]');
      if (su) { e.preventDefault(); const s = setupsFor(S.task).find(y => y.id === su.dataset.setup); if (s) setupMenu(e, s); return; }
      const lt = e.target.closest('[data-lib^="preset:"]');
      if (lt) {
        e.preventDefault(); const pid = lt.dataset.lib.slice(7); const picked = (S.picks[S.task] || []).some(p => p.id === lt.dataset.lib);
        menu({ x: e.clientX, y: e.clientY }, [{ icon: 'check', label: picked ? 'Don\'t use it' : 'Use it', run: () => togglePick(lt.dataset.lib) }, '-',
          { icon: 'trash', label: 'Remove from the library', danger: true, run: () => Bridge.call('preset_remove', { id: pid }).then(r => { S.allPresets = r.presets; for (const k in S.picks) S.picks[k] = (S.picks[k] || []).filter(p => p.id !== lt.dataset.lib); saveSettings(); refreshSidebar(); }) }]);
        return;
      }
      const c = e.target.closest('[data-preset]'); if (!c) return; e.preventDefault();
      const id = c.dataset.preset; const p = findPreset(id);
      menu({ x: e.clientX, y: e.clientY }, [
        { icon: 'check', label: S.presets.includes(id) ? 'Stop using' : 'Use for this capture', run: () => togglePreset(id) },
        { icon: 'edit', label: 'Edit the preset file', run: () => Bridge.fire('preset_edit', { id }) },
        '-',
        { icon: 'trash', label: 'Delete this preset', danger: true, run: () => Bridge.call('preset_remove', { id }).then(r => { S.allPresets = r.presets; S.presets = S.presets.filter(x => x !== id); refreshSidebar(); toast(`Removed "${p ? p.name : id}"`); }) },
      ]);
    });
  }
  function findPreset(id) { for (const k in S.allPresets) for (const p of S.allPresets[k]) if (p.id === id) return p; return null; }
  function setTask(id) {
    S.task = TASKS[id] ? id : 'general';
    renderSidebar(); renderChips(); placeholder(); saveSettings();
  }
  function togglePreset(id) {
    const i = S.presets.indexOf(id); if (i >= 0) S.presets.splice(i, 1); else S.presets.push(id);
    refreshSidebar(); saveSettings();
  }

  // ---------------------------------------------------------------- marks panel
  function renderMarks() {
    const body = $('#marks-body'); if (!body) return;
    const focused = document.activeElement && document.activeElement.classList && document.activeElement.classList.contains('cm') ? document.activeElement.dataset.id : null;
    let html = '';
    const cl = S.claude.filter(st => st.kind === 'text' && /^C\d+/.test(st.text || ''));
    if (cl.length) {
      html += `<div class="sect"><span class="grow">From Claude</span><button class="btn" style="height:26px;min-width:26px;padding:0 8px;font-size:11.5px" data-hideclaude="1">${S.showClaude ? 'Hide' : 'Show'}</button></div>`;
      for (const st of cl) { const m = /^(C\d+)\s+(.*)$/.exec(st.text) || [0, '', st.text]; html += `<div class="mark-row claude-row"><span class="chip">${esc(m[1].replace('C', ''))}</span><div class="tx"><div class="t1">${esc(m[1])}</div><div class="t2">${esc(m[2])}</div></div></div>`; }
      html += `<div class="sect"><span class="grow">Your marks</span></div>`;
    }
    const mine = S.strokes.filter(st => st.kind !== 'match');
    if (!mine.length) html += `<div class="empty">${icon('box')}No marks yet. Draw on the capture: boxes and pins are numbered, so you can refer to them in your request.</div>`;
    const sorted = mine.slice().sort((a, b) => (typeof a.label === 'number' ? a.label : 1e6 + S.strokes.indexOf(a)) - (typeof b.label === 'number' ? b.label : 1e6 + S.strokes.indexOf(b)));
    for (const st of sorted) {
      if (st._id == null) st._id = ++S.ids;
      const rgb = rgbOf(st);
      const chip = typeof st.label === 'number' ? `<span class="chip ${lum(rgb) > 0.62 ? 'dark' : ''}" style="--c:${css(rgb)}">${st.label}</span>`
        : st.kind === 'ref' ? `<span class="chip k" style="--c:var(--text2)">${icon('image', 's14')}</span>` : `<span class="chip k" style="--c:${css(rgb)}">${icon(KIND_ICON[st.kind] || 'pen', 's14')}</span>`;
      const hit = st._hit ? `${st._hit.name || st._hit.type}${st._hit.layer ? ' on ' + st._hit.layer : ''}` : '';
      const what = st.kind === 'text' ? `"${(st.text || '').split('\n')[0]}"` : st.kind === 'measure' ? (st.measure ? st.measure.text : '...') : st.kind === 'ref' ? st.label : hit;
      html += `<div class="mark-row ${S.sel === st ? 'sel' : ''}" data-id="${st._id}">${chip}<div class="tx"><div class="t1">${esc(KIND_NAME[st.kind] || st.kind)}<span class="what" title="${esc(what)}">${esc(what)}</span></div>
        ${st.kind === 'text' ? '' : `<textarea class="cm" rows="1" data-id="${st._id}" placeholder="Add a comment">${esc(st.comment || '')}</textarea>`}</div>
        <button class="btn del" data-del="${st._id}" data-tip="Delete">${icon('trash', 's16')}</button></div>`;
    }
    body.innerHTML = html;
    if (focused) { const f = body.querySelector(`.cm[data-id="${focused}"]`); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }
    $('#marks-count').textContent = mine.length ? String(mine.length) : '';
  }
  function bindMarks() {
    const body = $('#marks-body');
    const byId = id => S.strokes.find(st => String(st._id) === String(id));
    body.addEventListener('click', e => {
      if (e.target.closest('[data-hideclaude]')) { toggleClaude(); return; }
      const d = e.target.closest('[data-del]'); if (d) { const st = byId(d.dataset.del); if (st) removeMark(st); return; }
      if (e.target.closest('.cm')) return;
      const r = e.target.closest('.mark-row[data-id]'); if (r) { const st = byId(r.dataset.id); if (st) { setTool('select'); select(st); revealMark(st); } }
    });
    body.addEventListener('focusin', e => { const c = e.target.closest('.cm'); if (c) { c._snap = false; } });
    body.addEventListener('input', e => {
      const c = e.target.closest('.cm'); if (!c) return; const st = byId(c.dataset.id); if (!st) return;
      if (!c._snap) { snapshot(); c._snap = true; }
      const v = c.value.trim(); if (v) st.comment = v; else delete st.comment; dirty(); saveDraft();
    });
    body.addEventListener('keydown', e => { const c = e.target.closest('.cm'); if (c && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); c.blur(); } if (c && e.key === 'Escape') c.blur(); e.stopPropagation(); });
    body.addEventListener('pointerover', e => { const r = e.target.closest('.mark-row[data-id]'); const st = r ? byId(r.dataset.id) : null; if (st !== S.hoverMark) { S.hoverMark = st; dirty(); } });
    body.addEventListener('pointerleave', () => { S.hoverMark = null; dirty(); });
  }
  function revealMark(st) {
    const b = bounds(st); const [x0, y0] = toScreen([b[0], b[1]]); const [x1, y1] = toScreen([b[2], b[3]]);
    if (x0 >= S.safe.x && y0 >= S.safe.y && x1 <= S.safe.x + S.safe.w && y1 <= S.safe.y + S.safe.h) return;
    const v = vstate(); animateView({ zoom: v.zoom, cx: (b[0] + b[2]) / 2, cy: (b[1] + b[3]) / 2 });
  }
  function updateCounts() {
    const n = S.strokes.filter(st => st.kind !== 'match').length;
    const badge = $('#marks-badge'); badge.textContent = n ? String(n) : ''; badge.dataset.n = n;
    updateToolbar();
  }
  function toggleClaude() { S.showClaude = !S.showClaude; dirty(); renderMarks(); renderBanners(); }

  // ---------------------------------------------------------------- composer
  const note = $('#note');
  function autoGrow() { note.style.height = 'auto'; note.style.height = Math.min(154, note.scrollHeight) + 'px'; computeSafe(); dirty(); }
  note.addEventListener('input', () => { autoGrow(); S.recallIndex = -1; saveDraft(); });
  function placeholder() {
    const c = S.categories.find(x => x.id === S.task);
    let ph = (c && c.example) || 'Ask Claude about this view';
    if (S.mode === 'reply') ph = S.boot.text.reply[1];
    else if (S.mode === 'options') ph = S.boot.text.options[1];
    else if (S.mode === 'style') ph = S.boot.text.style[1];
    note.placeholder = ph;
  }
  function recall(dir) {
    const list = S.settings.recent_notes || []; if (!list.length) return;
    S.recallIndex = clamp(S.recallIndex + dir, -1, list.length - 1);
    note.value = S.recallIndex < 0 ? '' : list[S.recallIndex]; autoGrow(); note.setSelectionRange(note.value.length, note.value.length);
  }
  function renderChips() {
    const ch = $('#chips'); let html = '';
    if (S.mode === 'reply') html += `<span class="pill claude noclose">${icon('reply', 's14')}<span>Answering Claude's crit of ${esc(S.boot.reply.reply_to || '')}</span></span>`;
    if (S.mode === 'options' || S.mode === 'style') { const o = S.options[S.optIndex]; html += `<span class="pill accent noclose"><span>Picking ${S.mode === 'style' ? 'style' : 'option'} ${esc(o ? o.label : '')}</span></span>`; }
    if (S.mode === 'draft') html += `<span class="pill noclose">${icon('clock', 's14')}<span>Restored draft from ${esc(S.boot.draft_when || '')}</span></span>`;
    if (S.task !== 'general') {
      const d = taskDef(S.task); const tg = S.task === 'materials' ? '' : tagFor(S.task).replace(/^[^:]+: ?/, '');
      html += `<span class="pill accent" data-tip="${esc(d.label)}: what Claude gets" data-sub="${esc(briefFor(S.task).slice(0, 420))}">${icon(d.icon, 's14')}<span>${esc(d.label)}${tg && tg !== d.label ? ' \u00b7 ' + esc(tg) : ''}</span><button class="btn x" data-x="task">${icon('x', 's14')}</button></span>`;
    }
    (S.picks[S.task] || []).forEach((p, i) => { html += `<span class="pill" data-tip="${esc(p.name)}" data-sub="${esc(p.on && p.on !== 'note' ? 'Goes on ' + onLabel(p.on).toLowerCase() : 'Goes where your note says')}">${icon(S.task === 'lighting' ? 'sun' : 'swatch', 's14')}<span>${esc(p.name)}${/^#\d+$/.test(p.on || '') ? ', mark ' + esc(p.on.slice(1)) : ''}</span><button class="btn x" data-x="pick:${i}">${icon('x', 's14')}</button></span>`; });
    for (const id of S.presets) { const p = findPreset(id); if (p) html += `<span class="pill" data-tip="${esc(p.name)}" data-sub="${esc(p.instructions || '')}"><span>${esc(p.name)}</span><button class="btn x" data-x="${esc(id)}">${icon('x', 's14')}</button></span>`; }
    if (S.queue.length) html += `<span class="pill" data-tip="Captures waiting in the batch" data-sub="${esc(S.queue.map(q => q.stamp + (q.note ? ': ' + q.note : '')).join('\n'))}">${icon('stack', 's14')}<span>${S.queue.length} in batch, sent with this one</span><button class="btn x" data-x="queue">${icon('x', 's14')}</button></span>`;
    ch.innerHTML = html;
    $('#queue-badge').textContent = S.queue.length ? String(S.queue.length) : ''; $('#queue-badge').dataset.n = S.queue.length;
    $('#send-badge').textContent = S.queue.length ? String(S.queue.length + 1) : ''; $('#send-badge').dataset.n = S.queue.length ? S.queue.length + 1 : 0;
    requestAnimationFrame(() => { computeSafe(); dirty(); });
  }
  function bindComposer() {
    $('#chips').addEventListener('click', e => {
      const x = e.target.closest('[data-x]'); if (!x) return; const k = x.dataset.x;
      if (k === 'task') { S.task = 'general'; S.presets = []; renderSidebar(); renderChips(); placeholder(); saveSettings(); }
      else if (k === 'queue') sheet({ title: 'Clear the batch?', text: `The ${S.queue.length} queued capture${S.queue.length === 1 ? '' : 's'} will be deleted. This one stays.`,
        buttons: [{ label: 'Keep' }, { label: 'Clear batch', danger: true, run: () => Bridge.call('queue_clear').then(() => { S.queue = []; renderChips(); toast('Batch cleared'); }) }] });
      else if (k.startsWith('pick:')) { (S.picks[S.task] || []).splice(+k.slice(5), 1); saveSettings(); refreshSidebar(); saveDraft(); }
      else { S.presets = S.presets.filter(id => id !== k); renderSidebar(); renderChips(); saveSettings(); }
    });
    $('#send-btn').addEventListener('click', () => send('send'));
    $('#queue-btn').addEventListener('click', () => send('queue'));
    $('#target-btn').addEventListener('click', e => targetMenu(e.currentTarget));
    $('#attach-btn').addEventListener('click', e => attachMenu(e.currentTarget));
  }
  function targetLabel() {
    const b = $('#target-btn');
    if (!S.settings.jump) { b.innerHTML = `${icon('terminal', 's16')}<span class="t">Don't paste</span>${icon('chevron', 's14')}`; b.classList.add('off'); return; }
    b.classList.remove('off');
    const t = S.terminals.find(x => x.hwnd === S.target);
    b.innerHTML = `${icon('terminal', 's16')}<span class="t">${esc(t ? t.title : (S.terminals.length ? 'Pick a Claude Code window' : 'No terminal found'))}</span>${icon('chevron', 's14')}`;
  }
  function targetMenu(anchor) {
    const items = [{ head: 'Paste the request into' }];
    for (const t of S.terminals) items.push({ icon: 'terminal', label: t.title, sub: t.claude ? 'Claude Code' : t.kind, check: S.settings.jump && S.target === t.hwnd, run: () => { S.target = t.hwnd; S.settings.jump = true; S.settings.target_title = t.title; saveSettings(); targetLabel(); } });
    if (!S.terminals.length) items.push({ label: 'No Windows Terminal window found', disabled: true });
    items.push('-');
    items.push({ icon: 'x', label: 'Don\'t paste', sub: 'The capture attaches to your next prompt in any session', check: !S.settings.jump, run: () => { S.settings.jump = false; saveSettings(); targetLabel(); } });
    items.push({ icon: 'redo', label: 'Look for terminals again', run: () => Bridge.call('terminals').then(r => { S.terminals = r.terminals; if (!S.terminals.some(x => x.hwnd === S.target)) S.target = r.target; targetLabel(); toast(`${S.terminals.length} terminal window${S.terminals.length === 1 ? '' : 's'} found`, 'terminal'); }) });
    menu(anchor, items, { wide: true, up: true });
  }
  function attachMenu(anchor) {
    menu(anchor, [
      { head: 'Add an image to this request' },
      { icon: 'image', label: 'From a file...', run: addRefFromFile },
      { icon: 'paste', label: 'Paste from the clipboard', key: 'Ctrl+V', run: pasteImage },
      { icon: 'search', label: 'Find one in Chrome', key: 'Ctrl+F', run: examples },
      '-',
      { head: 'The marked image' },
      { icon: 'download', label: 'Save it as a PNG...', key: 'Ctrl+S', run: saveAs },
      { icon: 'copy', label: 'Copy it', key: 'Ctrl+C', run: copyImage },
      '-',
      { icon: 'trash', label: 'Clear all marks', key: 'Ctrl+Del', danger: true, run: clearAll },
    ], { up: true });
  }

  // ---------------------------------------------------------------- references (images the user drops or pastes onto the spot)
  function centreImg() { return toImg(S.safe.x + S.safe.w / 2, S.safe.y + S.safe.h / 2); }
  function gotRef(r) {
    if (!r || r.cancelled) return;
    if (r.error) { toast(r.error, 'warn'); return; }
    const refs = r.refs || (r.ref ? [r.ref] : []);
    if (!refs.length) { toast('No image found in that', 'warn'); return; }
    // load them all, then add the cards in label order (a small PNG finishes before a big JPEG),
    // as one step, so one undo takes back the whole drop
    Promise.all(refs.map(ref => new Promise(res => {
      const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = ref.url;
    }))).then(ims => {
      const ok = refs.filter((ref, i) => ims[i]);
      if (ok.length) {
        snapshot();
        refs.forEach((ref, i) => {
          const im = ims[i]; if (!im) return;
          S.refImgs[ref.label] = im;
          const k = (0.2 * S.iw) / Math.max(im.naturalWidth, im.naturalHeight);
          const size = [r1(im.naturalWidth * k), r1(im.naturalHeight * k)];
          const x = clamp(ref.at[0], size[0] / 2, S.iw - size[0] / 2), y = clamp(ref.at[1], size[1] / 2, S.ih - size[1] / 2);
          const st = { kind: 'ref', label: ref.label, file: ref.file, source: ref.source, points: [[r1(x), r1(y)]], size, role: defaultRole(), _id: ++S.ids };
          if (ref.original) st.original = ref.original;
          S.refCount = Math.max(S.refCount, +ref.label.slice(1));
          S.strokes.push(st);
        });
        changed();
        toast(`${ok.map(x => x.label).join(', ')} added as ${roleLabel(defaultRole()).toLowerCase()}. Change what ${ok.length === 1 ? 'it is' : 'they are'} for in the sidebar, under Images`, 'image', 3600);
      }
      if (ok.length < refs.length) toast('That image wouldn\'t load', 'warn');
    });
  }
  function pasteImage() {
    const p = S.mouse.in ? toImg(S.mouse.x, S.mouse.y) : centreImg();
    spinner('Reading the clipboard...');
    Bridge.call('paste', { at: p, n: S.refCount + 1 }).then(r => { spinner(); gotRef(r); }).catch(e => { spinner(); toast('Couldn\'t paste that (' + e.message + ')', 'warn'); });
  }
  function examples() { Bridge.call('examples').catch(e => toast(e.message, 'warn')); }

  // drops: files from Explorer (with their real paths), images dragged from a browser
  let dragDepth = 0;
  function dropKind(e) { return e.target && e.target.closest && e.target.closest('[data-dropzone="lib"]') ? 'lib' : 'ref'; }
  window.addEventListener('dragenter', e => { e.preventDefault(); dragDepth++; showDrop(e); });
  window.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; showDrop(e); });
  window.addEventListener('dragleave', e => { if (--dragDepth <= 0) { dragDepth = 0; hideDrop(); } });
  window.addEventListener('drop', e => { e.preventDefault(); dragDepth = 0; hideDrop(); try { onDrop(e); } catch (err) { report(err, 'drop'); } });
  function showDrop(e) {
    const kind = dropKind(e); const ov = $('#drop-overlay'); const ring = $('#drop-ring');
    if (kind === 'lib') {
      const z = e.target.closest('[data-dropzone="lib"]');
      $('#drop-text').textContent = z.dataset.kind === 'hdris' ? 'Drop to add it to your HDRIs' : 'Drop to add it to your materials library';
      const r = z.getBoundingClientRect(); Object.assign(ring.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', borderRadius: '14px' });
    } else {
      const side = e.target && e.target.closest && e.target.closest('#sidebar, #marks-panel, #composer');
      $('#drop-text').textContent = S.photo ? 'Close the photo first (Tab)' : `Drop to add R${S.refCount + 1} to this request` + (side ? '' : ', here');
      const t = T(); Object.assign(ring.style, { left: t.ox + 'px', top: t.oy + 'px', width: t.iw * t.s + 'px', height: t.ih * t.s + 'px', borderRadius: '14px' });
    }
    ov.classList.add('show'); ring.classList.add('show');
  }
  function hideDrop() { $('#drop-overlay').classList.remove('show'); $('#drop-ring').classList.remove('show'); }
  function dropAlternatives(dt) {
    const alts = [];
    try { const html = dt.getData('text/html'); const re = /<img[^>]+src="((?:https?:\/\/|data:image)[^"]+)"/gi; let m; while ((m = re.exec(html || ''))) alts.push(m[1].replace(/&amp;/g, '&')); } catch (x) { /* none */ }
    try { const ul = dt.getData('text/uri-list'); (ul || '').split(/\r?\n/).filter(u => u && !u.startsWith('#')).forEach(u => alts.push(u.trim())); } catch (x) { /* none */ }
    try { const tx = (dt.getData('text/plain') || '').trim(); if (/^https?:\/\//i.test(tx)) alts.push(tx.split(/\s/)[0]); } catch (x) { /* none */ }
    return alts.filter((a, i) => alts.indexOf(a) === i);
  }
  function readB64(file) {
    return new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(',')[1]); fr.onerror = rej; fr.readAsDataURL(file); });
  }
  // Files dropped from disk go over as real paths. A file that exists only in memory (some
  // browsers and apps drag those) is refused by WebView2 ("not a file on the disk"), and outside
  // WebView2 there are no paths at all: those go over as bytes instead.
  async function callWithFiles(type, payload, files) {
    const asBytes = async () => {
      const b64 = [];
      for (const f of files.slice(0, 6)) { try { b64.push({ name: f.name, data: await readB64(f) }); } catch (x) { /* unreadable */ } }
      return Bridge.call(type, Object.assign({}, payload, { b64 }));
    };
    if (!files.length) return Bridge.call(type, payload);
    if (!Bridge.live || !window.chrome.webview.postMessageWithAdditionalObjects) return asBytes();
    try { return await Bridge.call(type, payload, files); }
    catch (err) { if (err && err.postFailed) return asBytes(); throw err; }
  }
  async function onDrop(e) {
    const dt = e.dataTransfer; const kind = dropKind(e);
    const files = [...(dt.files || [])];
    const alts = dropAlternatives(dt);
    const at = e.target && e.target.closest && e.target.closest('#sidebar, #marks-panel, #composer') ? centreImg() : toImg(e.clientX, e.clientY);
    if (kind === 'lib') {
      const lk = e.target.closest('[data-dropzone="lib"]').dataset.kind;
      spinner('Adding it to your library...');
      callWithFiles('preset_add', { task: lk === 'hdris' ? 'lighting' : 'materials', library: true, alts, names: files.map(f => f.name) }, files.slice(0, 6)).then(r => { spinner(); libAdded(r, lk); })
        .catch(err => { spinner(); toast('Couldn\'t add that to the library (' + err.message + ')', 'warn'); });
      return;
    }
    if (S.photo) { toast('Close the photo first (Tab), then drop', 'warn'); return; }
    const images = files.filter(f => /^image\//.test(f.type) || /\.(png|jpe?g|bmp|gif|tiff?|webp|heic|heif)$/i.test(f.name));
    if (!images.length && !alts.length) { toast('No image found in that', 'warn'); return; }
    spinner(alts.length && !images.length ? 'Fetching the image...' : 'Adding the image...');
    callWithFiles('ref', { at, n: S.refCount + 1, alts, names: images.map(f => f.name) }, images.slice(0, 6))
      .then(r => { spinner(); gotRef(r); }).catch(err => { spinner(); toast('Couldn\'t add that image (' + err.message + ')', 'warn'); });
  }

  // ---------------------------------------------------------------- export, save, copy, send
  async function exportPNG() {
    const c = document.createElement('canvas'); c.width = S.iw; c.height = S.ih; const x = c.getContext('2d');
    const base = S.options.length ? (S.optImgs[S.options[S.optIndex].label] || S.img) : S.img;
    x.drawImage(base, 0, 0, S.iw, S.ih);
    const t = { s: 1, ox: 0, oy: 0, iw: S.iw, ih: S.ih, export: true };
    if (S.mode === 'reply') for (const st of S.claude) drawMark(x, st, t);
    for (const st of S.strokes) drawMark(x, st, t);
    return c.toDataURL('image/png').split(',')[1];
  }
  async function saveAs() {
    commitText(); const png = await exportPNG(); Bridge.raw('BLOB:marked:', png);
    Bridge.call('save_png', { blob: 'marked' }).then(r => { if (r && r.path) toast('Saved ' + r.name, 'download'); }).catch(e => toast('Save failed (' + e.message + ')', 'warn'));
  }
  async function copyImage() {
    commitText(); const png = await exportPNG(); Bridge.raw('BLOB:marked:', png);
    Bridge.call('copy_png', { blob: 'marked' }).then(() => toast('Marked image copied', 'copy')).catch(e => toast('Copy failed (' + e.message + ')', 'warn'));
  }
  function cleanStrokes(list) {
    return list.map(st => { const o = {}; for (const k in st) if (k[0] !== '_') o[k] = st[k]; return o; });
  }
  async function send(mode) {
    if (S.sending) return;
    if (S.photo) closePhoto();
    if (S.live) { const st = S.live; S.live = null; S.drag = null; commitNew(st); }
    commitText(); if (S.comment) closeComment(true);
    const text = note.value.trim();
    if (mode === 'queue' && S.mode !== 'capture' && S.mode !== 'draft') { toast('Replies and boards are sent on their own', 'warn'); return; }
    S.sending = true; document.body.classList.add('sending');
    try {
      const png = await exportPNG(); Bridge.raw('BLOB:marked:', png);
      const recent = (S.settings.recent_notes || []).filter(n => n !== text); if (text) recent.unshift(text); S.settings.recent_notes = recent.slice(0, 20);
      for (const st of S.strokes) if (st.kind === 'ref') st.role = roleOf(st);
      const job = S.mode === 'capture' || S.mode === 'draft';
      const taskOptions = job ? Object.assign(opts(S.task), { picks: (S.picks[S.task] || []).map(p => Object.assign({}, p)) }) : null;
      await Bridge.call('send', {
        mode, note: text, strokes: cleanStrokes(S.strokes), claude_strokes: cleanStrokes(S.claude), task: S.task, presets: S.presets.slice(),
        task_label: taskDef(S.task).label, task_options: taskOptions, task_brief: job ? briefFor(S.task) : '', task_tag: job ? tagFor(S.task) : '',
        target: S.settings.jump ? S.target : null, option: S.options.length ? S.options[S.optIndex].label : null, settings: S.settings, blob: 'marked',
      });
      if (mode === 'queue') { toast('Added to the batch', 'stack'); }
    } catch (e) {
      toast('Send failed (' + e.message + ')', 'warn');
    } finally { S.sending = false; document.body.classList.remove('sending'); }
  }
  function cancel() {
    const n = S.strokes.length;
    if (!n && !note.value.trim()) { Bridge.fire('cancel', { settings: S.settings }); return; }
    sheet({ title: S.mode === 'reply' || S.mode === 'options' || S.mode === 'style' ? 'Close without answering?' : 'Discard this markup?',
      text: S.mode === 'reply' || S.mode === 'options' || S.mode === 'style' ? 'Your marks are thrown away. The reply stays waiting: ClaudeReply opens it again.'
        : `${n ? n + ' mark' + (n === 1 ? '' : 's') : 'Your request'} will be thrown away.`,
      buttons: [{ label: 'Keep editing' }, { label: 'Discard', danger: true, run: () => Bridge.fire('cancel', { settings: S.settings }) }] });
  }
  Bridge.on('confirm_close', () => cancel());

  // ---------------------------------------------------------------- options and style boards
  function flipOption(i) {
    if (!S.options.length) return; S.optIndex = (i + S.options.length) % S.options.length; renderBanners(); renderChips(); dirty();
  }

  // ---------------------------------------------------------------- banners at the top of the stage
  function renderBanners() {
    const bn = $('#banners'); const out = [];
    if (S.photo) {
      const ref = refByLabel(S.photo); const n = ref && ref.matches ? ref.matches.length : 0;
      out.push(`<div class="glass banner"><span class="ico">${icon('match')}</span><span class="msg">Photo ${esc(S.photo)} <em>· ${n} point${n === 1 ? '' : 's'} here · ${modelMatches().length} on the model</em></span><button class="btn text primary" data-b="photo-close">Back to the model <span class="kbd" style="background:rgba(255,255,255,.25);color:#fff">Tab</span></button></div>`);
    } else if (ghostRefs() && S.photoLast && refByLabel(S.photoLast)) {
      const ref = refByLabel(S.photoLast); const n = ref.matches ? ref.matches.length : 0;
      out.push(`<div class="glass banner"><span class="ico">${icon('match')}</span><span class="msg">Model points for ${esc(S.photoLast)} <em>· ${n} on the photo · ${modelMatches().length} here</em></span><button class="btn text primary" data-b="photo-open">Open the photo <span class="kbd" style="background:rgba(255,255,255,.25);color:#fff">Tab</span></button></div>`);
    } else if (S.options.length) {
      const o = S.options[S.optIndex];
      out.push(`<div class="glass banner"><div class="opt-seg">${S.options.map((x, i) => `<button class="btn ${i === S.optIndex ? 'on' : ''}" data-opt="${i}">${esc(x.label)}</button>`).join('')}</div>
        <span class="msg">${esc(o.title || '')} <em>${o.idea ? '· ' + esc(o.idea) : ''}</em></span><span class="kbd">&larr;</span><span class="kbd" style="margin-left:2px;margin-right:6px">&rarr;</span></div>`);
    } else if (S.mode === 'reply') {
      const n = S.claude.filter(st => st.kind === 'pin').length || S.claude.length;
      out.push(`<div class="glass banner"><span class="ico claude">${icon('crit')}</span><span class="msg">${esc(S.boot.reply.title || 'Claude\'s crit')} <em>· ${n} finding${n === 1 ? '' : 's'}</em></span>
        <button class="btn text" data-b="claude-toggle">${S.showClaude ? 'Hide' : 'Show'} <span class="kbd">H</span></button><button class="btn text" data-b="claude-list">List</button></div>`);
    }
    if (S.boot && S.boot.pending_reply && S.mode === 'capture' && !S.dismissedReply) {
      const pr = S.boot.pending_reply;
      out.push(`<div class="glass banner"><span class="ico claude">${icon('reply')}</span><span class="msg">Claude replied to ${esc(pr.reply_to)} <em>· ${esc(pr.title || '')}</em></span>
        <button class="btn text primary" data-b="reply-open">Open</button><button class="btn text" data-b="reply-dismiss">Dismiss</button></div>`);
    }
    if (S.boot && S.boot.drafts && S.boot.drafts.length && S.mode === 'capture' && !S.dismissedDraft) {
      const d = S.boot.drafts[0];
      out.push(`<div class="glass banner"><span class="ico">${icon('clock')}</span><span class="msg">Unsent markup from ${esc(d.when)} <em>· ${d.marks} mark${d.marks === 1 ? '' : 's'}${d.note ? ' · "' + esc(d.note.slice(0, 40)) + '"' : ''}</em></span>
        <button class="btn text primary" data-b="draft-open">Restore</button><button class="btn text" data-b="draft-discard">Discard</button></div>`);
    }
    bn.innerHTML = out.join('');
    requestAnimationFrame(() => { computeSafe(); dirty(); lensSoon(); updateGlass(true); });
  }
  function bindBanners() {
    $('#banners').addEventListener('click', e => {
      const o = e.target.closest('[data-opt]'); if (o) { flipOption(+o.dataset.opt); return; }
      const b = e.target.closest('[data-b]'); if (!b) return;
      const a = b.dataset.b;
      if (a === 'photo-close') closePhoto();
      else if (a === 'photo-open') openPhoto(S.photoLast);
      else if (a === 'claude-toggle') toggleClaude();
      else if (a === 'claude-list') togglePanel('marks', true);
      else if (a === 'reply-open') {
        const go = () => Bridge.fire('reply_open', { settings: S.settings });
        if (S.strokes.length) sheet({ title: 'Open Claude\'s reply?', text: 'This capture is kept as a draft, so you can restore it next time.', buttons: [{ label: 'Stay here' }, { label: 'Open the reply', primary: true, run: go }] });
        else go();
      } else if (a === 'reply-dismiss') { Bridge.call('reply_dismiss', { id: S.boot.pending_reply.id }).then(() => { S.dismissedReply = true; renderBanners(); toast('Reply moved to answered'); }); }
      else if (a === 'draft-open') {
        const go = () => Bridge.fire('draft_open', { stamp: S.boot.drafts[0].stamp, settings: S.settings });
        if (S.strokes.length) sheet({ title: 'Restore the older markup?', text: 'This capture is kept as a draft too.', buttons: [{ label: 'Stay here' }, { label: 'Restore', primary: true, run: go }] });
        else go();
      } else if (a === 'draft-discard') { Bridge.call('draft_discard', { stamp: S.boot.drafts[0].stamp }).then(() => { S.boot.drafts.shift(); if (!S.boot.drafts.length) S.dismissedDraft = true; renderBanners(); }); }
    });
  }

  // ---------------------------------------------------------------- toasts, tooltips, spinner
  function toast(text, ic, ms) {
    const warn = ic === 'warn';
    const t = el(`<div class="glass toast ${warn ? 'warn' : ''}">${icon(warn ? 'warn' : (ic || 'check'), 's16')}<span>${esc(text)}</span></div>`);
    const box = $('#toasts'); box.appendChild(t); while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, ms || (warn ? 5200 : 3000));
  }
  let spin = null;
  function spinner(text) {
    if (spin) { spin.remove(); spin = null; }
    if (text) { spin = el(`<div id="spinner"><div class="glass"><span class="spin"></span><span>${esc(text)}</span></div></div>`); document.body.appendChild(spin); }
  }
  const tip = $('#tooltip'); let tipTimer = 0, tipFor = null;
  document.addEventListener('pointerover', e => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (t === tipFor) return; hideTip(); if (!t || S.drag) return; tipFor = t;
    tipTimer = setTimeout(() => {
      const key = t.dataset.key ? `<span class="kbd">${esc(t.dataset.key)}</span>` : '';
      tip.innerHTML = `${esc(t.dataset.tip)}${key}${t.dataset.sub ? `<span class="tip-sub">${esc(t.dataset.sub)}</span>` : ''}`;
      const r = t.getBoundingClientRect(); tip.classList.add('show');
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let x = r.left + r.width / 2 - w / 2, y = r.bottom + 8; if (y + h > innerHeight - 8) y = r.top - h - 8;
      tip.style.left = clamp(x, 8, innerWidth - w - 8) + 'px'; tip.style.top = y + 'px';
    }, 550);
  });
  function hideTip() { clearTimeout(tipTimer); tipFor = null; tip.classList.remove('show'); }

  // ---------------------------------------------------------------- menus and sheets
  function closeMenus() { $('#layer').innerHTML = ''; }
  function menu(anchor, items, opt) {
    opt = opt || {}; closeMenus(); hideTip();
    const m = el(`<div class="glass menu ${opt.wide ? 'wide' : ''}"></div>`);
    for (const it of items) {
      if (it === '-') { m.appendChild(el('<div class="ms"></div>')); continue; }
      if (it.head) { m.appendChild(el(`<div class="mh">${esc(it.head)}</div>`)); continue; }
      if (it.html) { m.appendChild(el(it.html)); continue; }
      const b = el(`<button class="mi ${it.danger ? 'danger' : ''} ${it.sub ? 'tall' : ''}" ${it.disabled ? 'disabled' : ''}>
        ${it.check != null ? `<span class="ck">${it.check ? icon('check', 's16') : ''}</span>` : (it.icon ? icon(it.icon, 's16') : '')}
        <span class="l">${esc(it.label)}${it.sub ? `<span class="l2">${esc(it.sub)}</span>` : ''}</span>${it.key ? `<span class="k">${esc(it.key)}</span>` : ''}</button>`);
      b.addEventListener('click', () => { closeMenus(); if (it.run) it.run(); });
      m.appendChild(b);
    }
    const layer = $('#layer'); const shield = el('<div style="position:fixed;inset:0"></div>');
    shield.addEventListener('pointerdown', closeMenus); shield.addEventListener('contextmenu', e => { e.preventDefault(); closeMenus(); });
    layer.appendChild(shield); layer.appendChild(m);
    const w = m.offsetWidth, h = m.offsetHeight;
    let x, y;
    if (anchor && anchor.getBoundingClientRect) {
      const r = anchor.getBoundingClientRect(); x = r.left + r.width / 2 - w / 2; y = opt.up ? r.top - h - 10 : r.bottom + 10;
      if (y < 8) y = r.bottom + 10; if (y + h > innerHeight - 8) y = r.top - h - 10;
      m.style.setProperty('--ox', (r.left + r.width / 2 - clamp(x, 8, innerWidth - w - 8)) + 'px'); m.style.setProperty('--oy', opt.up ? '100%' : '0%');
    } else { x = anchor.x; y = anchor.y; if (y + h > innerHeight - 8) y = anchor.y - h; m.style.setProperty('--ox', '0'); m.style.setProperty('--oy', '0'); }
    m.style.left = clamp(x, 8, innerWidth - w - 8) + 'px'; m.style.top = clamp(y, 8, innerHeight - h - 8) + 'px';
    return m;
  }
  function markMenu(st, x, y) {
    const items = [];
    if (st.kind === 'text') items.push({ icon: 'edit', label: 'Edit the text', run: () => beginText(st) });
    else items.push({ icon: 'comment', label: st.comment ? 'Edit the comment...' : 'Add a comment...', run: () => openComment(st) });
    if (st.kind === 'ref') items.push({ icon: 'match', label: 'Open for photo match', run: () => openPhoto(st.label) });
    if (!['ref', 'match'].includes(st.kind)) items.push({ html: `<div class="dots">${COLOURS.map((c, i) => `<button class="btn swatch ${c.name === st.colour ? 'on' : ''}" style="--c:${css(c.rgb)}" data-i="${i}"><i></i></button>`).join('')}</div>` });
    items.push('-');
    if (st.kind !== 'ref') items.push({ icon: 'duplicate', label: 'Duplicate', run: () => duplicateMark(st) });
    items.push({ icon: 'front', label: 'Bring to front', run: () => toFront(st) });
    items.push({ icon: 'trash', label: 'Delete', key: 'Del', danger: true, run: () => removeMark(st) });
    const m = menu({ x, y }, items);
    m.addEventListener('click', e => { const b = e.target.closest('[data-i]'); if (b) { recolour(st, +b.dataset.i); closeMenus(); } });
  }
  function canvasMenu(x, y) {
    menu({ x, y }, [
      { icon: 'undo', label: 'Undo', key: 'Ctrl+Z', disabled: !S.history.length, run: undo },
      { icon: 'redo', label: 'Redo', key: 'Ctrl+Y', disabled: !S.future.length, run: redo },
      '-',
      { icon: 'paste', label: 'Paste an image here', key: 'Ctrl+V', run: pasteImage },
      { icon: 'fit', label: 'Zoom to fit', key: 'Ctrl+0', run: fitView },
      '-',
      { icon: 'trash', label: 'Clear all marks', key: 'Ctrl+Del', danger: true, disabled: !S.strokes.length, run: clearAll },
    ]);
  }
  function sheet(o) {
    closeMenus();
    const sc = el(`<div class="scrim"><div class="glass sheet ${o.wide ? 'wide' : ''}"><h3>${esc(o.title)}</h3>${o.text ? `<p>${esc(o.text)}</p>` : ''}${o.html || ''}<div class="acts"></div></div></div>`);
    const acts = sc.querySelector('.acts');
    (o.buttons || [{ label: 'Done', primary: true }]).forEach(b => {
      const btn = el(`<button class="btn ${b.primary ? 'primary' : ''} ${b.danger ? 'danger' : ''}">${esc(b.label)}</button>`);
      btn.addEventListener('click', () => { closeMenus(); if (b.run) b.run(); }); acts.appendChild(btn);
    });
    sc.addEventListener('pointerdown', e => { if (e.target === sc) closeMenus(); });
    $('#layer').appendChild(sc);
    const pr = sc.querySelector('.btn.primary, .btn.danger'); if (pr) setTimeout(() => pr.focus(), 30);
    return sc;
  }
  function helpSheet() {
    const rows = (title, list) => `<h4>${title}</h4>` + list.map(([l, k]) => `<div class="row"><span class="l">${esc(l)}</span><span class="kbd">${esc(k)}</span></div>`).join('');
    sheet({ title: 'ClaudeMark shortcuts', wide: true, html: `<p>Mark the capture, say what you want, press Enter. Claude gets the image, the camera, every mark and what Rhino finds under it.</p><div class="keys">` +
      rows('Tools', TOOLS.map(x => [x.name, x.key === 'M' ? 'Shift+M' : x.key.toUpperCase()]).concat([['Pan', 'Space+drag'], ['Hide Claude\'s marks (reply)', 'H']])) +
      rows('Marks', [['Colour', '1 to 7'], ['Thinner, thicker', '[  ]'], ['Straight line, square, 15 degree snap', 'Shift'], ['Comment on a mark', 'Double-click'], ['Delete the selected or hovered mark', 'Del'], ['Nudge the selected mark', 'Arrows'], ['Undo, redo', 'Ctrl+Z, Ctrl+Y'], ['Clear all', 'Ctrl+Del']]) +
      rows('View', [['Zoom', 'Scroll or Ctrl+scroll'], ['Fit, actual size', 'Ctrl+0, Ctrl+1'], ['Pan', 'Middle-drag']]) +
      rows('Send', [['Send to Claude', 'Enter'], ['New line in the request', 'Shift+Enter'], ['Add to batch and keep going', 'Ctrl+Shift+Enter'], ['Earlier requests', 'Up in an empty box'], ['Paste an image as a reference', 'Ctrl+V'], ['Save, copy the marked image', 'Ctrl+S, Ctrl+C'], ['Find examples in Chrome', 'Ctrl+F'], ['Close (asks first if you marked)', 'Esc']]) + '</div>' });
  }
  function settingsSheet() {
    const st = S.settings;
    const tg = (key, label, hint) => `<div class="toggle-row"><span class="lbl">${esc(label)}${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</span><button class="switch ${st[key] ? 'on' : ''}" data-set="${key}"></button></div>`;
    const sc = sheet({ title: 'Settings', html: `
      <div class="sect" style="padding-left:2px">Appearance</div>
      <div class="segctl" id="set-theme">${['auto', 'dark', 'light'].map(x => `<button data-theme="${x}" class="${(st.appearance || 'auto') === x ? 'on' : ''}">${x[0].toUpperCase() + x.slice(1)}</button>`).join('')}</div>
      <div class="sect" style="padding-left:2px">Accent</div>
      <div class="accents">${Object.keys(ACCENTS).map(k => `<button data-accent="${k}" class="${(st.accent || 'blue') === k ? 'on' : ''}" style="--c:${ACCENTS[k]}" data-tip="${k[0].toUpperCase() + k.slice(1)}"></button>`).join('')}</div>
      <div class="set-group" style="margin-top:16px">
        ${tg('jump', 'Paste into Claude Code', 'Bring the terminal forward and put the image and request in its prompt')}
        ${tg('hover_inspect', 'Show what is under the cursor', 'Rhino\'s pick under the pointer, live')}
        ${tg('number_marks', 'Number boxes, ellipses and clouds', 'So you can say "fix 2 and 4"')}
        ${tg('show_comments', 'Show comments on the image', 'Claude sees them in the picture as well as in the data')}
        ${tg('keep_task', 'Keep the task and presets', 'Otherwise each capture starts on General')}
      </div>
      <div class="set-group">
        ${tg('refraction', 'Glass refraction', 'The capture bends at the edges of the bars, like real glass')}
        ${tg('reduce_transparency', 'Reduce transparency', 'Solid panels instead of glass')}
        ${tg('reduce_motion', 'Reduce motion')}
      </div>
      <div style="display:flex;gap:8px;margin-top:6px"><button class="btn text" style="background:var(--fill2)" data-open="folder">${icon('folder', 's16')}Open the ClaudeMark folder</button></div>
      <p style="margin:12px 0 0;font-size:11.5px">ClaudeMark ${esc(S.boot.version || '')}. The old window is still there as the Rhino command ClaudeMarkClassic.</p>` });
    sc.addEventListener('click', e => {
      const sw = e.target.closest('[data-set]'); if (sw) { const k = sw.dataset.set; st[k] = !st[k]; sw.classList.toggle('on', !!st[k]); applySettings(); saveSettings(); }
      const th = e.target.closest('[data-theme]'); if (th) { st.appearance = th.dataset.theme; sc.querySelectorAll('[data-theme]').forEach(b => b.classList.toggle('on', b === th)); applySettings(); saveSettings(); }
      const ac = e.target.closest('[data-accent]'); if (ac) { st.accent = ac.dataset.accent; sc.querySelectorAll('[data-accent]').forEach(b => b.classList.toggle('on', b === ac)); applySettings(); saveSettings(); }
      if (e.target.closest('[data-open]')) Bridge.fire('open_folder');
    });
  }
  const saveSettings = debounce(() => {
    S.settings.task = S.task; S.settings.presets = S.presets.slice(); S.settings.tool = S.tool; S.settings.picks = S.picks;
    Bridge.fire('settings', { settings: S.settings });
  }, 400);
  function applySettings() {
    const st = S.settings, root = document.documentElement;
    const dark = st.appearance === 'dark' || (st.appearance !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    const ac = ACCENTS[st.accent] || ACCENTS.blue; root.style.setProperty('--accent', ac);
    const n = parseInt(ac.slice(1), 16); root.style.setProperty('--accent-rgb', `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`);
    if (st.reduce_transparency) root.setAttribute('data-reduce-transparency', ''); else root.removeAttribute('data-reduce-transparency');
    if (st.reduce_motion) root.setAttribute('data-reduce-motion', ''); else root.removeAttribute('data-reduce-motion');
    Bridge.fire('theme', { dark });
    updateTopRight(); targetLabel(); dirty();
    if (S.img) { updateLens(); requestAnimationFrame(() => updateGlass(true)); }
  }
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applySettings);

  // ---------------------------------------------------------------- drafts (so a crash or a slip of Esc loses nothing)
  const saveDraft = debounce(() => {
    if (!S.boot || S.mode === 'reply' || S.mode === 'options' || S.mode === 'style') return;
    Bridge.raw('DRAFT:', JSON.stringify({ strokes: cleanStrokes(S.strokes), note: note.value, task: S.task, presets: S.presets, picks: S.picks, ref_count: S.refCount, saved: Date.now() }));
  }, 1200);

  // ---------------------------------------------------------------- boot
  function loadImage(url) { return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error('image ' + url)); im.src = url; }); }
  async function boot() {
    const b = await Bridge.call('boot');
    S.boot = b; S.mode = b.mode || 'capture'; S.iw = b.image.w; S.ih = b.image.h;
    S.settings = Object.assign({ hover_inspect: true, number_marks: true, show_comments: true, jump: true, keep_task: false, appearance: 'auto', accent: 'blue', refraction: true }, b.settings || {});
    applySettings();
    $('#backdrop').style.backgroundImage = `url('${b.image.url}')`;
    S.categories = b.categories; S.allPresets = b.presets || {}; S.env = b.env || {};
    S.terminals = b.terminals || []; S.target = b.target; S.queue = b.queue || [];
    const cn = S.settings.colour_name || OLD_COLOUR_ORDER[S.settings.colour] || 'red';
    S.colour = Math.max(0, COLOURS.findIndex(c => c.name === cn));
    S.width = clamp(S.settings.width == null ? 1 : S.settings.width, 0, 2);
    S.tool = TOOLS.some(x => x.id === S.settings.tool) ? S.settings.tool : 'box';
    S.task = (S.settings.keep_task && S.categories.some(c => c.id === S.settings.task)) ? S.settings.task : 'general';
    const known = new Set(Object.values(S.allPresets).flat().map(p => p.id));
    S.presets = S.settings.keep_task ? (S.settings.presets || []).filter(id => known.has(id)) : [];
    S.picks = S.settings.keep_task && S.settings.picks && typeof S.settings.picks === 'object' ? clone(S.settings.picks) : {};
    if (b.restore) { S.task = b.restore.task || S.task; S.presets = (b.restore.presets || []).filter(id => known.has(id)); if (b.restore.picks) S.picks = b.restore.picks; }
    if (!TASKS[S.task]) S.task = 'general';
    await document.fonts.load(`650 30px Inter`).catch(() => { });
    const jobs = [loadImage(b.image.url).then(im => { S.img = im; })];
    if (b.reply) {
      S.claude = (b.reply.strokes || []).map(st => Object.assign({}, st));
      S.claude.forEach(st => { if (st.kind === 'text') measureText(st); });
      if (b.reply.options) {
        S.options = b.reply.options;
        for (const o of S.options) jobs.push(loadImage(o.url).then(im => { S.optImgs[o.label] = im; }).catch(() => { }));
      }
    }
    const restored = (b.restore && b.restore.strokes) || [];
    for (const st of restored) {
      st._id = ++S.ids;
      if (st.kind === 'ref' && b.restore.ref_urls && b.restore.ref_urls[st.label]) jobs.push(loadImage(b.restore.ref_urls[st.label]).then(im => { S.refImgs[st.label] = im; }).catch(() => { }));
      if (st.kind === 'text') measureText(st);
    }
    S.strokes = restored; S.refCount = (b.restore && b.restore.ref_count) || restored.filter(st => st.kind === 'ref').length;
    if (b.restore && b.restore.note) note.value = b.restore.note;
    await Promise.all(jobs);
    buildToolbar(); buildTopRight(); bindSidebar(); bindMarks(); bindComposer(); bindBanners();
    docInfo(); renderSidebar(); renderMarks(); renderChips(); renderBanners(); placeholder(); targetLabel(); autoGrow();
    if (S.settings.sidebar_open) togglePanel('sidebar', true);
    if (S.settings.marks_open || S.mode === 'reply') togglePanel('marks', true);
    resetView(S.view, S.iw, S.ih);
    resize(); updateCounts(); setTool(S.tool);
    for (const sel of LENS) for (const e of document.querySelectorAll(sel)) ro.observe(e);
    updateLens();
    requestAnimationFrame(() => { openGlass(); dirty(); cv.focus(); updateGlass(true); });
    setTimeout(() => {   // once the bars have landed
      if (S.mode === 'reply') toast('Claude\'s findings are in orange. Mark your answer on top and send', 'crit', 4500);
      else if (S.mode === 'options' || S.mode === 'style') toast('Flip with the arrow keys, mark changes on the one you want, send', 'check', 4500);
    }, 450);
    Bridge.fire('ready', {});
  }
  window.addEventListener('resize', () => { if (S.boot) resize(); });
  Bridge.on('toast', d => toast(d.text, d.icon || 'check', d.ms));
  Bridge.on('focus', () => cv.focus());

  // ---------------------------------------------------------------- a test surface (used by the automated checks)
  window.CMTest = {
    S, T, toScreen, toImg, exportPNG, setTool, setColour, setWidth, select, openComment, closeComment, commitNew, newStroke,
    beginText, commitText, togglePanel, settingsSheet, helpSheet, menu, markMenu, canvasMenu, attachMenu, targetMenu, closeMenus, sheet,
    fitView, zoomAt, actualSize, openPhoto, closePhoto, addMatch, flipOption, send, cancel, undo, redo, toast, renderMarks, renderChips,
    setTask, togglePreset, showHover, layoutChrome, gotRef, measureText,
    TASKS, opts, setOpt, setOpts, defaults, briefFor, tagFor, setupsFor, applySetup, togglePick, libItems, renderSidebar, roleOf, saveSetupSheet, fieldMenu,
    ENGINE, openEnginePop, closeEnginePop, get engPop() { return engPop; },
    opening(at) {   // play the opening again; given a time in ms, hold it there so it can be photographed
      openHold = at == null ? null : at;
      endOpening(); void document.body.offsetWidth;
      const ran = openGlass();
      if (ran && openHold != null) for (const a of document.getAnimations()) if (/^cm-open/.test(a.animationName || '')) { a.pause(); a.currentTime = openHold; }
      return ran;
    },
    pointer(type, x, y, opt) { const ev = new PointerEvent(type, Object.assign({ clientX: x, clientY: y, bubbles: true, pointerId: 1, button: 0, buttons: type === 'pointerup' ? 0 : 1, isPrimary: true }, opt || {})); (type === 'pointerdown' ? cv : window).dispatchEvent(ev); },
    drag(kind, a, b, steps) { // a, b in image px; uses the real pointer handlers
      setTool(kind); const A = toScreen(a), B = toScreen(b); this.pointer('pointerdown', A[0], A[1]); steps = steps || 8;
      for (let i = 1; i <= steps; i++) this.pointer('pointermove', A[0] + (B[0] - A[0]) * i / steps, A[1] + (B[1] - A[1]) * i / steps, { target: cv });
      this.pointer('pointerup', B[0], B[1]);
    },
    key(k, opt) { window.dispatchEvent(new KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true }, opt || {}))); },
    async perf() { // frame times while drawing, and while zooming with the lens on and off
      const rafT = async (n, step) => { const ts = []; let last = performance.now(); for (let i = 0; i < n; i++) { step(i); await new Promise(r => requestAnimationFrame(r)); const now = performance.now(); ts.push(now - last); last = now; } ts.sort((a, b) => a - b); return { median: +ts[n >> 1].toFixed(1), p90: +ts[Math.floor(n * 0.9)].toFixed(1), max: +ts[n - 1].toFixed(1) }; };
      const out = {}; const A = toScreen([S.iw * 0.15, S.ih * 0.5]);
      setTool('pen'); this.pointer('pointerdown', A[0], A[1]);
      out.draw = await rafT(90, i => this.pointer('pointermove', A[0] + i * 7, A[1] + Math.sin(i / 5) * 40));
      this.pointer('pointerup', A[0] + 630, A[1]);
      const c = [S.safe.x + S.safe.w / 2, S.safe.y + S.safe.h / 2];
      out.zoom_lens = await rafT(60, i => zoomAt(i < 30 ? 1.02 : 1 / 1.02, c[0], c[1]));
      S.settings.refraction = false; updateLens();
      out.zoom_plain = await rafT(60, i => zoomAt(i < 30 ? 1.02 : 1 / 1.02, c[0], c[1]));
      S.settings.refraction = true; updateLens(); undo(); setTool('box');
      Bridge.fire('log', { msg: 'PERF ' + JSON.stringify(out) });
      return out;
    },
    hoverAt(p) { const s = toScreen(p); return Bridge.call('pick', { x: p[0], y: p[1] }).then(r => { S.mouse.in = true; showHover(r && r.hit, s[0], s[1]); return r; }); },
    marks(fr) { // draw one of each mark at fractions of the capture, through the real pointer handlers
      const W = S.iw, H = S.ih, P = (fx, fy) => [W * fx, H * fy];
      this.drag('box', P(.10, .25), P(.30, .55)); this.drag('box', P(.55, .15), P(.70, .35));
      this.drag('ellipse', P(.40, .62), P(.55, .82)); this.drag('cloud', P(.72, .55), P(.92, .82));
      setColour(3); this.drag('arrow', P(.50, .50), P(.38, .40));
      setColour(0); this.drag('measure', P(.12, .9), P(.45, .9));
      setTool('pin'); const q = toScreen(P(.8, .3)); this.pointer('pointerdown', q[0], q[1]); this.pointer('pointerup', q[0], q[1]);
      setTool('text'); beginText(null, P(.6, .45)); $('#text-editor').value = 'raise this'; commitText();
      S.strokes[0].comment = 'test comment on box 1'; renderMarks(); setTool('box');
      return S.strokes.length;
    },
    state() { return { mode: S.mode, tool: S.tool, colour: COLOURS[S.colour].name, width: S.width, marks: S.strokes.map(st => ({ kind: st.kind, label: st.label, comment: st.comment, points: st.points.length })), zoom: S.view.zoom, safe: S.safe, presets: S.presets, task: S.task, queue: S.queue.length, history: S.history.length }; },
  };

  boot().catch(e => { report(e, 'boot'); document.body.classList.remove('boot'); toast('ClaudeMark could not start: ' + e.message, 'warn', 20000); });
})();
