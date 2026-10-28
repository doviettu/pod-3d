'use strict';
// POD Studio v2 UI: 2D art extraction, 3D unwrap (via the existing engine), history, zoom viewer.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const SIDE_LABEL = { front: 'Trước', back: 'Sau' };
const RIGHTS_LABEL = { unknown: 'Chưa kiểm', green: 'Xanh', yellow: 'Vàng', red: 'Đỏ' };
const STATUS_LABEL = { queued: 'Đang chờ', running: 'Đang xử lý', review: 'Chờ duyệt', approved: 'Đã duyệt',
                       failed: 'Lỗi', interrupted: 'Bị gián đoạn' };

const state = {
  '2d': { sources: [], boxes: {}, manual: false, active: 'front' },
  '3d': { sources: [] },
  templates: [],
  watching: new Set(),
};

async function api(path, body) {
  const options = body === undefined ? {} :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Lỗi ${response.status}`);
  return data;
}

function note(kind, message, error = false) {
  const el = $(`#note-${kind}`);
  el.textContent = message || '';
  el.classList.toggle('error', error);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.onerror = () => reject(new Error('Không đọc được file.'));
    reader.readAsDataURL(file);
  });
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) node.append(child);
  return node;
}

// ---------- tabs ----------
$$('.tabs button').forEach(button => button.addEventListener('click', () => {
  $$('.tabs button').forEach(b => b.setAttribute('aria-selected', String(b === button)));
  $$('.tab').forEach(t => { t.hidden = t.id !== `tab-${button.dataset.tab}`; });
  if (button.dataset.tab === 'history') loadHistory();
}));

// ---------- adding sources ----------
async function addSources(kind, entries) {
  const s = state[kind];
  const limit = kind === '2d' ? 2 : 1;
  if (kind === '3d') s.sources = [];
  const room = limit - s.sources.length;
  if (room <= 0) { note(kind, 'Đã đủ số ảnh. Bấm "Bỏ" để thay ảnh.', true); return; }
  note(kind, 'Đang tải và nhận diện ảnh…');
  try {
    const all = [...s.sources.map(x => ({ source_id: x.source_id })), ...entries.slice(0, room)];
    const result = await api('/api/v2/inspect', { kind, sources: all });
    s.sources = result.sources;
    if (kind === '2d') { s.manual = false; autoBoxes(); }
    note(kind, result.duplicates.length ? `Ảnh này đã làm rồi: ${result.duplicates.join(', ')}` : '');
    renderSources(kind);
    if (kind === '3d') detectTemplate();
  } catch (error) {
    note(kind, error.message, true);
  }
}

$$('.drop').forEach(drop => {
  const kind = drop.dataset.for;
  const input = $('input[type=file]', drop);
  drop.addEventListener('click', event => { if (event.target === drop || event.target.tagName === 'P') input.click(); });
  drop.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') input.click(); });
  drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', async event => {
    event.preventDefault();
    drop.classList.remove('over');
    const files = [...event.dataTransfer.files].filter(f => f.type.startsWith('image/'));
    const url = event.dataTransfer.getData('text/uri-list') || event.dataTransfer.getData('text/plain');
    if (files.length) addSources(kind, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f) }))));
    else if (/^https?:\/\//.test(url)) addSources(kind, [{ url: url.trim() }]);
  });
  input.addEventListener('change', async () => {
    const files = [...input.files];
    input.value = '';
    if (files.length) addSources(kind, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f) }))));
  });
});

$$('.url-row').forEach(form => form.addEventListener('submit', event => {
  event.preventDefault();
  const input = $('input', form);
  addSources(form.dataset.for, [{ url: input.value.trim() }]);
  input.value = '';
}));

document.addEventListener('paste', async event => {
  const active = $('.tabs button[aria-selected="true"]').dataset.tab;
  if (!['2d', '3d'].includes(active) || event.target.matches('input, textarea')) return;
  const files = [...event.clipboardData.files].filter(f => f.type.startsWith('image/'));
  const text = event.clipboardData.getData('text');
  if (files.length) addSources(active, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f) }))));
  else if (/^https?:\/\//.test(text)) addSources(active, [{ url: text.trim() }]);
});

// ---------- 2D boxes ----------
function sidesChoice() { return $('input[name=sides]:checked').value; }

function autoBoxes() {
  // Same rule as the server (art2d.assign_sides) so the preview matches the result.
  const s = state['2d'];
  const regions = s.sources.flatMap((src, i) => src.detection.garments.filter(g => g.art).map(g => ({ source: i, box: g.art })));
  const sides = sidesChoice();
  s.boxes = {};
  if (!regions.length) return;
  if (regions.length >= 2 && (sides === 'auto' || sides === 'both')) { s.boxes.front = regions[0]; s.boxes.back = regions[1]; }
  else if (sides === 'back') s.boxes.back = regions[1] || regions[0];
  else s.boxes.front = regions[0];
}

$$('input[name=sides]').forEach(r => r.addEventListener('change', () => {
  if (!state['2d'].manual) autoBoxes();
  renderSources('2d');
}));

function renderSources(kind) {
  const s = state[kind];
  const host = $(`#sources-${kind}`);
  host.replaceChildren();
  s.sources.forEach((src, index) => {
    const img = el('img', { src: `/api/v2/sources/${src.source_id}`, alt: `Ảnh ${index + 1}`, draggable: 'false' });
    const stage = el('div', { class: 'stage', tabindex: kind === '2d' ? '0' : null }, img);
    const remove = el('button', { type: 'button', onclick: () => {
      s.sources.splice(index, 1);
      if (kind === '2d') { s.manual = false; autoBoxes(); }
      renderSources(kind);
    } }, 'Bỏ ảnh');
    const meta = el('div', { class: 'meta' },
      el('span', {}, `${src.width} × ${src.height} px`, src.url ? el('span', { class: 'muted' }, ' · từ link') : null),
      remove);
    const parts = [stage];
    if (kind === '2d') {
      parts.push(boxToolbar(index), boxInfo(src, index));
      for (const [side, spec] of Object.entries(s.boxes)) {
        if (spec && spec.source === index) stage.append(boxEl(side, spec.box, index));
      }
      enableBoxEditing(stage, index);
    }
    parts.push(meta);
    host.append(el('div', { class: 'source' }, ...parts));
  });
  if (kind === '2d') {
    // Re-rendering replaces the stage, so give keyboard focus back to the one being edited.
    if (s.focus !== undefined) $(`#sources-2d .source:nth-child(${s.focus + 1}) .stage`)?.focus({ preventScroll: true });
    const total = Object.values(s.boxes).filter(Boolean).length;
    $('#run-2d').disabled = !s.sources.length || !total;
    if (s.sources.length && !total) note('2d', 'Chưa tự tìm được vùng art. Bấm "+ Trước" hoặc kéo trên ảnh để chọn vùng.', true);
  } else {
    $('#run-3d').disabled = !s.sources.length;
  }
}

// ---------- 2D box editor ----------
document.addEventListener('pointerdown', event => {
  if (!event.target.closest('#sources-2d')) state['2d'].focus = undefined;
});
// Each side (front/back) has at most one box, on any source image. Drag inside a box to
// move it, drag a handle to resize, drag on empty image to draw the selected side again.
const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
const MIN_BOX = 0.03;

function boxToolbar(index) {
  const s = state['2d'];
  return el('div', { class: 'boxbar' },
    ['front', 'back'].map(side => {
      const spec = s.boxes[side];
      const here = spec && spec.source === index;
      return el('button', {
        type: 'button', class: `side ${side}${s.active === side ? ' active' : ''}`,
        'aria-pressed': s.active === side ? 'true' : 'false',
        title: here ? 'Chọn khung này để chỉnh' : 'Thêm khung vào ảnh này',
        onclick: () => {
          s.active = side;
          if (!here) setBox(side, index, spec ? spec.box : defaultBox(index));
          renderSources('2d');
        } }, here ? `Khung ${SIDE_LABEL[side]}` : `+ ${SIDE_LABEL[side]}`);
    }),
    el('button', { type: 'button', class: 'ghost', title: 'Bỏ các khung đã chỉnh, dùng vùng máy tự nhận',
      onclick: () => { s.manual = false; autoBoxes(); note('2d', ''); renderSources('2d'); } }, 'Tự nhận lại'));
}

function boxInfo(src, index) {
  const lines = Object.entries(state['2d'].boxes).filter(([, spec]) => spec && spec.source === index).map(([side, spec]) => {
    const w = Math.round(spec.box[2] * src.width), h = Math.round(spec.box[3] * src.height);
    const small = Math.max(w, h) < 500;
    return el('span', { class: small ? 'warn' : '' }, `${SIDE_LABEL[side]}: ${w} × ${h} px`,
      small ? ' (ảnh nhỏ, bản in sẽ kém nét; nên tìm ảnh lớn hơn)' : '');
  });
  return lines.length ? el('div', { class: 'boxinfo' }, lines) : el('div', { class: 'boxinfo muted' }, 'Kéo trên ảnh để vẽ khung art.');
}

function defaultBox(index) {
  // Centre of the garment found on this image, else the middle of the photo.
  const g = state['2d'].sources[index].detection.garments[0];
  const [gx, gy, gw, gh] = g ? g.garment : [0, 0, 1, 1];
  return clampBox([gx + gw * 0.25, gy + gh * 0.22, gw * 0.5, gh * 0.5]);
}

function clampBox([x, y, w, h]) {
  w = Math.min(1, Math.max(MIN_BOX, w));
  h = Math.min(1, Math.max(MIN_BOX, h));
  return [Math.min(1 - w, Math.max(0, x)), Math.min(1 - h, Math.max(0, y)), w, h].map(v => Math.round(v * 10000) / 10000);
}

function setBox(side, source, box) {
  const s = state['2d'];
  s.boxes[side] = { source, box: clampBox(box) };
  s.manual = true;
  note('2d', '');
}

function boxEl(side, box, index) {
  const node = el('div', { class: `box ${side}${state['2d'].active === side ? ' active' : ''}`, 'data-side': side },
    el('span', { class: 'label' }, SIDE_LABEL[side]),
    el('button', { type: 'button', class: 'del', title: `Xoá khung ${SIDE_LABEL[side]}`, 'aria-label': `Xoá khung ${SIDE_LABEL[side]}`,
      onpointerdown: event => event.stopPropagation(),
      onclick: () => { delete state['2d'].boxes[side]; state['2d'].manual = true; renderSources('2d'); } }, '×'),
    HANDLES.map(h => el('i', { class: `handle ${h}`, 'data-handle': h })));
  placeBox(node, box);
  return node;
}

function placeBox(node, [x, y, w, h]) {
  Object.assign(node.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
}

function enableBoxEditing(stage, index) {
  const s = state['2d'];
  let drag = null;
  const point = event => {
    const r = stage.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (event.clientY - r.top) / r.height))];
  };
  stage.addEventListener('dragstart', event => event.preventDefault());
  stage.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault();
    stage.focus({ preventScroll: true });
    s.focus = index;
    const boxNode = event.target.closest('.box');
    const p = point(event);
    if (boxNode) {
      const side = boxNode.dataset.side;
      s.active = side;
      $$('.box', stage).forEach(b => b.classList.toggle('active', b === boxNode));
      drag = { mode: event.target.dataset.handle || 'move', side, node: boxNode, start: p, box: [...s.boxes[side].box] };
    } else {
      // Draw the selected side on empty image; it replaces that side's box wherever it was.
      const node = stage.querySelector(`.box[data-side="${s.active}"]`) || boxEl(s.active, [...p, 0, 0], index);
      if (!node.isConnected) stage.append(node);
      drag = { mode: 'draw', side: s.active, node, start: p, box: null };
    }
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener('pointermove', event => {
    if (!drag) return;
    const [px, py] = point(event);
    const dx = px - drag.start[0], dy = py - drag.start[1];
    let box;
    if (drag.mode === 'draw') {
      box = [Math.min(px, drag.start[0]), Math.min(py, drag.start[1]), Math.abs(dx), Math.abs(dy)];
    } else if (drag.mode === 'move') {
      const [x, y, w, h] = drag.box;
      box = clampBox([x + dx, y + dy, w, h]);
    } else {
      let [x0, y0, x1, y1] = [drag.box[0], drag.box[1], drag.box[0] + drag.box[2], drag.box[1] + drag.box[3]];
      if (drag.mode.includes('w')) x0 = Math.min(x1 - MIN_BOX, Math.max(0, x0 + dx));
      if (drag.mode.includes('e')) x1 = Math.max(x0 + MIN_BOX, Math.min(1, x1 + dx));
      if (drag.mode.includes('n')) y0 = Math.min(y1 - MIN_BOX, Math.max(0, y0 + dy));
      if (drag.mode.includes('s')) y1 = Math.max(y0 + MIN_BOX, Math.min(1, y1 + dy));
      box = [x0, y0, x1 - x0, y1 - y0];
    }
    drag.current = box;
    placeBox(drag.node, box);
  });
  const finish = () => {
    if (!drag) return;
    const { side, current, mode } = drag;
    drag = null;
    if (current && (mode !== 'draw' || (current[2] > MIN_BOX && current[3] > MIN_BOX))) setBox(side, index, current);
    renderSources('2d');
  };
  stage.addEventListener('pointerup', finish);
  stage.addEventListener('pointercancel', finish);
  stage.addEventListener('focus', () => { s.focus = index; });
  stage.addEventListener('keydown', event => {
    const spec = s.boxes[s.active];
    if (!spec || spec.source !== index) return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      delete s.boxes[s.active]; s.manual = true; event.preventDefault(); renderSources('2d'); return;
    }
    const step = event.shiftKey ? 0.02 : 0.004;
    const move = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!move) return;
    event.preventDefault();
    const [x, y, w, h] = spec.box;
    setBox(s.active, index, event.altKey ? [x, y, w + move[0], h + move[1]] : [x + move[0], y + move[1], w, h]);
    renderSources('2d');
  });
}

// ---------- 2D run ----------
$('#run-2d').addEventListener('click', async () => {
  const s = state['2d'];
  const body = {
    kind: '2d', sides: sidesChoice(), background: $('input[name=background]:checked').value,
    sources: s.sources.map(src => ({ source_id: src.source_id })),
  };
  // The boxes on screen are what the user checked, so always send them.
  if (Object.values(s.boxes).some(Boolean)) body.boxes = s.boxes;
  try {
    $('#run-2d').disabled = true;
    note('2d', 'Đang xếp hàng…');
    const job = await api('/api/v2/jobs', body);
    note('2d', job.duplicate ? `Đã làm ảnh này với cùng tuỳ chọn (${job.sku}); hiển thị kết quả cũ.` : '');
    showJob(job, '2d');
  } catch (error) {
    note('2d', error.message, true);
  } finally {
    $('#run-2d').disabled = !s.sources.length;
  }
});

// ---------- 3D ----------
async function loadTemplates() {
  try {
    const status = await api('/api/status');
    state.templates = status.templates || [];
    const select = $('#template');
    select.replaceChildren(...state.templates.map(t => el('option', { value: t.name, disabled: !t.ready },
      `${t.label}${t.ready ? '' : ' (thiếu dữ liệu)'}${t.production_ready === false ? ' · nháp' : ''}`)));
    renderPanels();
  } catch (error) {
    note('3d', 'Không tải được danh sách kiểu dáng: ' + error.message, true);
  }
}

function renderPanels() {
  const tpl = state.templates.find(t => t.name === $('#template').value);
  const allowed = ['Front', 'Back', 'Sleeve'];
  $('#panel-list').replaceChildren(...(tpl ? tpl.panels : []).filter(p => allowed.includes(p.name)).map(p =>
    el('label', {}, el('input', { type: 'checkbox', value: p.name, checked: p.name === 'Front' }), ` ${p.name}`)));
}

$('#template').addEventListener('change', renderPanels);
$$('input[name=mode3d]').forEach(r => r.addEventListener('change', () => {
  $('#panels-ai').hidden = $('input[name=mode3d]:checked').value !== 'ai';
}));

async function detectTemplate() {
  const src = state['3d'].sources[0];
  if (!src) return;
  $('#detect-3d').textContent = 'Đang nhận diện kiểu dáng…';
  try {
    const blob = await (await fetch(`/api/v2/sources/${src.source_id}`)).blob();
    const result = await api('/api/detect-template', { image_base64: await fileToBase64(blob) });
    const pick = result.selected || result.suggested;
    if (pick) {
      $('#template').value = pick;
      renderPanels();
      $('#detect-3d').textContent = result.selected ? 'Đã nhận đúng kiểu dáng.' : 'Gợi ý kiểu dáng gần nhất, hãy kiểm tra lại.';
    } else {
      $('#detect-3d').textContent = 'Chưa nhận ra kiểu dáng: hãy chọn tay (hoặc chấm điểm mốc bằng `pod3d mark`).';
    }
  } catch (error) {
    $('#detect-3d').textContent = 'Không nhận diện được: ' + error.message;
  }
}

$('#run-3d').addEventListener('click', async () => {
  const ai = $('input[name=mode3d]:checked').value === 'ai';
  const panels = $$('#panel-list input:checked').map(i => i.value);
  if (ai && !confirm(`Gửi ${panels.length} mảnh lên OpenAI (có phí). Tiếp tục?`)) return;
  try {
    $('#run-3d').disabled = true;
    const job = await api('/api/v2/jobs', {
      kind: '3d', template: $('#template').value, openai: ai, panels: ai ? panels : [],
      use_model: $('#use-model').checked, sources: [{ source_id: state['3d'].sources[0].source_id }],
    });
    note('3d', '');
    showJob(job, '3d');
  } catch (error) {
    note('3d', error.message, true);
  } finally {
    $('#run-3d').disabled = !state['3d'].sources.length;
  }
});

// ---------- results ----------
function showJob(job, kind) {
  const host = $(`#results-${kind}`);
  let card = $(`[data-job="${job.id}"]`, host);
  const fresh = renderJob(job);
  if (card) card.replaceWith(...fresh); else host.prepend(...fresh);
  if (['queued', 'running'].includes(job.status)) watch(job.id, kind);
}

function watch(id, kind) {
  if (state.watching.has(id)) return;
  state.watching.add(id);
  const tick = async () => {
    try {
      const job = await api(`/api/v2/jobs/${id}`);
      $$(`[data-job="${id}"]`).forEach((n, i) => { if (i) n.remove(); });
      showJob(job, kind);
      if (['queued', 'running'].includes(job.status)) setTimeout(tick, 2000);
      else state.watching.delete(id);
    } catch { state.watching.delete(id); }
  };
  setTimeout(tick, 1500);
}

function rightsControl(job) {
  const select = el('select', { 'aria-label': 'Bản quyền', onchange: async () => {
    const reason = select.value === 'green' || select.value === 'red' ? prompt('Lý do (ví dụ: tự thiết kế / logo Disney):', '') : '';
    if (reason === null) { select.value = job.rights; return; }
    try { showJob(await api(`/api/v2/jobs/${job.id}/rights`, { rights: select.value, reason }), job.kind); }
    catch (error) { alert(error.message); select.value = job.rights; }
  } }, Object.entries(RIGHTS_LABEL).map(([v, label]) => el('option', { value: v, selected: v === job.rights }, `Bản quyền: ${label}`)));
  return select;
}

function renderJob(job) {
  const status = el('span', { class: `badge ${job.status === 'failed' ? 'red' : job.status === 'approved' ? 'green' : ''}` }, STATUS_LABEL[job.status] || job.status);
  const head = el('div', { class: 'row' }, el('b', {}, job.sku), status);
  if (['queued', 'running', 'failed', 'interrupted'].includes(job.status)) {
    const last = job.logs && job.logs.length ? job.logs[job.logs.length - 1].message : '';
    return [el('div', { class: 'card', 'data-job': job.id }, el('div', { class: 'body' }, head,
      job.error ? el('div', { class: 'warn' }, job.error) : el('div', { class: 'muted' }, last || 'Đang xử lý…')))];
  }
  const approve = job.status === 'review' ? el('button', { onclick: async () => {
    try { showJob(await api(`/api/v2/jobs/${job.id}/approve`, {}), job.kind); } catch (error) { alert(error.message); }
  } }, 'Duyệt') : null;
  const cards = [];
  if (job.kind === '2d') {
    for (const [side, out] of Object.entries(job.outputs.sides || {})) {
      const full = `/v2files/${job.id}/${out.file}`;
      cards.push(el('div', { class: 'card', 'data-job': job.id },
        el('div', { class: 'thumb checker', onclick: () => openViewer(full, `${job.sku} · ${SIDE_LABEL[side]}`) },
          el('img', { src: `/v2files/${job.id}/${out.preview}`, alt: SIDE_LABEL[side], loading: 'lazy' })),
        el('div', { class: 'body' }, head.cloneNode(true),
          el('div', { class: 'muted' }, `${SIDE_LABEL[side]} · ${out.size[0]} × ${out.size[1]} px · ${out.dpi} DPI`),
          ...(out.warnings || []).map(w => el('div', { class: 'warn' }, w)),
          el('div', { class: 'actions' },
            el('button', { onclick: () => openViewer(full, `${job.sku} · ${SIDE_LABEL[side]}`) }, 'Mở rộng'),
            el('a', { class: 'button', href: full + '?download=1' }, 'Tải PNG'),
            rightsControl(job), approve))));
    }
  } else {
    const images = (job.outputs.artifacts || []).filter(a => a.kind === 'image');
    for (const preview of job.outputs.previews || []) {
      const full = (images.find(a => a.name.startsWith(preview.panel + ' ')) || {}).url;
      cards.push(el('div', { class: 'card', 'data-job': job.id },
        el('div', { class: 'thumb', onclick: () => full && openViewer(full, `${job.sku} · ${preview.panel}`) },
          el('img', { src: preview.openai_url || preview.local_url, alt: preview.panel, loading: 'lazy' })),
        el('div', { class: 'body' }, head.cloneNode(true), el('div', { class: 'muted' }, preview.panel),
          el('div', { class: 'actions' },
            full ? el('button', { onclick: () => openViewer(full, `${job.sku} · ${preview.panel}`) }, 'Mở rộng') : null,
            full ? el('a', { class: 'button', href: full }, 'Tải PNG') : null))));
    }
    const zip = (job.outputs.artifacts || []).find(a => a.kind === 'zip');
    cards.push(el('div', { class: 'card', 'data-job': job.id }, el('div', { class: 'body' }, head.cloneNode(true),
      zip ? el('a', { class: 'button', href: zip.url }, 'Tải bộ ZIP') : null,
      el('a', { class: 'button', href: '/legacy' }, 'Duyệt chữ/logo, xuất PSD'),
      el('div', { class: 'actions' }, rightsControl(job), approve))));
  }
  return cards.length ? cards : [el('div', { class: 'card', 'data-job': job.id }, el('div', { class: 'body' }, head))];
}

// ---------- history ----------
async function loadHistory() {
  try {
    const jobs = await api('/api/v2/jobs');
    $('#jobs').replaceChildren(...jobs.map(job => el('tr', {},
      el('td', {}, job.sku), el('td', {}, `${job.kind.toUpperCase()} · ${job.mode === 'ai' ? 'AI' : 'Local'}${job.sides ? ' · ' + job.sides : ''}`),
      el('td', {}, STATUS_LABEL[job.status] || job.status),
      el('td', {}, el('span', { class: `badge ${job.rights}` }, RIGHTS_LABEL[job.rights])),
      el('td', {}, new Date(job.created_at).toLocaleString('vi-VN')),
      el('td', {}, el('button', { onclick: () => {
        $(`.tabs button[data-tab="${job.kind}"]`).click();
        showJob(job, job.kind);
      } }, 'Xem')))));
  } catch (error) {
    $('#jobs').replaceChildren(el('tr', {}, el('td', { colspan: 6 }, error.message)));
  }
}
$('#refresh').addEventListener('click', loadHistory);

// ---------- zoom viewer ----------
let osd = null;
function openViewer(url, title) {
  const dialog = $('#viewer');
  $('#viewer-title').textContent = title;
  $('#viewer-download').href = url + (url.includes('?') ? '&' : '?') + 'download=1';
  dialog.showModal();
  if (!osd) {
    osd = OpenSeadragon({
      id: 'osd', showNavigationControl: false, showNavigator: true, navigatorPosition: 'BOTTOM_RIGHT',
      maxZoomPixelRatio: 8, visibilityRatio: 0.2, gestureSettingsMouse: { clickToZoom: false },
      imageSmoothingEnabled: false,
    });
  }
  osd.open({ type: 'image', url, buildPyramid: true });
}
function zoomActual() {
  if (!osd || !osd.world.getItemAt(0)) return;
  osd.viewport.zoomTo(osd.world.getItemAt(0).imageToViewportZoom(1));
}
$$('[data-zoom]').forEach(b => b.addEventListener('click', () => b.dataset.zoom === 'fit' ? osd.viewport.goHome() : zoomActual()));
$('#viewer-close').addEventListener('click', () => $('#viewer').close());
const backgrounds = [['checker', 'ca rô'], ['white', 'trắng'], ['dark', 'tối']];
let bgIndex = 0;
$('#viewer-bg').addEventListener('click', () => {
  bgIndex = (bgIndex + 1) % backgrounds.length;
  $('#osd').className = backgrounds[bgIndex][0];
  $('#viewer-bg').textContent = `Nền: ${backgrounds[bgIndex][1]}`;
});
document.addEventListener('keydown', event => {
  if (!$('#viewer').open) return;
  if (event.key === '1') zoomActual();
  if (event.key === '0') osd.viewport.goHome();
});

loadTemplates();
