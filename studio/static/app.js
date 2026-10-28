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
    const img = el('img', { src: `/api/v2/sources/${src.source_id}`, alt: `Ảnh ${index + 1}` });
    const stage = el('div', { class: 'stage' }, img);
    const remove = el('button', { type: 'button', onclick: () => {
      s.sources.splice(index, 1);
      if (kind === '2d') { s.manual = false; autoBoxes(); }
      renderSources(kind);
    } }, 'Bỏ');
    const meta = el('div', { class: 'meta' },
      el('span', {}, `${src.width} × ${src.height} px`, src.url ? el('span', { class: 'muted' }, ' · từ link') : null),
      kind === '2d' ? el('div', { class: 'chips' },
        ['front', 'back'].map(side => el('button', {
          type: 'button', class: s.active === side ? 'active' : '',
          title: 'Kéo trên ảnh để vẽ lại khung',
          onclick: () => { s.active = side; renderSources(kind); } }, `Vẽ khung ${SIDE_LABEL[side]}`)),
        s.manual ? el('button', { type: 'button', onclick: () => { s.manual = false; autoBoxes(); renderSources(kind); } }, 'Tự nhận lại') : null,
      ) : null,
      remove);
    if (kind === '2d') {
      for (const [side, spec] of Object.entries(s.boxes)) {
        if (spec && spec.source === index) stage.append(boxEl(side, spec.box));
      }
      enableDrawing(stage, index);
    }
    host.append(el('div', { class: 'source' }, stage, meta));
  });
  if (kind === '2d') {
    const total = s.sources.reduce((n, src) => n + src.detection.garments.filter(g => g.art).length, 0);
    $('#run-2d').disabled = !s.sources.length;
    if (s.sources.length && !total && !s.manual) note('2d', 'Chưa tự tìm được vùng art. Hãy kéo trên ảnh để chọn vùng.', true);
  } else {
    $('#run-3d').disabled = !s.sources.length;
  }
}

function boxEl(side, [x, y, w, h]) {
  return el('div', { class: `box ${side}`, style: `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%` },
    el('span', {}, SIDE_LABEL[side]));
}

function enableDrawing(stage, index) {
  let start = null, ghost = null;
  const point = event => {
    const r = stage.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (event.clientY - r.top) / r.height))];
  };
  stage.addEventListener('pointerdown', event => {
    start = point(event);
    stage.setPointerCapture(event.pointerId);
    ghost = boxEl(state['2d'].active, [...start, 0, 0]);
    stage.append(ghost);
  });
  stage.addEventListener('pointermove', event => {
    if (!start) return;
    const [x, y] = point(event);
    const box = [Math.min(x, start[0]), Math.min(y, start[1]), Math.abs(x - start[0]), Math.abs(y - start[1])];
    Object.assign(ghost.style, { left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` });
    ghost.dataset.box = JSON.stringify(box);
  });
  stage.addEventListener('pointerup', () => {
    if (!start) return;
    const box = ghost.dataset.box ? JSON.parse(ghost.dataset.box) : null;
    start = null;
    if (box && box[2] > 0.02 && box[3] > 0.02) {
      const s = state['2d'];
      s.boxes[s.active] = { source: index, box };
      s.manual = true;
      note('2d', '');
    }
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
  if (s.manual) body.boxes = s.boxes;
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
