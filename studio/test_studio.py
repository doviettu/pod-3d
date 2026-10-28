import base64
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

import studio
from openai_api import Settings


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        self.tpl = SimpleNamespace(name="sweater", dpi=300,
            panels=[SimpleNamespace(name=n, size=(20, 24), source="unwrap") for n in ("Front", "Back")],
            silhouette_path=self.root / "silhouette.png")
        self.tpl.map_path = lambda n: self.root / (n + ".npz")
        self.tpl.panel = lambda n: next(p for p in self.tpl.panels if p.name == n)
        self.tpl.silhouette_path.touch()
        for panel in self.tpl.panels:
            self.tpl.map_path(panel.name).touch()
        templates = SimpleNamespace(available=lambda: ["sweater"], load=lambda name: self.tpl)
        self.local_run = patch.object(self, "fake_run", wraps=self.fake_run).start()
        pipeline = SimpleNamespace(run=self.local_run)
        patch("studio.load_pod", return_value=(pipeline, templates)).start()
        patch("studio.runtime_info", return_value={"python": "test", "mps": False}).start()
        self.settings = Settings(self.root / "private" / "settings.json")
        self.engine = studio.Engine(self.settings, self.root, self.root / "jobs")
        self.addCleanup(lambda: self.engine.executor.shutdown(wait=True))
        image = io.BytesIO()
        Image.new("RGB", (30, 30), "red").save(image, "PNG")
        self.request = {"template": "sweater", "filename": "<img onerror=alert(1)>.png",
                        "image_base64": base64.b64encode(image.getvalue()).decode(),
                        "openai": False, "use_model": False, "panels": ["Front"]}

    def fake_run(self, tpl, source, out_dir, **kwargs):
        out_dir.mkdir()
        kwargs["log"]("Đang làm Front")
        for panel in tpl.panels:
            Image.new("RGB", panel.size, "red").save(out_dir / (panel.name + ".png"), dpi=(300, 300))
        path = out_dir.with_suffix(".zip")
        with zipfile.ZipFile(path, "w") as archive:
            for p in out_dir.glob("*.png"):
                archive.write(p, p.name)
        return path

    def finish(self, request=None):
        created = self.engine.submit(request or self.request)
        self.engine.executor.shutdown(wait=True)
        return self.engine.get_job(created["id"])

    def test_local_run_never_calls_api_and_checks_outputs(self):
        with patch("studio.reconstruct_artwork") as api:
            job = self.finish()
        api.assert_not_called()
        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["review_required"])
        self.assertEqual(job['quality_status'], 'awaiting_review')
        self.assertEqual(len(job["previews"]), 2)
        self.assertEqual(job["estimated_image_cost_usd"], 0)
        self.assertTrue(any(a["kind"] == "zip" for a in job["artifacts"]))
        self.assertFalse(self.local_run.call_args.kwargs["force"])
        self.assertFalse(self.local_run.call_args.kwargs["use_model"])

    def test_missing_map_is_rejected_before_run(self):
        self.tpl.map_path("Back").unlink()
        with self.assertRaisesRegex(ValueError, "Back"):
            self.engine.submit(self.request)
        self.local_run.assert_not_called()

    def test_missing_output_cannot_report_success(self):
        def partial(*args, **kwargs):
            path = self.fake_run(*args, **kwargs)
            (path.with_suffix("") / "Back.png").unlink()
            return path
        self.engine.pipeline.run = partial
        job = self.finish()
        self.assertEqual(job["status"], "failed")
        self.assertFalse(any(a['kind'] == 'zip' for a in job['artifacts']))

    def test_mac_sidecars_do_not_enter_print_archive(self):
        def with_sidecars(*args, **kwargs):
            path = self.fake_run(*args, **kwargs)
            folder = path.with_suffix("")
            for panel in self.tpl.panels:
                (folder / ("._" + panel.name + ".png")).write_bytes(b"macOS metadata")
            with zipfile.ZipFile(path, "a") as archive:
                archive.write(folder / "._Front.png", "._Front.png")
            return path
        self.engine.pipeline.run = with_sidecars
        job = self.finish()
        self.assertEqual(job["status"], "succeeded")
        with zipfile.ZipFile(self.engine.root / job["id"] / "local.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"Front.png", "Back.png"})
            self.assertIsNone(archive.testzip())

    def test_unexpected_real_panel_is_still_rejected(self):
        def extra_panel(*args, **kwargs):
            path = self.fake_run(*args, **kwargs)
            Image.new("RGB", (20, 24)).save(path.with_suffix("") / "Unexpected.png")
            return path
        self.engine.pipeline.run = extra_panel
        job = self.finish()
        self.assertEqual(job["status"], "failed")

    def test_bad_inputs_never_queue_or_spend(self):
        for updates in ({"image_base64": "not base64!"}, {"openai": "yes"},
                        {"template": "../../private"}, {"openai": True}, {"gemini": True},
                        {"openai": True, "panels": ["Front", "Front"]}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.engine.submit({**self.request, **updates})
        self.assertFalse(self.engine.jobs)

    def test_openai_creates_separate_copy_and_pins_config(self):
        key = "sk-private-key-for-test"
        self.settings.update(api_key=key)
        def edited(source, target, config, *, panel, canvas_size, notes, design_spec):
            self.assertEqual(config.api_key, key)
            self.assertEqual(config.model, "gpt-image-2.5-flare")
            self.assertEqual(config.quality, "medium")
            self.assertEqual(source.name, 'input.png')
            self.assertEqual(panel, 'Front')
            self.assertEqual(notes, '')
            Image.new("RGB", (40, 48), "blue").save(target)
            return {"estimated_image_cost_usd": None}
        with patch("studio.reconstruct_artwork", side_effect=edited) as api:
            job = self.finish({**self.request, "openai": True})
        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["review_required"])
        self.assertEqual(job['workflow'], 'shared-artwork-v2')
        api.assert_called_once()
        self.assertFalse(self.local_run.call_args.kwargs['use_model'])
        folder = self.engine.root / job["id"]
        with Image.open(folder / "local" / "Front.png") as local:
            self.assertEqual(local.getpixel((0, 0)), (255, 0, 0))
        with Image.open(folder / "openai" / "Front.png") as ai:
            self.assertEqual(ai.getpixel((0, 0)), (0, 0, 255))
        with zipfile.ZipFile(folder / "openai.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"Front.png", "Back.png"})
        self.assertNotIn(key, (folder / "report.json").read_text())

    def test_openai_sheet_is_one_request_and_cached_on_rerun(self):
        self.settings.update(api_key="sk-private-key-for-test")
        calls = []

        def request(settings, payload):
            calls.append(payload)
            edge = int(payload['size'].split('x')[0])
            sheet = Image.new("RGB", (edge, edge), "blue")
            stream = io.BytesIO()
            sheet.save(stream, "PNG")
            return {'data': [{'b64_json': base64.b64encode(stream.getvalue()).decode()}]}
        ask = {**self.request, "openai": True, "ai_mode": "sheet", "panels": ["Front", "Back"]}
        with patch("openai_api._request", side_effect=request), patch("studio.reconstruct_artwork") as per_panel:
            job = self.finish(ask)
            self.engine.executor = studio.ThreadPoolExecutor(max_workers=1)
            again = self.finish(ask)
        per_panel.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual((job["status"], again["status"]), ("succeeded", "succeeded"))
        self.assertEqual(job["ai_sheet"]["paid_calls"], 1)
        self.assertEqual(again["ai_sheet"]["paid_calls"], 0)
        with Image.open(self.engine.root / again["id"] / "openai" / "Back.png") as back:
            self.assertEqual((back.size, back.getpixel((0, 0))), ((20, 24), (0, 0, 255)))
        estimate = self.engine.ai_estimate("sweater", ["Front", "Back"], "sheet", job["design_spec"], job["input_sha256"])
        self.assertEqual(estimate["usd"], 0)

    def test_openai_sheet_respects_the_budget(self):
        self.settings.update(api_key="sk-private-key-for-test")
        self.engine.budget.update({"daily_usd": 0})
        with patch("openai_api._request") as network, self.assertRaises(ValueError):
            self.engine.submit({**self.request, "openai": True, "ai_mode": "sheet", "panels": ["Front"]})
        network.assert_not_called()

    def test_openai_sheet_auto_skips_when_local_saw_everything(self):
        self.settings.update(api_key="sk-private-key-for-test")
        with patch("openai_api._request") as network:
            job = self.finish({**self.request, "openai": True, "ai_mode": "sheet", "panels": "auto"})
        network.assert_not_called()
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["panels"], [])

    def test_openai_failure_keeps_local_and_does_not_retry(self):
        key = "sk-private-key-for-test"
        self.settings.update(api_key=key)
        with patch("studio.reconstruct_artwork", side_effect=RuntimeError("API rejected " + key)) as api:
            job = self.finish({**self.request, "openai": True})
        api.assert_called_once()
        self.assertEqual(job["status"], "failed")
        self.assertTrue(any(a["kind"] == "zip" for a in job["artifacts"]))
        self.assertNotIn(key, json.dumps(job))
        self.assertFalse((self.engine.root / job["id"] / "openai.zip").exists())

    def test_downloads_are_limited_to_recorded_artifacts(self):
        job = self.finish()
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.engine.file_path(job["id"], "../../private/settings.json")
        with self.assertRaises(FileNotFoundError):
            self.engine.file_path(job["id"], "input.png")
        self.assertTrue(self.engine.file_path(job["id"], "local/Front.png").is_file())

    def test_restart_marks_unfinished_job_interrupted(self):
        folder = self.engine.root / ("a" * 32)
        folder.mkdir()
        studio.write_json(folder / "report.json", {"id": folder.name, "status": "running", "created_at": studio.now()})
        other = studio.Engine(self.settings, self.root, self.engine.root)
        self.addCleanup(lambda: other.executor.shutdown())
        self.assertEqual(other.get_job(folder.name)["status"], "interrupted")

    def test_legacy_gemini_history_survives_switch_without_resubmission(self):
        job = self.finish()
        folder = self.engine.root / job['id']
        old_preview = folder / 'previews/Front-gemini.jpg'
        old_preview.write_bytes((folder / 'previews/Front-local.jpg').read_bytes())
        job.pop('openai')
        job.pop('provider')
        job['gemini'] = True
        job['model'] = 'gemini-nano-banana-2.1'
        job['previews'][0]['gemini_url'] = self.engine.url(job['id'], old_preview)
        studio.write_json(folder / 'report.json', job)
        before = (folder / 'report.json').read_bytes()
        with patch('studio.reconstruct_artwork') as api:
            other = studio.Engine(self.settings, self.root, self.engine.root)
            self.addCleanup(lambda: other.executor.shutdown())
        api.assert_not_called()
        self.assertEqual(other.get_job(job['id'])['model'], 'gemini-nano-banana-2.1')
        self.assertEqual(other.file_path(job['id'], 'previews/Front-gemini.jpg'), old_preview.resolve())
        self.assertEqual((folder / 'report.json').read_bytes(), before)

    def test_sleeve_is_a_supported_paid_panel_and_artwork_precedes_placement(self):
        self.tpl.panels.append(SimpleNamespace(name='Sleeve', size=(18, 28), source='unwrap'))
        self.tpl.map_path('Sleeve').touch()
        self.settings.update(api_key='sk-private-sleeve-test')
        native_sizes = {'Front': (30, 36), 'Back': (30, 36), 'Sleeve': (27, 42)}
        def reconstruct(source, target, config, *, panel, canvas_size, notes, design_spec):
            self.assertEqual(source.name, 'input.png')
            self.assertEqual(notes, 'Keep deer bands')
            Image.new('RGB', native_sizes[panel], 'blue').save(target)
            return {'estimated_image_cost_usd': None, 'manual_review_required': True}
        with patch('studio.reconstruct_artwork', side_effect=reconstruct) as api:
            job = self.finish({**self.request, 'openai': True,
                               'panels': ['Front', 'Back', 'Sleeve'], 'design_notes': 'Keep deer bands'})
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(api.call_count, 3)
        self.assertEqual(job['quality_status'], 'awaiting_review')
        with Image.open(self.engine.file_path(job['id'], 'artwork/Sleeve.png')) as master:
            self.assertEqual(master.size, (27, 42))
        with Image.open(self.engine.file_path(job['id'], 'openai/Sleeve.png')) as panel:
            self.assertEqual(panel.size, (18, 28))
            self.assertLess(abs(panel.info['dpi'][0] - 300), 1)
        self.assertEqual(job['required_review_panels'], ['Front', 'Back', 'Sleeve'])

    def test_print_zip_requires_all_current_approvals_and_old_zip_remains_a_snapshot(self):
        job = self.finish()
        with self.assertRaisesRegex(ValueError, 'Chưa duyệt đủ'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        for panel in ('Front', 'Back'):
            self.engine.postprocess(job['id'], 'review', {'panel': panel, 'approved': True})
        self.assertEqual(self.engine.get_job(job['id'])['quality_status'], 'approved')
        with self.assertRaisesRegex(ValueError, 'xem trên áo'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        preview_path = self.engine.root / job['id'] / 'previews/garment-test.jpg'
        Image.new('RGB', (50,50), 'red').save(preview_path)
        self.engine.add_artifact(job['id'], preview_path, 'image')
        with patch('studio.create_garment_preview', return_value={'url': self.engine.url(job['id'], preview_path), 'kind': 'calibrated-2d'}):
            self.engine.postprocess(job['id'], 'garment-preview', {})
        with self.assertRaisesRegex(ValueError, 'bảng đối chiếu'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        self.engine.postprocess(job['id'], 'assembly-proof', {})
        export = self.engine.postprocess(job['id'], 'approved-export', {})
        relative = export['url'].split('/' + job['id'] + '/', 1)[1]
        path = self.engine.file_path(job['id'], relative)
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {'Front.png', 'Back.png', 'approval.json'})
            self.assertEqual(json.loads(archive.read('approval.json'))['reviews'], export['revisions'])
        before = path.read_bytes()
        self.engine.postprocess(job['id'], 'review', {'panel': 'Front', 'approved': False})
        self.assertEqual(self.engine.get_job(job['id'])['quality_status'], 'partially_reviewed')
        with self.assertRaisesRegex(ValueError, 'Front'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        self.assertEqual(path.read_bytes(), before)

    def test_export_rejects_missing_preview_and_unapproved_fallback_proof(self):
        job = self.finish()
        for panel in ('Front','Back'):
            self.engine.postprocess(job['id'], 'review', {'panel':panel,'approved':True})
        current = self.engine.get_job(job['id'])
        revisions = {name:value['revision'] for name,value in current['reviews'].items()}
        self.engine.update(job['id'], garment_preview={'kind':'calibrated-2d','url':'/not-recorded.jpg',
                                                       'review_revisions':revisions})
        self.engine.postprocess(job['id'], 'assembly-proof', {})
        with self.assertRaisesRegex(ValueError, 'không còn hợp lệ'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        preview = self.engine.root / job['id'] / 'previews/garment-present.jpg'
        Image.new('RGB',(50,50),'red').save(preview)
        self.engine.add_artifact(job['id'],preview,'image')
        self.engine.update(job['id'], garment_preview={'kind':'calibrated-2d',
            'url':self.engine.url(job['id'],preview),'review_revisions':revisions})
        spec = self.engine.root / job['id'] / 'reviews' / revisions['Front'] / 'spec.json'
        spec.unlink()
        proof = self.engine.postprocess(job['id'], 'assembly-proof', {})
        self.assertIn('Front',proof['missing_approvals'])
        self.assertEqual(proof['sources']['Front']['path'],'local/Front.png')
        with self.assertRaisesRegex(ValueError, 'bảng đối chiếu'):
            self.engine.postprocess(job['id'], 'approved-export', {})
        self.assertFalse(list((self.engine.root / job['id']).glob('approved-*.zip')))

    def test_detection_is_free_and_does_not_queue_or_infer_artwork(self):
        image_bytes = base64.b64decode(self.request['image_base64'])
        reference = self.root / 'reference.png'
        reference.write_bytes(image_bytes)
        self.engine.templates.detection_references = lambda:[{
            'id':'sweater','label':'Mẫu chuẩn','path':reference,'background':'white','ready':True}]
        with patch('studio.reconstruct_artwork') as paid:
            result = self.engine.detect_product({'image_base64':self.request['image_base64']})
        self.assertEqual(result['selected_template'],'sweater')
        self.assertEqual(result['method'],'exact')
        self.assertEqual(result['paid_calls'],0)
        self.assertFalse(self.engine.jobs)
        self.local_run.assert_not_called()
        paid.assert_not_called()
        self.assertNotIn(str(self.root),json.dumps(result))
        with self.assertRaises(ValueError):
            self.engine.detect_product({'image_base64':'not base64'})

    def test_draft_working_canvas_cannot_be_exported_as_factory_pattern(self):
        job = self.finish()
        for panel in ('Front','Back'):
            self.engine.postprocess(job['id'],'review',{'panel':panel,'approved':True})
        self.engine.templates.metadata = lambda name:{'production_ready':False,'label':'Mẫu nháp'}
        with self.assertRaisesRegex(ValueError,'kích thước in xưởng'):
            self.engine.postprocess(job['id'],'approved-export',{})
        self.assertFalse(list((self.engine.root / job['id']).glob('approved-*.zip')))

    def test_shared_plan_is_offline_and_bad_briefs_never_queue(self):
        with patch('studio.reconstruct_artwork') as network:
            plan = self.engine.design_plan({'template': 'sweater', 'design_notes': 'Keep deer bands'})
        self.assertEqual(plan['paid_calls'], 0)
        self.assertEqual(set(plan['spec']['panels']), {'Front', 'Back'})
        self.assertIn('Keep deer bands', plan['prompts']['Front'])
        network.assert_not_called()
        with self.assertRaises(ValueError):
            self.engine.submit({**self.request, 'design_data': {'palette': [{'color': 'bad'}]}})
        with self.assertRaises(ValueError):
            self.engine.submit({**self.request, 'use_reference': 'yes'})
        with self.assertRaisesRegex(ValueError, 'kích thước'):
            self.engine.submit({**self.request, 'design_geometry': {'Front':[999,999]}})
        self.assertFalse(self.engine.jobs)

    def test_exact_source_uses_originals_and_skips_even_a_selected_paid_request(self):
        self.settings.update(api_key='sk-test-reference-only')
        def restore(match, destination, template):
            destination.mkdir()
            for panel in template.panels:
                Image.new('RGB', panel.size, 'green').save(destination / (panel.name + '.png'), dpi=(300,300))
            return {'mode': 'source-original', 'sample': 'factory-sample', 'panels': {}}
        self.engine.references = SimpleNamespace(find=lambda *args: {'sample': 'factory-sample'}, restore=restore)
        with patch('studio.reconstruct_artwork') as network:
            job = self.finish({**self.request, 'openai': True})
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['workflow'], 'source-original')
        self.assertEqual(job['provider'], 'reference')
        self.assertEqual(job['estimated_image_cost_usd'], 0)
        self.assertFalse(job['openai'])
        self.assertTrue(job['review_required'])
        self.local_run.assert_not_called()
        network.assert_not_called()

    def test_patch_preserves_saved_layers_and_invalidates_approval_without_overwriting_history(self):
        self.settings.update(api_key='sk-test-patch-only')
        job = self.finish()
        layers = [{'text':'A','font_id':'system-arial','font_size':9,'x':4,'y':3,'color':'#111111'}]
        old = self.engine.postprocess(job['id'], 'review', {'panel':'Front','approved':True,'text_layers':layers})
        old_path = self.engine.root / job['id'] / 'reviews' / old['revision'] / 'Front-approved.png'
        previous_bytes = old_path.read_bytes()
        def repair(source, target, config, **kwargs):
            self.assertEqual(kwargs['panel'], 'Front')
            self.assertTrue(kwargs['protected_boxes'])
            self.assertEqual(kwargs['protected_boxes'][0][:2], [4,3])
            Image.new('RGB', (20,24), 'blue').save(target, dpi=(300,300))
            return {'pixel_lock_verified':True,'paid_calls':1,'manual_review_required':True}
        with patch('studio.repair_image', side_effect=repair) as network:
            view = self.engine.postprocess(job['id'], 'repair-patch',
                                          {'panel':'Front','source':'local','bbox':[0,0,20,24]})
        network.assert_called_once()
        self.assertNotEqual(view['revision'], old['revision'])
        self.assertFalse(view['spec']['approved'])
        self.assertEqual(view['spec']['text_layers'], layers)
        self.assertEqual(old_path.read_bytes(), previous_bytes)
        self.assertTrue(self.engine.get_job(job['id'])['review_required'])
        self.assertEqual(len(self.engine.get_job(job['id'])['api_repairs']), 1)
        self.assertIsNone(self.engine.get_job(job['id'])['estimated_image_cost_usd'])
        self.assertTrue(self.engine.get_job(job['id'])['repair_attempted'])

    def test_http_rejects_cross_origin_and_never_returns_key(self):
        key = "sk-private-key-for-http-test"
        self.settings.update(api_key=key)
        server = studio.ThreadingHTTPServer(("127.0.0.1", 0), studio.Handler)
        server.engine = self.engine
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(close)
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            data = response.read()
            status = response.status
            connection.close()
            return status, data
        status, data = request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn(key.encode(), data)
        status, _ = request("POST", "/api/settings", '{"model":"gpt-image-2.5-sunburst"}',
                            {"Content-Type": "application/json", "Origin": "https://evil.invalid"})
        self.assertEqual(status, 403)
        self.assertEqual(self.settings.model, "gpt-image-2.5-flare")
        status, _ = request("GET", "/api/status", headers={"Host": "evil.invalid"})
        self.assertEqual(status, 403)
        status, _ = request("POST", "/api/settings", "{broken", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _ = request("GET", "/settings.json")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
