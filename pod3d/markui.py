"""`python -m pod3d mark mockup.jpg -t hoodie`: a page in the browser to click the 8 points of
each panel. Saving writes mockup.marks.json next to the image, which `run` then picks up."""
import base64
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import marks

PAGE = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>Chấm điểm mốc</title>
<style>
body{margin:0;font:14px system-ui,sans-serif;display:flex;height:100vh;background:#f4f4f5;color:#18181b}
#side{width:300px;padding:16px;overflow:auto;background:#fff;border-right:1px solid #e4e4e7}
#wrap{flex:1;overflow:auto;position:relative}
canvas{display:block;cursor:crosshair}
button{font:inherit;padding:6px 10px;margin:2px 0;border:1px solid #d4d4d8;border-radius:6px;background:#fff;cursor:pointer;width:100%;text-align:left}
button.on{background:#18181b;color:#fff;border-color:#18181b}
#save{background:#16a34a;color:#fff;border-color:#16a34a;text-align:center;margin-top:12px}
.hint{color:#52525b;margin:8px 0}
svg text{font:bold 11px system-ui}
</style></head><body>
<div id="side">
<b>Chấm 8 điểm cho từng mảnh</b>
<p class="hint">Chọn mảnh, rồi bấm 8 điểm trên ảnh theo thứ tự trong hình dưới (theo chiều kim đồng hồ). Kéo điểm để chỉnh. Mảnh không thấy trên ảnh thì bỏ qua.</p>
<svg width="120" height="140" viewBox="0 0 120 140">
<rect x="20" y="15" width="80" height="110" fill="#e4e4e7" stroke="#71717a"/>
<g fill="#dc2626"><circle cx="20" cy="15" r="9"/><circle cx="60" cy="15" r="9"/><circle cx="100" cy="15" r="9"/>
<circle cx="100" cy="70" r="9"/><circle cx="100" cy="125" r="9"/><circle cx="60" cy="125" r="9"/>
<circle cx="20" cy="125" r="9"/><circle cx="20" cy="70" r="9"/></g>
<g fill="#fff" text-anchor="middle"><text x="20" y="19">1</text><text x="60" y="19">2</text><text x="100" y="19">3</text>
<text x="100" y="74">4</text><text x="100" y="129">5</text><text x="60" y="129">6</text><text x="20" y="129">7</text><text x="20" y="74">8</text></g>
<text x="60" y="8" text-anchor="middle" fill="#52525b">trên</text></svg>
<div id="panels"></div>
<p class="hint" id="hint"></p>
<button id="clear">Xoá điểm của mảnh này</button>
<button id="save">Lưu</button>
<p class="hint" id="msg"></p>
</div>
<div id="wrap"><canvas id="c"></canvas></div>
<script>
const DATA = __DATA__;
const img = new Image(); img.src = DATA.image;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
let pts = DATA.marks || {}, cur = DATA.panels[0].name, drag = null, scale = 1;
const COLORS = ['#dc2626','#2563eb','#16a34a','#d97706','#9333ea','#0891b2'];
function list(){
  const el = document.getElementById('panels'); el.innerHTML = '';
  DATA.panels.forEach(p => {
    const b = document.createElement('button');
    b.textContent = p.name + '  (' + ((pts[p.name]||[]).length) + '/8)';
    if (p.name === cur) b.className = 'on';
    b.onclick = () => { cur = p.name; list(); draw(); };
    el.appendChild(b);
  });
  document.getElementById('hint').textContent = DATA.panels.find(p => p.name === cur).hint;
}
function quad(a, m, b, t){ const c=[2*m[0]-(a[0]+b[0])/2, 2*m[1]-(a[1]+b[1])/2];
  return [(1-t)**2*a[0]+2*(1-t)*t*c[0]+t*t*b[0], (1-t)**2*a[1]+2*(1-t)*t*c[1]+t*t*b[1]]; }
function draw(){
  const W = document.getElementById('wrap').clientWidth - 4, H = document.getElementById('wrap').clientHeight - 4;
  scale = Math.min(W / img.width, H / img.height, 4);
  cv.width = img.width * scale; cv.height = img.height * scale;
  ctx.drawImage(img, 0, 0, cv.width, cv.height);
  DATA.panels.forEach((p, i) => {
    const q = pts[p.name] || []; const col = COLORS[i % COLORS.length];
    ctx.globalAlpha = p.name === cur ? 1 : 0.45; ctx.strokeStyle = col; ctx.lineWidth = 2;
    if (q.length === 8) {
      const sides = [[0,1,2],[2,3,4],[4,5,6],[6,7,0]];
      ctx.beginPath();
      sides.forEach(([a,m,b]) => { for (let t = 0; t <= 1.0001; t += 0.05) {
        const [x,y] = quad(q[a], q[m], q[b], t); ctx.lineTo(x*scale, y*scale); } });
      ctx.stroke();
    }
    q.forEach((pt, k) => { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(pt[0]*scale, pt[1]*scale, 8, 0, 7); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 11px system-ui'; ctx.textAlign = 'center'; ctx.fillText(k+1, pt[0]*scale, pt[1]*scale+4); });
  });
  ctx.globalAlpha = 1;
}
function at(e){ const r = cv.getBoundingClientRect(); return [(e.clientX-r.left)/scale, (e.clientY-r.top)/scale]; }
cv.onmousedown = e => {
  const p = at(e), q = pts[cur] || (pts[cur] = []);
  const k = q.findIndex(t => Math.hypot(t[0]-p[0], t[1]-p[1]) * scale < 10);
  if (k >= 0) drag = k;
  else if (q.length < 8) {
    q.push(p);
    if (q.length === 8) {  // panel done: go on to the next one still missing points
      const next = DATA.panels.find(t => (pts[t.name] || []).length < 8);
      if (next) cur = next.name;
    }
    list();
  }
  draw();
};
cv.onmousemove = e => { if (drag !== null) { pts[cur][drag] = at(e); draw(); } };
window.onmouseup = () => drag = null;
document.getElementById('clear').onclick = () => { delete pts[cur]; list(); draw(); };
document.getElementById('save').onclick = async () => {
  const done = {}; Object.keys(pts).forEach(k => { if (pts[k].length === 8) done[k] = pts[k]; });
  const r = await fetch('/save', {method:'POST', body: JSON.stringify({mockup_size:[img.width, img.height], panels: done})});
  document.getElementById('msg').textContent = r.ok ? 'Đã lưu ' + Object.keys(done).length + ' mảnh. Có thể đóng trang và chạy lệnh run.' : 'Lưu lỗi';
};
img.onload = () => { list(); draw(); }; window.onresize = draw;
</script></body></html>"""


def serve(template, mockup_path, log=print):
    mockup_path = Path(mockup_path)
    out = marks.path_for(mockup_path)
    mime = mimetypes.guess_type(mockup_path.name)[0] or "image/jpeg"
    data = {
        "image": f"data:{mime};base64," + base64.b64encode(mockup_path.read_bytes()).decode(),
        "panels": [{"name": p.name, "hint": p.hint} for p in template.panels if p.source == "unwrap"],
        "marks": marks.load(out)["panels"] if out.exists() else {},
    }
    page = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).encode()
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out.write_text(json.dumps(body, indent=1))
            self.send_response(200)
            self.end_headers()
            log(f"  saved {len(body['panels'])} panels -> {out}")
            done.set()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{srv.server_port}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"  marking page: {url} (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return out
