/* Built-in capture: observe text drawing, reconstruct lines by position, and
 * merge visible reader media. No upstream runtime, network interception, or
 * browser fingerprint changes. The Canvas observation idea is credited in
 * references/architecture.md. */
(() => {
  if (!location.pathname.startsWith('/web/reader/') || window.__wereadLibraryCapture) return;
  const surfaces = new Map();
  let paintedAt = 0;
  let overflow = false;
  const context = CanvasRenderingContext2D.prototype;
  const nativeText = context.fillText;
  const nativeClear = context.clearRect;

  context.fillText = function (text, x, y, ...rest) {
    const answer = Reflect.apply(nativeText, this, [text, x, y, ...rest]);
    text = String(text);
    if (!text || text.startsWith('abcdefghijklmn') || !Number.isFinite(x) || !Number.isFinite(y)) return answer;
    const fontSize = Number((this.font.match(/([\d.]+)px/) || [0, 16])[1]);
    if (!surfaces.has(this.canvas)) surfaces.set(this.canvas, new Map());
    const records = surfaces.get(this.canvas);
    if (records.size >= 200000) { overflow = true; return answer; }
    const key = [Math.round(x*10), Math.round(y*10), fontSize, text].join('|');
    const width = this.measureText(text).width;
    records.set(key, {text, x, y, size:fontSize, width});
    paintedAt = performance.now();
    return answer;
  };
  context.clearRect = function (x, y, width, height) {
    const answer = Reflect.apply(nativeClear, this, [x, y, width, height]);
    if (x <= 0 && y <= 0) surfaces.delete(this.canvas);
    paintedAt = performance.now();
    return answer;
  };

  const median = numbers => {
    const sorted = numbers.slice().sort((a,b) => a-b);
    return sorted.length ? sorted[Math.floor(sorted.length/2)] : 20;
  };

  function snapshot() {
    // Long chapters use several attached canvases with shared chapter coordinates.
    // Merge those surfaces; choosing only the largest one loses the chapter tail.
    const ranked = [...surfaces.entries()].map(([canvas, records]) => ({
      canvas, records:[...records.values()], weight:[...records.values()].reduce((n,r) => n+r.text.length,0)
    })).sort((a,b) => b.weight-a.weight);
    const selected = ranked.filter(s => s.canvas.isConnected && s.canvas.closest('.wr_canvasContainer, .passage-content'));
    if (!selected.length) return {markdown:'', complete:false, overflow};
    const unique = new Map();
    for (const surface of selected) for (const r of surface.records) {
      unique.set([Math.round(r.x*10),Math.round(r.y*10),r.size,r.text].join('|'),r);
    }
    const active = {records:[...unique.values()]};
    const bodySize = median(active.records.map(r=>r.size));
    const lines = [];
    for (const record of active.records.sort((a,b) => a.y-b.y || a.x-b.x)) {
      let line = lines.at(-1);
      if (!line || Math.abs(record.y-line.y) > Math.max(3,bodySize*0.25)) {
        line = {y:record.y, runs:[], size:record.size}; lines.push(line);
      }
      line.runs.push(record); line.size = Math.max(line.size, record.size);
    }
    const gaps = lines.slice(1).map((r,i)=>r.y-lines[i].y).filter(g=>g>bodySize*0.5);
    const normalGap = median(gaps);
    const maxRight = Math.max(...lines.map(l=>Math.max(...l.runs.map(r=>r.x+r.width))));
    for (const line of lines) {
      const runs = line.runs.sort((a,b)=>a.x-b.x);
      let value = '', end = null;
      for (const run of runs) {
        if (end !== null && run.x-end > bodySize*0.25 && /[A-Za-z0-9]$/.test(value) && /^[A-Za-z0-9]/.test(run.text)) value += ' ';
        value += run.text; end = run.x+run.width;
      }
      line.text = value; line.end = end;
      line.heading = line.size >= bodySize*1.2;
    }
    const events = lines.map(line=>({...line, kind:'text'}));
    const seen = new Set();
    for (const element of document.querySelectorAll('.passage-content img, .passage-content pre, .passage-content hr')) {
      const transform = getComputedStyle(element).transform;
      let y = Number.parseFloat(element.style.top) || 0;
      if (transform && transform !== 'none') {
        try { y += new DOMMatrixReadOnly(transform).m42; } catch (_) {}
      }
      let text = '';
      if (element.tagName === 'IMG') {
        const src = element.currentSrc || element.getAttribute('src') || '';
        if (src.startsWith('https://')) text = `![](${src})`;
      } else if (element.tagName === 'PRE') {
        text = element.textContent || '';
      } else text = '---';
      const key = [y,text].join('|');
      if (text && !seen.has(key)) { seen.add(key); events.push({kind:'media', y, text}); }
    }
    events.sort((a,b)=>a.y-b.y);
    let markdown = '', previous = null;
    for (const item of events) {
      if (item.kind === 'media') { markdown += '\n\n'+item.text+'\n\n'; previous = null; continue; }
      const paragraph = !previous || item.heading || previous.heading || item.y-previous.y > normalGap*1.3 || previous.end < maxRight*0.78;
      if (markdown) markdown += paragraph ? '\n\n' : '\n';
      markdown += (item.heading ? '## ' : '') + item.text;
      previous = item;
    }
    return {markdown:markdown.trim(), complete:performance.now()-paintedAt >= 900, overflow,
      diagnostics:{surfaces:selected.length, text_runs:active.records.length, lines:lines.length}};
  }
  window.__wereadLibraryCapture = {snapshot,
    diagnostics() { return [...surfaces.entries()].map(([canvas,records])=>({
      tag:canvas.parentElement?.className,connected:canvas.isConnected,
      bounds:{top:canvas.getBoundingClientRect().top,width:canvas.getBoundingClientRect().width,height:canvas.getBoundingClientRect().height},
      first:[...records.values()].slice(0,2),last:[...records.values()].slice(-2),runs:records.size
    })); },
    reset() { surfaces.clear(); paintedAt=performance.now(); overflow=false; }};
})();
