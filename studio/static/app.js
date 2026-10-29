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
  if (button.dataset.tab === 'history') { loadHistory(); loadRights(); loadGoogle(); }
  if (button.dataset.tab === 'claude') loadClaude();
}));

// ---------- adding sources ----------
async function addSources(kind, entries) {
  const s = state[kind];
  const limit = kind === '2d' ? 2 : 1;
  if (kind === '3d') { s.sources = []; s.marking = null; }
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
    if (files.length) addSources(kind, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f), filename: f.name }))));
    else if (/^https?:\/\//.test(url)) addSources(kind, [{ url: url.trim() }]);
  });
  input.addEventListener('change', async () => {
    const files = [...input.files];
    input.value = '';
    if (files.length) addSources(kind, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f), filename: f.name }))));
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
  if (files.length) addSources(active, await Promise.all(files.map(async f => ({ image_base64: await fileToBase64(f), filename: f.name }))));
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
      s.marking = null;
      if (kind === '2d') { s.manual = false; autoBoxes(); }
      renderSources(kind);
    } }, 'Bỏ ảnh');
    const meta = el('div', { class: 'meta' },
      el('span', {}, `${src.width} × ${src.height} px`, src.url ? el('span', { class: 'muted' }, ' · từ link') : null),
      remove);
    const parts = [stage];
    if (kind === '3d' && s.marking) {
      markingOverlay(stage);
      parts.push(markingTools());
    }
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
    $('#run-3d').disabled = !s.sources.length || !$('#template').value;
    $('#mark-start').disabled = !s.sources.length;
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

// ---------- 3D: styles, templates, detection ----------
async function loadTemplates() {
  try {
    const [status, library] = await Promise.all([api('/api/status'), api('/api/v2/styles')]);
    state.templates = status.templates || [];
    state.styles = library.styles;
    state.defaultQuads = library.default_quads;
    state.unsorted = library.unsorted;
    $('#style').replaceChildren(...state.styles.map(st => el('option', { value: st.id },
      `${st.label} · ${st.panels.length} mảnh · ${st.templates.length} khuôn`)));
    fillTemplates();
    renderLibrary();
  } catch (error) {
    note('3d', 'Không tải được thư viện kiểu dáng: ' + error.message, true);
  }
}

function currentStyle() { return (state.styles || []).find(st => st.id === $('#style').value); }

function fillTemplates(selected) {
  const st = currentStyle();
  const list = st ? st.templates : [];
  $('#template').replaceChildren(...(list.length ? list.map(t => el('option', { value: t.name, disabled: !t.ready },
    `${t.label}${t.ready ? '' : ' (thiếu dữ liệu)'}${t.production_ready === false ? ' · nháp' : ''}`))
    : [el('option', { value: '' }, 'Chưa có khuôn: hãy tạo khuôn mới từ ảnh')]));
  if (selected) $('#template').value = selected;
  renderPanels();
  $('#run-3d').disabled = !state['3d'].sources.length || !$('#template').value;
}

const AI_PANELS = ['Front', 'Back', 'Sleeve', 'Hood', 'Left', 'Right', 'FrontLeft', 'FrontRight'];

function aiMode() { return $('input[name=aimode]:checked').value; }

function renderPanels() {
  const tpl = state.templates.find(t => t.name === $('#template').value);
  const sheet = aiMode() === 'sheet';
  const auto = sheet && $('#ai-auto').checked;
  $('#ai-auto').closest('label').hidden = !sheet;
  $('#panel-list').hidden = auto;
  $('#panel-list').replaceChildren(...(tpl ? tpl.panels : []).filter(p => AI_PANELS.includes(p.name)).map(p =>
    el('label', {}, el('input', { type: 'checkbox', value: p.name, checked: p.name === 'Front', onchange: estimateAI }), ` ${p.name}`)));
  estimateAI();
}

function aiPanels() {
  return aiMode() === 'sheet' && $('#ai-auto').checked ? 'auto' : $$('#panel-list input:checked').map(i => i.value);
}

let estimateTimer = null;
function estimateAI() {
  clearTimeout(estimateTimer);
  const out = $('#ai-estimate');
  if ($('input[name=mode3d]:checked').value !== 'ai' || !state['3d'].sources.length || !$('#template').value) { out.textContent = ''; return; }
  estimateTimer = setTimeout(async () => {
    try {
      const r = await api('/api/v2/ai-estimate', { template: $('#template').value, ai_mode: aiMode(), panels: aiPanels(),
        source_id: state['3d'].sources[0].source_id });
      state.aiEstimate = r;
      const b = r.budget;
      out.replaceChildren(
        el('b', {}, r.cached ? 'Đã có kết quả cho ảnh này: 0 lệnh, $0' : `Ước tính: ${r.calls} lệnh ≈ $${r.usd.toFixed(2)}`),
        el('br'), el('span', { class: 'muted' }, `Đã dùng hôm nay ~$${b.spent_today_usd.toFixed(2)} / trần $${b.daily_usd.toFixed(2)} · tháng ~$${b.spent_month_usd.toFixed(2)} / $${b.monthly_usd.toFixed(2)} `),
        el('button', { type: 'button', class: 'linkish', onclick: editBudget }, 'Sửa trần'),
        r.key_configured ? null : el('div', { class: 'bad' }, 'Chưa có khoá OpenAI: thêm ở Công cụ duyệt chi tiết → Cấu hình.'));
    } catch (error) {
      out.textContent = error.message;
    }
  }, 250);
}

async function editBudget() {
  const b = state.aiEstimate ? state.aiEstimate.budget : await api('/api/v2/ai-budget');
  const daily = prompt('Trần chi tiêu mỗi ngày (USD):', b.daily_usd);
  if (daily === null) return;
  const monthly = prompt('Trần chi tiêu mỗi tháng (USD):', b.monthly_usd);
  if (monthly === null) return;
  try {
    await api('/api/v2/ai-budget', { daily_usd: Number(daily), monthly_usd: Number(monthly) });
    estimateAI();
  } catch (error) { note('3d', error.message, true); }
}

$$('input[name=aimode]').forEach(r => r.addEventListener('change', renderPanels));
$('#ai-auto').addEventListener('change', renderPanels);

$('#style').addEventListener('change', () => { fillTemplates(); if (state['3d'].marking) startMarking(); });
$('#template').addEventListener('change', () => { renderPanels(); $('#run-3d').disabled = !state['3d'].sources.length || !$('#template').value; });
$$('input[name=mode3d]').forEach(r => r.addEventListener('change', () => {
  $('#panels-ai').hidden = $('input[name=mode3d]:checked').value !== 'ai';
  estimateAI();
}));

async function detectTemplate() {
  const src = state['3d'].sources[0];
  if (!src) return;
  const box = $('#detect-3d');
  box.replaceChildren(el('span', { class: 'muted' }, 'Đang nhận diện kiểu dáng…'));
  $('#mark-start').disabled = false;
  try {
    const r = await api('/api/v2/detect-style', { source_id: src.source_id });
    const styleId = r.suggested_style;
    if (styleId) { $('#style').value = styleId; fillTemplates(r.selected || undefined); }
    const kind = r.selected ? 'good' : styleId ? 'warn' : 'bad';
    box.replaceChildren(
      el('b', { class: kind }, r.selected ? 'Đã nhận ra khuôn' : styleId ? 'Gần giống, cần khuôn mới' : 'Chưa nhận ra'),
      el('p', {}, r.reason || ''),
      r.candidates.length ? el('p', { class: 'muted' }, 'Gần nhất: ' + r.candidates.slice(0, 3)
        .map(c => `${c.template} ${Math.round(c.score * 100)}%`).join(' · ')) : null);
    if (!r.selected && !$('#template').value) note('3d', 'Chọn kiểu dáng rồi bấm "Tạo khuôn mới từ ảnh này".');
    estimateAI();
  } catch (error) {
    box.replaceChildren(el('span', { class: 'bad' }, 'Không nhận diện được: ' + error.message));
  }
}

// ---------- 3D: marking a new template ----------
// Each panel is a quad (4 corners) on the mockup; the engine builds unwrap maps from it.
function startMarking() {
  const st = currentStyle();
  if (!st || !state['3d'].sources.length) return;
  const quads = {};
  for (const p of st.panels) if (p.kind === 'main' && state.defaultQuads[p.name]) quads[p.name] = state.defaultQuads[p.name].map(q => [...q]);
  state['3d'].marking = { style: st.id, quads, active: Object.keys(quads)[0] };
  note('3d', '');
  renderSources('3d');
}
$('#mark-start').addEventListener('click', startMarking);

function markingTools() {
  const m = state['3d'].marking;
  const st = state.styles.find(x => x.id === m.style);
  const label = el('input', { type: 'text', placeholder: 'Tên khuôn (không bắt buộc)', maxlength: '80', class: 'grow' });
  return el('div', { class: 'marktools' },
    el('p', { class: 'muted' }, `Kéo 4 góc của từng mảnh khớp với mảnh trên ảnh (${st.label}). Kéo bên trong để di chuyển cả mảnh. Mảnh không đánh dấu sẽ tô một màu lấy từ thân áo.`),
    el('div', { class: 'boxbar' }, st.panels.map(p => {
      const on = !!m.quads[p.name];
      return el('button', { type: 'button', class: `side${m.active === p.name ? ' active' : ''}${on ? '' : ' off'}`,
        title: on ? 'Chọn để chỉnh; bấm lại để bỏ' : 'Thêm mảnh này',
        onclick: () => {
          if (!on) { m.quads[p.name] = (state.defaultQuads[p.name] || [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]).map(q => [...q]); m.active = p.name; }
          else if (m.active === p.name) { delete m.quads[p.name]; m.active = Object.keys(m.quads)[0]; }
          else m.active = p.name;
          renderSources('3d');
        } }, `${on ? '' : '+ '}${p.name}`);
    })),
    el('div', { class: 'row' }, label,
      el('button', { type: 'button', onclick: () => { state['3d'].marking = null; renderSources('3d'); } }, 'Huỷ'),
      el('button', { type: 'button', class: 'accent', onclick: async event => {
        const names = Object.keys(m.quads);
        if (!names.length) { note('3d', 'Hãy đánh dấu ít nhất một mảnh.', true); return; }
        event.target.disabled = true;
        note('3d', 'Đang tạo khuôn và dựng bản đồ trải phẳng…');
        try {
          const r = await api('/api/v2/templates', { source_id: state['3d'].sources[0].source_id, style: m.style,
            label: label.value, panels: names.map(name => ({ name, quad: m.quads[name] })) });
          state['3d'].marking = null;
          await loadTemplates();
          $('#style').value = r.style;
          fillTemplates(r.name);
          renderSources('3d');
          note('3d', `Đã tạo khuôn ${r.label}. ${r.solid.length ? 'Tô một màu: ' + r.solid.join(', ') + '.' : ''}`);
          detectTemplate();
        } catch (error) {
          note('3d', error.message, true);
          event.target.disabled = false;
        }
      } }, 'Lưu khuôn')));
}

function markingOverlay(stage) {
  const m = state['3d'].marking;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 1 1');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.classList.add('quads');
  for (const [name, quad] of Object.entries(m.quads)) {
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', quad.map(p => p.join(',')).join(' '));
    poly.dataset.panel = name;
    if (name === m.active) poly.classList.add('active');
    svg.append(poly);
    const cx = quad.reduce((a, p) => a + p[0], 0) / 4, cy = quad.reduce((a, p) => a + p[1], 0) / 4;
    stage.append(el('span', { class: `qlabel${name === m.active ? ' active' : ''}`, style: `left:${cx * 100}%;top:${cy * 100}%` }, name));
    if (name === m.active) quad.forEach((p, i) => stage.append(el('i', { class: 'qhandle', 'data-panel': name, 'data-corner': String(i),
      style: `left:${p[0] * 100}%;top:${p[1] * 100}%` })));
  }
  stage.querySelector('img').after(svg);
  stage.classList.add('marking');
  let drag = null;
  const point = event => {
    const r = stage.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (event.clientY - r.top) / r.height))];
  };
  stage.addEventListener('dragstart', event => event.preventDefault());
  stage.addEventListener('pointerdown', event => {
    const t = event.target;
    const name = t.dataset.panel;
    if (!name) return;
    event.preventDefault();
    m.active = name;
    drag = { name, corner: t.dataset.corner !== undefined ? Number(t.dataset.corner) : null, start: point(event),
             quad: m.quads[name].map(p => [...p]), node: t };
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener('pointermove', event => {
    if (!drag) return;
    const [x, y] = point(event);
    const q = m.quads[drag.name];
    if (drag.corner !== null) { q[drag.corner] = [x, y]; }
    else {
      const dx = x - drag.start[0], dy = y - drag.start[1];
      const xs = drag.quad.map(p => p[0]), ys = drag.quad.map(p => p[1]);
      const ddx = Math.min(1 - Math.max(...xs), Math.max(-Math.min(...xs), dx));
      const ddy = Math.min(1 - Math.max(...ys), Math.max(-Math.min(...ys), dy));
      drag.quad.forEach((p, i) => { q[i] = [p[0] + ddx, p[1] + ddy]; });
    }
    const poly = svg.querySelector(`polygon[data-panel="${drag.name}"]`);
    poly.setAttribute('points', q.map(p => p.join(',')).join(' '));
    $$(`.qhandle[data-panel="${drag.name}"]`, stage).forEach((h, i) => Object.assign(h.style, { left: `${q[i][0] * 100}%`, top: `${q[i][1] * 100}%` }));
  });
  const finish = () => {
    if (!drag) return;
    drag = null;
    for (const q of Object.values(m.quads)) q.forEach(p => { p[0] = Math.round(p[0] * 10000) / 10000; p[1] = Math.round(p[1] * 10000) / 10000; });
    renderSources('3d');
  };
  stage.addEventListener('pointerup', finish);
  stage.addEventListener('pointercancel', finish);
}

// ---------- library tab ----------
function renderLibrary() {
  const host = $('#library');
  if (!host || !state.styles) return;
  const card = t => el('figure', { class: 'tpl' },
    t.preview_url ? el('img', { src: t.preview_url, alt: t.label, loading: 'lazy' }) : el('div', { class: 'noimg' }, 'Chưa có ảnh'),
    el('figcaption', {}, el('b', {}, t.label), el('span', { class: 'muted' },
      ` ${t.name}${t.ready ? '' : ' · thiếu dữ liệu'}${t.production_ready === false ? ' · nháp' : ''}`)));
  host.replaceChildren(...state.styles.map(st => el('section', { class: 'style' },
    el('h3', {}, st.label, el('span', { class: 'muted' }, st.sample_files ? ' · cỡ mảnh theo file mẫu xưởng' : ' · cỡ mảnh chuẩn 4500 × 5400')),
    el('p', { class: 'muted' }, st.panels.map(p => `${p.name} ${p.size[0]}×${p.size[1]}${p.kind === 'trim' ? ' (viền)' : ''}`).join(' · ')),
    st.templates.length ? el('div', { class: 'tpls' }, st.templates.map(card)) : el('p', { class: 'muted' }, 'Chưa có khuôn nào.'))),
    ...(state.unsorted.length ? [el('section', { class: 'style' }, el('h3', {}, 'Chưa xếp kiểu'), el('div', { class: 'tpls' }, state.unsorted.map(card)))] : []));
}

$('#run-3d').addEventListener('click', async () => {
  const ai = $('input[name=mode3d]:checked').value === 'ai';
  const panels = aiPanels();
  if (ai) {
    const e = state.aiEstimate;
    const what = panels === 'auto' ? 'các mảnh Local thấy ít' : `${panels.length} mảnh`;
    const cost = e ? (e.cached ? 'đã có kết quả, không tốn phí' : `${e.calls} lệnh ≈ $${e.usd.toFixed(2)}`) : 'có phí';
    if (!confirm(`Gửi ${what} lên OpenAI (${cost}). Tiếp tục?`)) return;
  }
  try {
    $('#run-3d').disabled = true;
    const job = await api('/api/v2/jobs', {
      kind: '3d', template: $('#template').value, openai: ai, panels: ai ? panels : [], ai_mode: aiMode(),
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

function rightsInfo(job) {
  if (job.rights === 'unknown' && !job.rights_reason) return null;
  const ev = job.rights_evidence || {};
  return el('div', { class: `rights ${job.rights}` },
    el('span', {}, job.rights_reason || ''),
    ...(ev.links || []).map(l => el('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer' }, l.label)));
}

function driveInfo(job) {
  if (job.drive_url) return el('a', { class: 'button', href: job.drive_url, target: '_blank', rel: 'noopener noreferrer' }, 'Mở trên Drive');
  if (!job.sync) return null;
  const text = { queued: 'Đang chờ đồng bộ Drive…', retry: 'Lỗi mạng, sẽ thử đồng bộ lại', error: 'Đồng bộ Drive lỗi' }[job.sync.state] || '';
  return el('div', { class: job.sync.state === 'error' ? 'warn' : 'muted', title: job.sync.error || '' },
    text + (job.sync.error && job.sync.state === 'error' ? `: ${job.sync.error}` : ''));
}

function renderJob(job) {
  const status = el('span', { class: `badge ${job.status === 'failed' ? 'red' : job.status === 'approved' ? 'green' : ''}` }, STATUS_LABEL[job.status] || job.status);
  const rightsBadge = job.rights !== 'unknown' ? el('span', { class: `badge ${job.rights}` }, RIGHTS_LABEL[job.rights]) : null;
  const head = el('div', { class: 'row' }, el('b', {}, job.sku), el('span', {}, rightsBadge, ' ', status));
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
          rightsInfo(job), driveInfo(job),
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
      rightsInfo(job), driveInfo(job),
      zip ? el('a', { class: 'button', href: zip.url }, 'Tải bộ ZIP') : null,
      el('a', { class: 'button', href: '/legacy' }, 'Duyệt chữ/logo, xuất PSD'),
      el('div', { class: 'actions' }, rightsControl(job), approve))));
  }
  return cards.length ? cards : [el('div', { class: 'card', 'data-job': job.id }, el('div', { class: 'body' }, head))];
}

// ---------- copyright keywords ----------
async function loadRights() {
  try {
    const r = await api('/api/v2/rights-config');
    $('#rights-keywords').value = r.keywords.join('\n');
    $('#rights-info').textContent = `${r.keywords.length} từ khoá · ${r.blacklist_count} art trong danh sách đen · ` +
      (r.ocr ? 'đọc chữ trên art: bật (Tesseract)' : 'đọc chữ trên art: tắt (cài Tesseract: brew install tesseract)');
  } catch (error) { $('#rights-info').textContent = error.message; }
}
$('#rights-save').addEventListener('click', async () => {
  try {
    await api('/api/v2/rights-config', { keywords: $('#rights-keywords').value.split('\n') });
    await loadRights();
  } catch (error) { alert(error.message); }
});

// ---------- Google Drive + Sheet ----------
async function googleCall(path, body) {
  try { renderGoogle(await api(path, body)); } catch (error) { alert(error.message); }
}

function renderGoogle(g) {
  const queue = g.queue || {};
  const waiting = (queue.queued || 0) + (queue.retry || 0);
  $('#google-info').textContent = !g.client ? 'chưa cài' : g.needs_reconnect ? 'cần kết nối lại' :
    g.connected ? `đã kết nối${waiting ? ` · ${waiting} job đang chờ` : ''}${queue.error ? ` · ${queue.error} lỗi` : ''}` : 'chưa kết nối';
  const body = [];
  if (!g.client || !g.connected) {
    body.push(el('ol', { class: 'muted steps' },
      el('li', {}, 'Vào ', el('a', { href: 'https://console.cloud.google.com/apis/library/drive.googleapis.com', target: '_blank', rel: 'noopener noreferrer' }, 'Google Cloud'),
        ', tạo project, bật "Google Drive API" và "Google Sheets API".'),
      el('li', {}, 'Màn hình đồng ý OAuth: loại External, thêm email của bạn, rồi bấm "Publish app" (chỉ dùng quyền drive.file nên không cần Google duyệt; để chế độ Testing thì 7 ngày phải kết nối lại).'),
      el('li', {}, 'Credentials → Create credentials → OAuth client ID → loại "Desktop app" → tải file JSON.'),
      el('li', {}, 'Dán nội dung file JSON vào ô dưới, bấm Lưu, rồi bấm Kết nối Google.')));
    const box = el('textarea', { rows: 4, placeholder: '{"installed":{"client_id":"…apps.googleusercontent.com", …}}' });
    body.push(el('label', { class: 'block' }, g.client ? `Client đã lưu: ${g.client_id_hint}. Dán file khác để thay:` : 'File JSON client OAuth', box),
      el('div', { class: 'actions' },
        el('button', { onclick: () => googleCall('/api/v2/google/client', { json: box.value }) }, 'Lưu client'),
        g.client ? el('button', { class: 'primary', onclick: async () => {
          try { location.href = (await api('/api/v2/google/connect', {})).url; } catch (error) { alert(error.message); }
        } }, g.needs_reconnect ? 'Kết nối Google lại' : 'Kết nối Google') : null));
  } else {
    body.push(el('p', { class: 'muted' }, 'Bấm Duyệt một job là file và dòng Sheet tự lên Drive. Tool chỉ thấy các file do nó tạo.'),
      el('div', { class: 'actions' },
        g.root_url ? el('a', { class: 'button', href: g.root_url, target: '_blank', rel: 'noopener noreferrer' }, 'Thư mục POD Studio') : null,
        g.sheet_url ? el('a', { class: 'button', href: g.sheet_url, target: '_blank', rel: 'noopener noreferrer' }, 'Sheet POD Quản lý') : null,
        el('button', { onclick: () => googleCall('/api/v2/google/sync', {}) },
          `Đồng bộ lại${g.unsynced ? ` (${g.unsynced} job chưa lên Drive)` : ''}`),
        el('button', { onclick: () => confirm('Ngắt kết nối Google? File trên Drive vẫn giữ nguyên.') && googleCall('/api/v2/google/disconnect', {}) }, 'Ngắt kết nối')));
  }
  if (g.last_error) body.push(el('div', { class: 'warn' }, g.last_error));
  $('#google-body').replaceChildren(...body);
}

async function loadGoogle() {
  try { renderGoogle(await api('/api/v2/google')); } catch (error) { $('#google-info').textContent = error.message; }
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

// ---------- ask Claude ----------
const chat = { session: null, chat: null, busy: false, attachments: [] };
const MODE_NOTE = {
  read: 'Claude chỉ đọc file, không chạy lệnh.',
  image: 'Claude tự vẽ art (SVG xuất PNG đúng khổ in), tách art, đổi kích thước. Không gọi API, không chạy lệnh khác.',
  full: 'Claude làm được mọi thứ như trong Terminal trên Mac: chạy lệnh, sửa file, cài gói.',
};

async function loadClaude() {
  try {
    const status = await api('/api/claude/status');
    if (!status.available) $('#chat-info').textContent = status.message;
    $('#chat-send').disabled = !status.available;
    for (const option of $('#chat-mode').options) option.disabled = !status.modes.includes(option.value);
    if ($('#chat-mode').selectedOptions[0].disabled) $('#chat-mode').value = 'image';
  } catch (err) { $('#chat-info').textContent = err.message; }
  $('#chat-mode-note').textContent = MODE_NOTE[$('#chat-mode').value];
  $('#chat-prompt').focus();
}

function chatBubble(kind, text = '') {
  const bubble = el('div', { class: `msg ${kind}` }, text);
  $('#chat-log').append(bubble);
  bubble.scrollIntoView({ block: 'end' });
  return bubble;
}

function renderAttachments() {
  $('#chat-attachments').replaceChildren(...chat.attachments.map((item, i) =>
    el('div', { class: 'chat-thumb' }, el('img', { src: item.url, alt: item.name }),
       el('button', { type: 'button', 'aria-label': 'Bỏ ảnh', onclick: () => {
         URL.revokeObjectURL(item.url); chat.attachments.splice(i, 1); renderAttachments();
       } }, '✕'))));
}

function addAttachments(files) {
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue;
    if (chat.attachments.length >= 4) { $('#chat-mode-note').textContent = 'Tối đa 4 ảnh mỗi lần.'; break; }
    if (file.size > 15 * 1024 * 1024) { $('#chat-mode-note').textContent = `${file.name} lớn hơn 15 MB.`; continue; }
    chat.attachments.push({ file, name: file.name || 'anh-dan.png', url: URL.createObjectURL(file) });
  }
  renderAttachments();
}

async function sendChat(prompt) {
  chat.busy = true;
  $('#chat-send').disabled = true;
  const mode = $('#chat-mode').value;
  const sending = chat.attachments.splice(0);
  renderAttachments();
  const mine = chatBubble('user', prompt);
  if (sending.length) mine.append(el('div', { class: 'shots' }, sending.map(a => el('img', { src: a.url, alt: a.name }))));
  const bubble = chatBubble('claude');
  const tools = el('div', { class: 'tools' }, 'Claude đang đọc câu hỏi…');
  const body = el('span');
  const shots = el('div', { class: 'shots' });
  bubble.append(tools, body, shots);
  try {
    const attachments = await Promise.all(sending.map(async a => ({ name: a.name, data: await fileToBase64(a.file) })));
    const response = await fetch('/api/claude/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, mode, attachments, session: chat.session, chat: chat.chat }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Lỗi ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finished = false;
    const used = [];
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const line = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (!line.startsWith('data: ')) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === 'start') {
          chat.chat = event.chat;
        } else if (event.type === 'text') {
          body.textContent += event.text;
          tools.textContent = used.length ? `Đã dùng: ${used.join(' · ')}` : '';
        } else if (event.type === 'tool') {
          used.push(event.detail || event.name);
          tools.textContent = `Đã dùng: ${used.join(' · ')}`;
        } else if (event.type === 'file') {
          shots.append(event.image
            ? el('img', { src: event.url, alt: event.name, title: event.name, onclick: () => window.open(event.url, '_blank') })
            : el('a', { href: `${event.url}?download`, download: event.name }, `⬇ ${event.name}`));
        } else if (event.type === 'done') {
          finished = true;
          chat.session = event.session || chat.session;
          tools.textContent = [used.length ? `Đã dùng: ${used.join(' · ')}` : '', `${event.seconds}s`].filter(Boolean).join(' · ');
        } else if (event.type === 'error') {
          finished = true;
          chat.session = event.session || chat.session;
          throw new Error(event.error);
        }
        bubble.scrollIntoView({ block: 'end' });
      }
    }
    if (!finished) throw new Error('Mất kết nối trước khi Claude trả lời xong.');
  } catch (err) {
    bubble.classList.add('error');
    body.textContent += (body.textContent ? '\n\n' : '') + err.message;
  } finally {
    chat.busy = false;
    $('#chat-send').disabled = false;
  }
}

$('#chat-form').addEventListener('submit', event => {
  event.preventDefault();
  const prompt = $('#chat-prompt').value.trim();
  if (!prompt || chat.busy) return;
  $('#chat-prompt').value = '';
  sendChat(prompt);
});
$('#chat-prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('#chat-form').requestSubmit();
  }
});
$('#chat-prompt').addEventListener('paste', event => {
  const files = [...event.clipboardData.files];
  if (files.length) { event.preventDefault(); addAttachments(files); }
});
$('#chat-file').addEventListener('change', event => { addAttachments(event.target.files); event.target.value = ''; });
$('#chat-mode').addEventListener('change', () => { $('#chat-mode-note').textContent = MODE_NOTE[$('#chat-mode').value]; });
$('#chat-new').addEventListener('click', () => {
  chat.session = null;
  chat.chat = null;
  $('#chat-log').replaceChildren();
  $('#chat-prompt').focus();
});
