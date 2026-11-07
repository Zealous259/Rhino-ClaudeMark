/* A stand-in for claude_mark.py, so the UI can be developed and tested in a plain browser.
   Loaded only when the page URL has ?mock=1. Scenario scripts drive the real handlers through window.CMTest. */
'use strict';
window.MockBridge = (function () {
  const params = new URLSearchParams(location.search);
  const scenario = params.get('scenario') || '';
  const mode = params.get('mode') || 'capture';
  const log = [];
  let bootData = null;
  window.__mock = { log, sent: null };

  async function getBoot() {
    if (bootData) return bootData;
    const r = await fetch('../mock/mock_boot.json?v=' + Date.now());
    bootData = await r.json();
    bootData.mode = mode;
    if (mode === 'reply') bootData.reply = bootData.sample_reply;
    if (mode === 'options' || mode === 'style') { bootData.reply = bootData.sample_options; if (mode === 'style') bootData.reply.mode = 'style'; }
    if (params.get('theme')) bootData.settings.appearance = params.get('theme');
    if (params.get('nopending')) bootData.pending_reply = null;
    if (params.get('nodrafts')) bootData.drafts = [];
    if (params.get('queue') === '0') bootData.queue = [];
    if (params.get('marks')) bootData.settings.marks_open = true;
    if (params.get('sidebar')) bootData.settings.sidebar_open = true;
    if (params.get('task')) { bootData.settings.keep_task = true; bootData.settings.task = params.get('task'); }
    return bootData;
  }

  function hitFor(x, y) {
    // a boot file can carry real picks: mock_hits = [{r: [x0, y0, x1, y1] in image px, id, type, name, layer}], most specific first
    const hits = bootData && bootData.mock_hits;
    if (hits) {
      for (const h of hits) if (x >= h.r[0] && x <= h.r[2] && y >= h.r[1] && y <= h.r[3]) return { id: h.id, type: h.type, name: h.name, layer: h.layer, point: h.point || [x * 10, y * 10, 0] };
      return null;
    }
    const layers = ['MODEL::FACADE', 'MODEL::ROOF', 'MODEL::STRUCTURE', 'SITE::PAVING'];
    const names = ['Facade panel 4', 'Roof slab', '', 'Paving slab 14'];
    const k = Math.abs(Math.floor(x / 300) + Math.floor(y / 200)) % 4;
    return { id: '6f1c2a9e-4d2b-4b8e-9a51-' + String(1000 + k), type: k === 2 ? 'Extrusion' : 'Brep', name: names[k], layer: layers[k], point: [x * 10, y * 10, 3200] };
  }
  function measureFor(a, b) {
    // mock_measures = [{a: [x, y], b: [x, y], value, units, text}] in image px: the nearest within 60 px wins (either way round)
    const near = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]) < 60;
    for (const m of (bootData && bootData.mock_measures) || []) if ((near(a, m.a) && near(b, m.b)) || (near(a, m.b) && near(b, m.a))) return { value: m.value, units: m.units || 'mm', text: m.text, method: 'surface' };
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]) * 7.3;
    return { value: d, units: 'mm', text: d >= 1000 ? (d / 1000).toFixed(2) + ' m' : Math.round(d) + ' mm', method: 'surface' };
  }

  async function handle(msg) {
    const p = msg.payload || {};
    switch (msg.type) {
      case 'boot': return getBoot();
      case 'pick': await sleep(40); return { hit: hitFor(p.x, p.y), ms: 12 };
      case 'measure': { await sleep(120); return measureFor(p.a, p.b); }
      case 'paste': case 'browse_image': case 'ref': {
        await sleep(150); const n = p.n || 1;
        return { refs: [{ label: 'R' + n, url: '../mock/ref.jpg', file: 'ref' + n + '.png', source: 'C:\\Users\\you\\Pictures\\precedent.png', at: p.at || [600, 300] }] };
      }
      case 'preset_browse': case 'preset_add': {
        const b = await getBoot(); const cat = p.task || 'materials'; const id = cat + '/your-photo-' + Date.now() + '.json';
        b.presets[cat] = (b.presets[cat] || []).concat([{ id, name: 'Your photo', summary: 'Match this photo\'s material', instructions: 'Make a V-Ray material that matches this photo', thumb_url: '../mock/ref.jpg', builtin: false, category: cat, files: ['C:\\Users\\you\\Pictures\\your-photo.png'], kind: p.library ? 'library' : undefined }]);
        return { presets: b.presets, id, category: cat, name: 'Your photo' };
      }
      case 'preset_remove': { const b = await getBoot(); for (const k in b.presets) b.presets[k] = b.presets[k].filter(x => x.id !== p.id); return { presets: b.presets }; }
      case 'library': { const r = await fetch('../mock/library.json?v=' + Date.now()); const lib = await r.json(); window.__mockThumbs = lib.thumbs; await sleep(120); return { items: p.kind === 'materials' ? lib.items : [], root: lib.root }; }
      case 'thumbs': { await sleep(60); const urls = {}; for (const id of p.ids || []) if (window.__mockThumbs && window.__mockThumbs[id]) urls[id] = window.__mockThumbs[id]; return { urls }; }
      case 'preset_save': {
        const b = await getBoot(); const id = p.task + '/' + p.name.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.json';
        b.presets[p.task] = (b.presets[p.task] || []).filter(x => x.id !== id).concat([{ id, name: p.name, summary: p.summary, kind: p.kind, options: p.options, category: p.task, builtin: false }]);
        return { presets: b.presets, id };
      }
      case 'preset_update': { const b = await getBoot(); for (const k in b.presets) for (const x of b.presets[k]) if (x.id === p.id) { if (p.name) x.name = p.name; if (p.options) x.options = p.options; if (p.summary != null) x.summary = p.summary; } return { presets: b.presets }; }
      case 'choose_folder': await sleep(80); return { path: 'C:\\Users\\you\\OneDrive\\Desktop\\Renders for the board' };
      case 'terminals': { const b = await getBoot(); return { terminals: b.terminals, target: b.target }; }
      case 'save_png': return { path: 'C:\\Users\\you\\Pictures\\ClaudeMark\\ClaudeMark 2026-11-05.png', name: 'ClaudeMark 2026-11-05.png' };
      case 'send': window.__mock.sent = p; document.title = 'SENT ' + p.mode; return { ok: true };
      default: return {};
    }
  }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function receive(text, cb) {
    if (text.startsWith('BLOB:')) { log.push('blob ' + text.length); window.__mock.blobLen = text.length; return; }
    if (text.startsWith('DRAFT:')) { window.__mock.draft = text.length; return; }
    const msg = JSON.parse(text); log.push(msg.type);
    if (msg.type === 'ready') { setTimeout(runScenario, 250); }
    if (msg.id == null) return;
    handle(msg).then(result => cb({ id: msg.id, ok: true, result })).catch(e => cb({ id: msg.id, ok: false, error: String(e) }));
  }

  // ---------------------------------------------------------------- scenarios for screenshots and checks
  const W = () => window.CMTest;
  const scenarios = {
    marks() {
      const t = W(); const S = t.S;
      t.drag('box', [180, 250], [420, 470]);
      t.drag('box', [760, 150], [960, 300]);
      t.drag('box', [1100, 520], [1300, 690]);
      t.drag('ellipse', [520, 560], [700, 700]);
      t.drag('cloud', [1020, 110], [1290, 300]);
      t.setColour(3); t.drag('arrow', [700, 420], [560, 330]);
      t.setColour(1); t.drag('marker', [200, 760], [600, 760], 12);
      t.setColour(0); const s = S.strokes; s[0].comment = 'close this gap'; s[2].comment = 'paving runs into the wall';
      t.drag('measure', [180, 520], [700, 520]);
      t.setTool('pin'); const P = t.toScreen([880, 520]); t.pointer('pointerdown', P[0], P[1]); t.pointer('pointerup', P[0], P[1]);
      t.renderMarks(); t.setTool('box');
    },
    select() { scenarios.marks(); const t = W(); t.setTool('select'); t.select(t.S.strokes[1]); },
    comment() { scenarios.marks(); const t = W(); t.setTool('select'); t.select(t.S.strokes[1]); t.openComment(t.S.strokes[1]); },
    text() { scenarios.marks(); const t = W(); t.setTool('text'); t.beginText(null, [300, 120]); document.getElementById('text-editor').value = 'raise this parapet\nto match the north side'; document.getElementById('text-editor').dispatchEvent(new Event('input')); },
    textdone() { scenarios.text(); W().commitText(); },
    panels() { scenarios.marks(); W().togglePanel('marks', true); W().togglePanel('sidebar', true); W().setTask('materials'); W().togglePreset(W().S.allPresets.materials[1].id); },
    settings() { scenarios.marks(); W().settingsSheet(); },
    help() { W().helpSheet(); },
    target() { scenarios.marks(); W().targetMenu(document.getElementById('target-btn')); },
    attach() { W().attachMenu(document.getElementById('attach-btn')); },
    ctxmenu() { scenarios.marks(); const t = W(); const P = t.toScreen([880, 520]); t.markMenu(t.S.strokes[t.S.strokes.length - 1], P[0] + 20, P[1]); },
    zoom() { scenarios.marks(); const t = W(); const P = t.toScreen([300, 360]); t.zoomAt(2.6, P[0], P[1]); },
    hover() { scenarios.marks(); const t = W(); const P = t.toScreen([1180, 420]); t.showHover({ type: 'Brep', name: 'Parapet cap', layer: 'SCENE 2::ROOF' }, P[0], P[1]); },
    discard() { scenarios.marks(); W().cancel(); },
    ref() { const t = W(); t.gotRef({ refs: [{ label: 'R1', url: '../mock/ref.jpg', file: 'ref1.png', source: 'x', at: [1000, 300] }] }); scenarios.marks(); },
    photo() { const t = W(); t.gotRef({ refs: [{ label: 'R1', url: '../mock/ref.jpg', file: 'ref1.png', source: 'x', at: [1000, 300] }] }); setTimeout(() => { t.openPhoto('R1'); t.addMatch([300, 200]); t.addMatch([900, 400]); }, 300); },
    narrow() { scenarios.marks(); },
    send() { scenarios.marks(); setTimeout(() => W().send('send'), 200); },
    erase() { scenarios.marks(); const t = W(); t.setWidth(2); t.drag('eraser', [100, 760], [400, 760], 10); t.setTool('box'); },
  };
  function runScenario() {
    try { if (scenarios[scenario]) scenarios[scenario](); }
    catch (e) { console.error(e); document.title = 'SCENARIO ERROR ' + e.message; window.__mock.error = String(e.stack || e); }
    if (params.get('debug')) setTimeout(() => {
      const d = document.createElement('pre');
      d.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:999;background:rgba(0,0,0,.8);color:#0f0;font:11px Consolas;padding:6px;max-width:700px;white-space:pre-wrap;pointer-events:none';
      const cp = document.getElementById('composer').getBoundingClientRect();
      const st = W().state();
      d.textContent = JSON.stringify({ safe: st.safe, zoom: st.zoom, T: W().T(), composer: [cp.top, cp.height], marks: st.marks.length, err: window.__mock.error || null, log: log.slice(-12) });
      document.body.appendChild(d);
      const sv = window.__mock.sent; if (sv) { const e2 = document.createElement('pre'); e2.style.cssText = d.style.cssText.replace('bottom:8px', 'top:8px'); e2.textContent = 'SENT ' + JSON.stringify(sv).slice(0, 900); document.body.appendChild(e2); }
    }, 900);
  }
  return { receive };
})();
