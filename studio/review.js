/* Manual review uses existing images and local tools only. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const builtins = [
    {id:'system-arial',name:'Arial',license_confirmed:true},
    {id:'system-times',name:'Times',license_confirmed:true}
  ];
  const drafts = new Map();
  let api, safeURL, node, readBase64, job, panels = [], current = null;
  let fonts = builtins, busy = false, sequence = 0, fieldID = 0, initialized = false;

  function message(text = '', error = false) {
    $('review-message').textContent = text;
    $('review-message').dataset.error = String(error);
  }
  function failure(error) {
    const text = error?.message || '';
    return /[À-ỹĐđ]/.test(text) && text.length <= 350 && !/traceback|exception|errno/i.test(text)
      ? text : 'Chưa hoàn tất thao tác. Kiểm tra máy chủ và công cụ local rồi thử lại.';
  }
  function image(img, url) {
    const href = safeURL(url);
    img.hidden = !href;
    if (href) img.src = href;
    else img.removeAttribute('src');
  }
  function download(parent, title, url) {
    const href = safeURL(url);
    if (!href) return;
    const link = node('a', title); link.href = href; link.download = '';
    parent.append(link);
  }
  function checkFile(file, kind, megabytes) {
    const valid = kind === 'font' ? /\.(ttf|otf)$/i.test(file?.name || '')
      : /\.png$/i.test(file?.name || '') && (!file.type || file.type === 'image/png');
    if (!valid || !file.size || file.size > megabytes * 1024 * 1024)
      throw new Error(kind === 'font' ? 'Chọn font TTF / OTF lớn hơn 0 và tối đa 5 MB.'
        : 'Chọn ảnh PNG lớn hơn 0 và tối đa ' + megabytes + ' MB.');
  }
  function number(value, name, min = 0, max = Infinity) {
    if (!Number.isInteger(value) || value < min || value > max)
      throw new Error(name + ' chưa hợp lệ. Nhập số trong kích thước mảnh.');
    return value;
  }
  function rectangle(box, state) {
    const x = number(box.x, 'X', 0, state.view.width);
    const y = number(box.y, 'Y', 0, state.view.height);
    const width = number(box.width, 'Chiều rộng', 1, state.view.width - x);
    const height = number(box.height, 'Chiều cao', 1, state.view.height - y);
    return [x,y,width,height];
  }
  function makeState(view, id, previous) {
    if (!view || !Number.isFinite(view.width) || !Number.isFinite(view.height) || view.width <= 0 || view.height <= 0)
      throw new Error('Chưa đọc được kích thước mảnh để duyệt.');
    const spec = view.spec || {};
    const openaiDefault = !view.revision && job?.id === id && ['artwork-first-v1','shared-artwork-v2'].includes(job.workflow) && safeURL(panels.find(item => item.panel === view.panel)?.openai_url);
    return {
      key:JSON.stringify([id,view.panel]), id, panel:view.panel, view,
      source:openaiDefault ? 'openai' : spec.source || 'local', approved:!!view.revision && !!spec.approved, dirty:false, clean:null, cleanAsset:spec.artwork_asset_id,
      text:(spec.text_layers || []).map(layer => ({...layer})),
      logos:(spec.logo_layers || []).map(layer => ({...layer})),
      erasures:(spec.erasures || []).map(layer => ({...layer})),
      region:previous?.region || {x:0,y:0,width:Math.min(256,view.width),height:Math.min(256,view.height)},
      vectors:previous?.vectors || []
    };
  }
  function dirty(state, approval = false) {
    state.dirty = true;
    if (!approval) state.approved = false;
    window.dispatchEvent(new CustomEvent('pod-review-changed', {detail:{jobId:state.id}}));
    if (current === state) {
      $('review-approved').checked = state.approved;
      $('review-proof-label').textContent = 'Bản đã lưu trước đó · Có thay đổi chưa lưu';
      $('review-downloads').replaceChildren();
      $('review-approval-note').textContent = 'Có thay đổi chưa lưu. Lưu và xem trước để kiểm tra.';
    }
  }
  function field(parent, label, type, value, change, attributes = {}) {
    const wrap = node('label', label), input = node('input');
    input.id = 'review-field-' + (++fieldID); input.type = type;
    input.value = value ?? ''; Object.assign(input, attributes);
    input.addEventListener('input', () => change(type === 'number' ? input.valueAsNumber : input.value));
    wrap.append(input); parent.append(wrap); return input;
  }
  function fontOptions(select, value) {
    select.replaceChildren();
    for (const font of fonts) select.append(new Option(font.name + (font.license_confirmed ? '' : ' · Chưa xác nhận phép'), font.id));
    if (value && !fonts.some(font => font.id === value)) select.append(new Option('Font không còn trong thư viện', value));
    select.value = value || fonts[0].id;
  }
  async function get_fonts() {
    const result = await api('/api/fonts');
    if (!Array.isArray(result)) throw new Error('Chưa đọc được thư viện font.');
    fonts = [...builtins.filter(font => !result.some(item => item.id === font.id)), ...result];
    for (const select of document.querySelectorAll('[data-review-font]')) fontOptions(select, select.value);
    const count = Math.min(3, fonts.length), box = $('review-sample-selects');
    const values = [...box.querySelectorAll('select')].map(select => select.value);
    box.replaceChildren();
    for (let i = 0; i < count; i++) {
      const label = node('label', 'Font mẫu ' + (i + 1)), select = node('select');
      select.dataset.reviewFont = ''; fontOptions(select, values[i] || fonts[i].id);
      label.append(select); box.append(label);
    }
  }
  function limits() {
    if (!current) return;
    $('review-add-text').disabled = current.text.length >= 4;
    $('review-add-logo').disabled = current.logos.length >= 4;
    $('review-segment').disabled = current.logos.length >= 4;
    $('review-repair').disabled = !['Front','Back','Sleeve','Hood','Left','Right'].includes(current.panel);
  }
  function layerBox(parent, label, list, layer, state) {
    const box = node('div', undefined, 'review-layer'), row = node('div', undefined, 'row');
    const remove = node('button', 'Bỏ ' + label.toLowerCase()); remove.type = 'button';
    remove.addEventListener('click', () => { list.splice(list.indexOf(layer), 1); dirty(state); renderLayers(); });
    row.append(node('strong', label), remove); box.append(row); parent.append(box); return box;
  }
  function coordinates(box, layer, keys, state, editsArtwork = true) {
    const fields = node('div', undefined, 'review-fields'); box.append(fields);
    const labels = {x:'X',y:'Y',width:'Rộng',height:'Cao',font_size:'Cỡ chữ'};
    for (const key of keys) field(fields, labels[key] + ' (px)', 'number', layer[key], value => {
      layer[key] = value; if (editsArtwork) dirty(state);
    }, {min:['width','height','font_size'].includes(key) ? '1' : '0', step:'1',required:true});
    if ('color' in layer) field(fields, 'Màu', 'color', layer.color, value => { layer.color = value; dirty(state); });
  }
  function renderLayers() {
    const state = current; if (!state) return;
    for (const id of ['review-text-rows','review-logo-rows','review-erase-rows']) $(id).replaceChildren();
    state.text.forEach((layer, i) => {
      const box = layerBox($('review-text-rows'), 'Dòng chữ ' + (i + 1), state.text, layer, state);
      field(box, 'Nội dung', 'text', layer.text, value => { layer.text = value; dirty(state); });
      const label = node('label', 'Font chữ'), select = node('select'); select.dataset.reviewFont = '';
      fontOptions(select, layer.font_id); select.addEventListener('change', () => { layer.font_id = select.value; dirty(state); });
      label.append(select); box.append(label); coordinates(box, layer, ['x','y','font_size'], state);
    });
    state.logos.forEach((layer, i) => {
      const box = layerBox($('review-logo-rows'), 'Logo ' + (i + 1), state.logos, layer, state);
      const asset = (state.view.logo_assets || []).find(item => item.asset_id === layer.asset_id);
      if (asset) { const img = node('img'); img.alt = 'Logo đã lưu ' + (i + 1); image(img, asset.url); box.append(img); }
      box.append(node('small', layer._file ? 'Đã chọn: ' + layer._file.name : layer.asset_id ? 'Đang giữ logo đã lưu.' : 'Chọn PNG để thêm logo.'));
      const label = node('label', layer.asset_id ? 'Thay logo (tùy chọn)' : 'Ảnh logo PNG'), input = node('input');
      input.type = 'file'; input.accept = 'image/png';
      input.addEventListener('change', () => {
        try {
          const file = input.files[0]; if (!file) return;
          checkFile(file, 'logo', 16); layer._file = file; dirty(state);
        } catch (error) { input.value = ''; message(failure(error), true); }
      });
      label.append(input); box.append(label); coordinates(box, layer, ['x','y','width'], state);
    });
    state.erasures.forEach((layer, i) => {
      const box = layerBox($('review-erase-rows'), 'Vùng che ' + (i + 1), state.erasures, layer, state);
      coordinates(box, layer, ['x','y','width','height'], state);
    });
    limits();
  }
  function render() {
    const state = current; if (!state) return;
    $('review-panel').value = state.panel; $('review-panel').disabled = busy; $('review-source').value = state.source;
    const preview = panels.find(item => item.panel === state.panel);
    $('review-source').querySelector('[value=openai]').disabled = !safeURL(preview?.openai_url) && state.view.spec?.source !== 'openai';
    const sourceURL = state.source === (state.view.spec?.source || 'local') ? state.view.source_url
      : state.source === 'openai' ? preview?.openai_url : preview?.local_url;
    image($('review-original'), sourceURL);
    $('review-size').textContent = state.view.width + ' × ' + state.view.height + ' px · ' + state.panel;
    $('review-clean').value = '';
    $('review-clean-note').textContent = state.clean ? 'Đã chọn nền sạch: ' + state.clean.name : 'Nền sạch phải cùng kích thước mảnh.';
    renderLayers(); $('review-region').replaceChildren(); coordinates($('review-region'), state.region, ['x','y','width','height'], state, false);
    $('review-approved').checked = state.approved;
    image($('review-preview'), state.view.preview_url);
    $('review-proof').hidden = !safeURL(state.view.preview_url);
    $('review-proof-label').textContent = state.dirty ? 'Bản đã lưu trước đó · Có thay đổi chưa lưu' : state.approved ? 'Bản đã duyệt để in' : 'Bản nháp đã lưu · Cần duyệt';
    $('review-downloads').replaceChildren();
    if (!state.dirty) {
      download($('review-downloads'), 'Tải PSD có lớp' + (state.approved ? '' : ' · Bản nháp'), state.view.psd_url);
      download($('review-downloads'), state.approved ? 'Tải PNG đã duyệt' : 'Tải PNG nháp · Chưa duyệt', state.view.png_url);
    }
    $('review-vector-downloads').replaceChildren();
    for (const vector of state.vectors) download($('review-vector-downloads'), vector.name || 'Tải SVG', vector.url);
    $('review-approval-note').textContent = state.dirty ? 'Có thay đổi chưa lưu.' : state.approved ? 'Đã duyệt thủ công.' : 'Bản nháp · Chưa duyệt để in.';
    $('review-controls').disabled = busy;
  }
  async function selectPanel(panel) {
    const ticket = ++sequence, id = job.id, key = JSON.stringify([id,panel]);
    current = null; $('review-controls').disabled = true; $('review-panel').disabled = busy; message('Đang mở mảnh…');
    // Keep unsaved edits when polling or switching between jobs and panels.
    if (drafts.has(key)) { current = drafts.get(key); render(); message(''); return; }
    image($('review-original'), null); $('review-proof').hidden = true; $('review-size').textContent = '';
    for (const name of ['review-text-rows','review-logo-rows','review-erase-rows','review-downloads','review-vector-downloads']) $(name).replaceChildren();
    try {
      const view = await api('/api/jobs/' + encodeURIComponent(id) + '/review?panel=' + encodeURIComponent(panel));
      if (ticket !== sequence) return;
      if (view.panel !== panel) throw new Error('Mảnh trả về chưa đúng. Hãy mở lại công việc.');
      current = makeState(view, id); drafts.set(key, current); render(); message('');
    } catch (error) {
      if (ticket === sequence) message(failure(error), true);
    }
  }
  function showJob(value) {
    if (!initialized) return;
    const available = !['queued','running'].includes(value.status)
      ? (value.previews || []).filter(item => safeURL(item.local_url) || safeURL(item.openai_url)) : [];
    $('manual-review').hidden = !available.length;
    if (!available.length) { ++sequence; current = null; job = value; panels = []; return; }
    const same = job?.id === value.id && panels.length && JSON.stringify(panels.map(item => item.panel)) === JSON.stringify(available.map(item => item.panel));
    job = value; panels = available;
    if (same) return;
    $('review-panel').replaceChildren(...panels.map(item => new Option(item.panel, item.panel)));
    selectPanel(panels[0].panel);
  }
  async function run(action) {
    if (busy) return;
    const key = current?.key; busy = true; $('review-controls').disabled = true; $('review-panel').disabled = true; $('review-font-controls').disabled = true;
    message('Đang xử lý trên máy…');
    try { await action(); if (current?.key === key) message('Đã hoàn tất trên máy.'); }
    catch (error) { if (current?.key === key) message(failure(error), true); }
    finally {
      busy = false; $('review-controls').disabled = !current; $('review-panel').disabled = false; $('review-font-controls').disabled = false; limits();
    }
  }
  async function save(state) {
    const texts = state.text.filter(layer => layer.text.trim());
    if (texts.length > 4 || state.logos.length > 4) throw new Error('Mỗi mảnh có tối đa 4 dòng chữ và 4 logo.');
    const text_layers = texts.map(layer => {
      number(layer.x, 'X chữ', 0, state.view.width); number(layer.y, 'Y chữ', 0, state.view.height);
      number(layer.font_size, 'Cỡ chữ', 1);
      if (!/^#[0-9a-f]{6}$/i.test(layer.color)) throw new Error('Chọn màu chữ hợp lệ.');
      return {text:layer.text,font_id:layer.font_id,font_size:layer.font_size,x:layer.x,y:layer.y,color:layer.color};
    });
    const erasures = state.erasures.map(layer => {
      rectangle(layer, state);
      if (!/^#[0-9a-f]{6}$/i.test(layer.color)) throw new Error('Chọn màu vùng che hợp lệ.');
      return {...layer};
    });
    const logo_layers = [];
    for (const layer of state.logos) {
      number(layer.x, 'X logo', 0, state.view.width); number(layer.y, 'Y logo', 0, state.view.height);
      number(layer.width, 'Chiều rộng logo', 1, state.view.width - layer.x);
      const logo = {x:layer.x,y:layer.y,width:layer.width};
      if (layer._file) { checkFile(layer._file, 'logo', 16); logo.image_base64 = await readBase64(layer._file); }
      else if (layer.asset_id) logo.asset_id = layer.asset_id;
      else throw new Error('Chọn PNG cho mỗi logo, hoặc bỏ dòng logo trống.');
      logo_layers.push(logo);
    }
    const body = {panel:state.panel,source:state.source,text_layers,logo_layers,erasures,approved:state.approved};
    if (state.clean) { checkFile(state.clean, 'logo', 16); body.artwork_base64 = await readBase64(state.clean); }
    else if (state.cleanAsset) body.artwork_asset_id = state.cleanAsset;
    const view = await api('/api/jobs/' + encodeURIComponent(state.id) + '/review', body);
    const next = adopt(view, state);
    window.dispatchEvent(new CustomEvent('pod-review-saved', {detail:{jobId:state.id}}));
    return next;
  }
  function adopt(view, previous) {
    if (view.panel !== previous.panel) throw new Error('Mảnh trả về chưa đúng. Hãy mở lại công việc.');
    const next = makeState(view, previous.id, previous); drafts.set(next.key, next);
    if (current === previous) { current = next; render(); }
    window.dispatchEvent(new CustomEvent('pod-review-changed', {detail:{jobId:previous.id}}));
    return next;
  }
  function init(deps) {
    if (initialized) return;
    ({api,safeURL,node,readBase64} = deps); initialized = true;
    $('review-panel').addEventListener('change', () => selectPanel($('review-panel').value));
    $('review-source').addEventListener('change', () => { current.source = $('review-source').value; dirty(current); render(); });
    $('review-approved').addEventListener('change', () => { current.approved = $('review-approved').checked; dirty(current, true); });
    $('review-clean').addEventListener('change', () => {
      try {
        const file = $('review-clean').files[0]; if (!file) return;
        checkFile(file, 'logo', 16); current.clean = file; dirty(current);
        $('review-clean-note').textContent = 'Đã chọn nền sạch: ' + file.name;
      } catch (error) { $('review-clean').value = ''; message(failure(error), true); }
    });
    $('review-add-text').addEventListener('click', () => {
      if (current.text.length >= 4) return;
      current.text.push({text:'',font_id:fonts[0].id,font_size:72,x:0,y:0,color:'#000000'}); dirty(current); renderLayers();
    });
    $('review-add-logo').addEventListener('click', () => {
      if (current.logos.length >= 4) return;
      current.logos.push({x:0,y:0,width:Math.min(200,current.view.width)}); dirty(current); renderLayers();
    });
    $('review-add-erase').addEventListener('click', () => {
      if (current.erasures.length >= 4) { message('Mỗi mảnh có tối đa 4 vùng che.', true); return; }
      current.erasures.push({x:0,y:0,width:Math.min(200,current.view.width),height:Math.min(100,current.view.height),color:'#ffffff'}); dirty(current); renderLayers();
    });
    $('review-form').addEventListener('submit', event => { event.preventDefault(); if (current) run(() => save(current)); });
    $('review-psd').addEventListener('click', () => run(async () => {
      if (!current) return;
      const state = current.dirty || !current.view.spec ? await save(current) : current;
      const view = await api('/api/jobs/' + encodeURIComponent(state.id) + '/psd', {panel:state.panel});
      adopt(view, state);
      if (!safeURL(view.psd_url)) throw new Error('Chưa tạo được PSD. Kiểm tra công cụ xuất PSD trên máy.');
    }));
    $('review-repair').addEventListener('click', () => run(async () => {
      if (!current) return;
      if (!['Front','Back','Sleeve','Hood','Left','Right'].includes(current.panel)) throw new Error('Chỉ phục hồi thân, tay hoặc mũ có trong mẫu.');
      if (current.dirty) throw new Error('Lưu các lớp chữ/logo và vùng chọn trước khi phục hồi.');
      const state = current, bbox = rectangle(state.region, state);
      const view = await api('/api/jobs/' + encodeURIComponent(state.id) + '/repair-patch',{panel:state.panel,source:state.source,bbox});
      adopt(view,state);
      message('Đã phục hồi vùng chọn. Vùng ngoài và các lớp chữ/logo đã lưu được giữ nguyên; cần duyệt lại.');
      window.dispatchEvent(new CustomEvent('pod-review-saved',{detail:{jobId:state.id}}));
    }));
    $('review-reset').addEventListener('click', async () => {
      if (!current || busy) return;
      const panel = current.panel, id = current.id; drafts.delete(current.key);
      await selectPanel(panel);
      window.dispatchEvent(new CustomEvent('pod-review-changed', {detail:{jobId:id}}));
    });
    for (const [id, action] of [['review-segment','segment'],['review-vectorize','vectorize']]) $(id).addEventListener('click', () => run(async () => {
      const state = current; if (!state) return;
      if (action === 'segment' && state.logos.length >= 4) throw new Error('Bỏ một logo trước khi tách thêm vùng.');
      const bbox = rectangle(state.region, state);
      if (action === 'vectorize' && bbox[2] * bbox[3] > 4_000_000)
        throw new Error('Vùng tạo SVG tối đa 4 triệu pixel. Hãy giảm chiều rộng hoặc chiều cao.');
      const body = {panel:state.panel,source:state.source,bbox};
      // Continue SAM 2 -> VTracer using the last saved cutout when one is available.
      if (action === 'vectorize') {
        const cutout = [...state.logos].reverse().find(layer => layer.asset_id && !layer._file);
        if (cutout) body.asset_id = cutout.asset_id;
      }
      const result = await api('/api/jobs/' + encodeURIComponent(state.id) + '/' + action, body);
      if (!safeURL(result.url)) throw new Error('Công cụ chưa trả về tệp kết quả. Hãy thử lại.');
      if (action === 'segment') {
        if (!result.asset_id) throw new Error('Chưa lưu được logo đã tách. Hãy thử lại.');
        state.logos.push({asset_id:result.asset_id,x:result.x,y:result.y,width:result.width});
        state.view.logo_assets = [...(state.view.logo_assets || []),{asset_id:result.asset_id,url:result.url}]; dirty(state);
      } else state.vectors.push(result);
      if (current === state) render();
    }));
    $('review-font-form').addEventListener('submit', event => {
      event.preventDefault(); run(async () => {
        const file = $('review-font-file').files[0]; checkFile(file, 'font', 5);
        const source_url = $('review-font-url').value.trim();
        if (source_url) {
          let url; try { url = new URL(source_url); } catch { throw new Error('Nhập đường dẫn trang font trên DaFont.'); }
          if (url.protocol !== 'https:' || !['dafont.com','www.dafont.com'].includes(url.hostname) || url.username || url.password)
            throw new Error('Dùng đường dẫn https://www.dafont.com/ của font, hoặc để trống.');
        }
        await api('/api/fonts', {filename:file.name,data_base64:await readBase64(file),...(source_url ? {source_url} : {}),license_confirmed:$('review-font-license').checked});
        $('review-font-form').reset(); await get_fonts();
      });
    });
    $('review-samples-form').addEventListener('submit', event => {
      event.preventDefault(); $('review-font-samples').replaceChildren();
      const text = Array.from($('review-sample-text').value).slice(0,120).join('');
      for (const select of $('review-sample-selects').querySelectorAll('select')) {
        const figure = node('figure'), img = node('img'); img.alt = 'Mẫu chữ: ' + select.selectedOptions[0].textContent;
        img.src = '/api/font-preview?' + new URLSearchParams({font_id:select.value,text});
        img.addEventListener('error', () => { img.hidden = true; figure.append(node('small', 'Chưa tạo được mẫu font. Kiểm tra font và máy chủ local.')); }, {once:true});
        figure.append(img,node('figcaption',select.selectedOptions[0].textContent)); $('review-font-samples').append(figure);
      }
    });
    for (const id of ['review-original','review-preview']) $(id).addEventListener('error', () => message('Chưa tải được ảnh kiểm tra. Hãy mở lại mảnh hoặc lưu lại bản xem trước.', true));
    get_fonts().catch(error => message(failure(error), true));
  }
  window.PodReview = {init,showJob,hasUnsaved:id => [...drafts.values()].some(state => state.id === id && state.dirty)};
})();
