import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote

from PIL import Image, ImageDraw

import assembly_proof as assembly


class FakeEngine:
    def __init__(self, root, job_id, template):
        self.root = root
        self.job = {"id": job_id, "template": "factory", "artifacts": [],
                    "reviews": {}, "previews": [], "panels": ["Front"]}
        self.templates = SimpleNamespace(load=lambda name: template)

    def get_job(self, job_id):
        if job_id != self.job["id"]:
            raise FileNotFoundError(job_id)
        return copy.deepcopy(self.job)

    def url(self, job_id, path):
        relative = Path(path).resolve().relative_to((self.root / job_id).resolve())
        return "/files/" + job_id + "/" + quote(relative.as_posix())

    def add_artifact(self, job_id, path, kind, name=None):
        self.job["artifacts"].append({"url": self.url(job_id, path), "kind": kind})

    def file_path(self, job_id, relative):
        folder = (self.root / job_id).resolve()
        target = (folder / relative).resolve()
        allowed = {item["url"] for item in self.job["artifacts"]}
        if (not target.is_relative_to(folder) or not target.is_file()
                or self.url(job_id, target) not in allowed):
            raise FileNotFoundError(relative)
        return target


class AssemblyProofTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.job_id = "a" * 32
        self.folder = self.root / self.job_id
        self.folder.mkdir()
        self.names = ["Front", "Back", "Sleeve", "Collar", "Hem", "Wrist"]
        self.template = SimpleNamespace(panels=[
            SimpleNamespace(name=name, size=(80 + index * 2, 100 + index * 2))
            for index, name in enumerate(self.names)])
        self.engine = FakeEngine(self.root, self.job_id, self.template)
        for panel in self.template.panels:
            self.panel(f"local/{panel.name}.png", panel.size)

    def panel(self, relative, size, recorded=True, format="PNG", color="gray"):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, color) as image:
            draw = ImageDraw.Draw(image)
            w, h = size
            draw.rectangle((0, 0, w - 1, 9), fill="red")
            draw.rectangle((0, h - 10, w - 1, h - 1), fill="blue")
            draw.rectangle((0, 10, 9, h - 11), fill="green")
            draw.rectangle((w - 10, 10, w - 1, h - 11), fill="yellow")
            image.save(path, format=format)
        if recorded:
            self.engine.add_artifact(self.job_id, path, "image")
        return path

    def review(self, name="Front", revision="b" * 32, approved=False, recorded=True):
        size = next(panel.size for panel in self.template.panels if panel.name == name)
        relative = f"reviews/{revision}/{name}-{'approved' if approved else 'draft'}.png"
        path = self.panel(relative, size, recorded, color="white")
        (path.parent / "spec.json").write_text(json.dumps({"approved": approved}))
        self.engine.job["reviews"][name] = {"revision": revision, "approved": approved}
        return relative

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def create(self):
        return assembly.create_assembly_proof(self.engine, self.job_id)

    def current(self, proof, job=None):
        return assembly.proof_is_current(self.engine, job or self.engine.get_job(self.job_id), proof)

    def test_all_six_panels_and_originals_and_none_revisions(self):
        originals = {path: self.sha(path) for path in self.folder.rglob("*.png")}
        before = copy.deepcopy(self.engine.job)
        proof = self.create()
        self.assertEqual(proof["kind"], "six-panel-proof")
        self.assertEqual(proof["panels"], self.names)
        self.assertEqual(proof["missing_approvals"], self.names)
        self.assertEqual(proof["review_revisions"], dict.fromkeys(self.names))
        self.assertEqual(proof["label"], "canvas edges, not verified sew lines")
        self.assertEqual(proof["dimensions"],
                         {panel.name: list(panel.size) for panel in self.template.panels})
        self.assertEqual(len(proof["images"]), 1)
        self.assertNotIn("approved", proof)
        self.assertEqual(self.engine.job["reviews"], before["reviews"])
        self.assertEqual(self.engine.job.keys(), before.keys())
        for name in self.names:
            source = proof["sources"][name]
            self.assertEqual(source["path"], f"local/{name}.png")
            self.assertEqual(source["sha256"], self.sha(self.folder / source["path"]))
            self.assertEqual(source["size"], proof["dimensions"][name])
        for path, digest in originals.items():
            self.assertEqual(self.sha(path), digest)
        manifest = self.engine.file_path(self.job_id, proof["manifest"])
        self.assertEqual(json.loads(manifest.read_text()), proof)
        image = self.engine.file_path(self.job_id, proof["images"][0]["path"])
        with Image.open(image) as sheet:
            self.assertEqual(sheet.format, "JPEG")
            self.assertEqual(sheet.size, (1260, 1064))
        self.assertTrue(self.current(json.loads(json.dumps(proof))))

    def test_four_edges_have_correct_direction_and_required_label_is_drawn(self):
        text = []
        original = ImageDraw.ImageDraw.text

        def record(draw, position, value, *args, **kwargs):
            text.append(value)
            return original(draw, position, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            proof = self.create()
        self.assertIn(assembly.LABEL, text)
        for name in self.names:
            self.assertIn(name, text)
        for label in ("Left", "Right", "Top", "Bottom"):
            self.assertEqual(text.count(label), 6)
        with Image.open(self.folder / proof["images"][0]["path"]) as sheet:
            # First tile begins below the page header; strips preserve top-to-bottom/left-to-right.
            for position, expected in [
                ((24, 64 + 210), (0, 128, 0)), ((396, 64 + 210), (255, 255, 0)),
                ((210, 64 + 398), (255, 0, 0)), ((210, 64 + 456), (0, 0, 255)),
            ]:
                pixel = sheet.getpixel(position)
                self.assertTrue(all(abs(a - b) < 20 for a, b in zip(pixel, expected)), (pixel, expected))

    def test_current_review_draft_and_approved_render_take_precedence(self):
        draft = self.review("Front")
        approved = self.review("Back", "c" * 32, True)
        proof = self.create()
        self.assertEqual(proof["sources"]["Front"]["path"], draft)
        self.assertEqual(proof["sources"]["Back"]["path"], approved)
        self.assertIn("Front", proof["missing_approvals"])
        self.assertNotIn("Back", proof["missing_approvals"])
        self.assertTrue(self.current(proof))

    def test_unrecorded_or_missing_review_render_falls_back_to_local(self):
        for recorded in (False, True):
            with self.subTest(recorded=recorded):
                relative = self.review(recorded=recorded, approved=True)
                if recorded:
                    (self.folder / relative).unlink()
                proof = self.create()
                self.assertEqual(proof["sources"]["Front"]["path"], "local/Front.png")
                self.assertIn("Front", proof["missing_approvals"])
                self.assertTrue(self.current(proof))

    def test_no_openai_or_old_review_fallback(self):
        old = self.review(approved=True)
        self.engine.job["reviews"]["Front"]["revision"] = "c" * 32
        self.panel("openai/Front.png", self.template.panels[0].size)
        proof = self.create()
        self.assertNotEqual(proof["sources"]["Front"]["path"], old)
        self.assertEqual(proof["sources"]["Front"]["path"], "local/Front.png")

    def test_wrong_size_rejected_without_publishing(self):
        self.panel("local/Wrist.png", (40, 40))
        before = copy.deepcopy(self.engine.job)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.engine.job, before)
        self.assertEqual(list((self.folder / "previews").iterdir()), [])

    def test_missing_panel_rejected(self):
        (self.folder / "local/Hem.png").unlink()
        before = copy.deepcopy(self.engine.job)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.engine.job, before)

    def test_same_path_content_mutation_invalidates_proof(self):
        proof = self.create()
        self.assertTrue(self.current(proof))
        self.panel("local/Front.png", self.template.panels[0].size, color="purple")
        self.assertFalse(self.current(proof))

    def test_new_saved_approval_revision_invalidates_even_stale_supplied_job(self):
        self.review(approved=True)
        proof = self.create()
        stale_job = self.engine.get_job(self.job_id)
        self.review(revision="c" * 32, approved=True)
        self.assertFalse(self.current(proof, stale_job))

    def test_review_render_mutation_and_deletion_invalidate(self):
        relative = self.review(approved=True)
        proof = self.create()
        self.panel(relative, self.template.panels[0].size, color="purple")
        self.assertFalse(self.current(proof))
        proof = self.create()
        (self.folder / relative).unlink()
        self.assertFalse(self.current(proof))

    def test_only_required_names_affect_review_revisions(self):
        proof = self.create()
        self.engine.job["reviews"]["DeletedPanel"] = {"revision": "d" * 32, "approved": True}
        self.assertTrue(self.current(proof))
        self.assertNotIn("DeletedPanel", self.create()["review_revisions"])
        forged = copy.deepcopy(proof)
        forged["review_revisions"]["DeletedPanel"] = "d" * 32
        self.assertFalse(self.current(forged))

    def test_panels_must_be_exact_and_templates_can_have_other_counts(self):
        proof = self.create()
        for names in (self.names[:-1], self.names + ["Extra"], self.names + ["Front"]):
            changed = copy.deepcopy(proof)
            changed["panels"] = names
            self.assertFalse(self.current(changed))
        self.template.panels = self.template.panels[:2]
        self.assertFalse(self.current(proof))
        smaller = self.create()
        self.assertEqual(smaller["panels"], ["Front", "Back"])
        self.assertEqual(smaller["kind"], "six-panel-proof")
        self.assertTrue(self.current(smaller))

    def test_more_than_six_panels_paginate(self):
        self.template.panels.append(SimpleNamespace(name="Hood", size=(80, 100)))
        self.panel("local/Hood.png", (80, 100))
        proof = self.create()
        self.assertEqual(len(proof["images"]), 2)
        self.assertIn("Hood", proof["sources"])
        self.assertTrue(self.current(proof))

    def test_persisted_manifest_and_outputs_must_exist_and_be_allowlisted(self):
        proof = self.create()
        original = copy.deepcopy(self.engine.job["artifacts"])
        for relative in (proof["manifest"], proof["images"][0]["path"], "local/Front.png"):
            with self.subTest(relative=relative):
                self.engine.job["artifacts"] = [
                    item for item in original if item["url"] != self.engine.url(self.job_id, self.folder / relative)]
                self.assertFalse(self.current(proof))
        self.engine.job["artifacts"] = original
        (self.folder / proof["images"][0]["path"]).unlink()
        self.assertFalse(self.current(proof))

    def test_manifest_and_proof_tampering_is_rejected(self):
        proof = self.create()
        forged = copy.deepcopy(proof)
        forged["sources"]["Front"]["sha256"] = "f" * 64
        self.assertFalse(self.current(forged))
        (self.folder / proof["manifest"]).write_text("{}")
        self.assertFalse(self.current(proof))
        for malformed in (None, {}, {"kind": "wrong"}, {"kind": "six-panel-proof", "panels": None}):
            self.assertFalse(self.current(malformed))

    def test_approval_report_alone_cannot_claim_approval(self):
        self.engine.job["reviews"]["Front"] = {"revision": "b" * 32, "approved": True}
        proof = self.create()
        self.assertIn("Front", proof["missing_approvals"])
        self.assertEqual(proof["sources"]["Front"]["path"], "local/Front.png")
        relative = self.review(approved=False)
        self.engine.job["reviews"]["Front"]["approved"] = True
        proof = self.create()
        self.assertEqual(proof["sources"]["Front"]["path"], relative)
        self.assertIn("Front", proof["missing_approvals"])

    def test_jpeg_inputs_and_unsafe_paths_and_symlinks(self):
        self.panel("local/Front.png", self.template.panels[0].size, format="JPEG")
        self.assertTrue(self.current(self.create()))
        path = self.folder / "local/Front.png"
        outside = self.root / "outside.jpg"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.create()
        self.assertTrue(outside.is_file())
        for job_id in ("../outside", "invalid"):
            with self.assertRaises(ValueError):
                assembly.create_assembly_proof(self.engine, job_id)

    def test_unallowlisted_local_is_rejected_and_no_report_source_is_trusted(self):
        self.engine.job["artifacts"] = [
            item for item in self.engine.job["artifacts"] if not item["url"].endswith("/local/Front.png")]
        self.engine.job["previews"] = [{"local_url": self.engine.url(self.job_id, self.folder / "local/Front.png")}]
        self.engine.job["filename"] = "../../outside.jpg"
        with self.assertRaises(ValueError):
            self.create()

    def test_40mp_panel_produces_a_compact_proof(self):
        self.template.panels = [SimpleNamespace(name="Front", size=(8000, 5000))]
        source = self.panel("local/Front.png", (8000, 5000), format="JPEG")
        digest = self.sha(source)
        proof = self.create()
        self.assertEqual(proof["dimensions"]["Front"], [8000, 5000])
        self.assertEqual(proof["images"][0]["size"], [840, 564])
        self.assertEqual(self.sha(source), digest)
        self.assertTrue(self.current(proof))

    def test_approval_change_without_new_revision_invalidates(self):
        relative = self.review(approved=True)
        proof = self.create()
        self.engine.job["reviews"]["Front"]["approved"] = False
        self.assertFalse(self.current(proof))
        self.engine.job["reviews"]["Front"]["approved"] = True
        (self.folder / relative).parent.joinpath("spec.json").write_text('{"approved": false}')
        self.assertFalse(self.current(proof))

    def test_contact_sheet_bytes_are_verified(self):
        proof = self.create()
        self.panel(proof["images"][0]["path"], tuple(proof["images"][0]["size"]), format="JPEG")
        self.assertFalse(self.current(proof))

    def test_invalid_dimensions_names_and_animated_images_rejected(self):
        original = copy.deepcopy(self.template.panels)
        for name, size in (("../escape", (80, 100)), ("Front", (8000, 8000)), ("Front", (0, 100))):
            self.template.panels[0] = SimpleNamespace(name=name, size=size)
            with self.assertRaises(ValueError):
                self.create()
        self.template.panels = original
        path = self.folder / "local/Front.png"
        size = self.template.panels[0].size
        with Image.new("RGB", size, "red") as a, Image.new("RGB", size, "blue") as b:
            a.save(path, save_all=True, append_images=[b])
        with self.assertRaises(ValueError):
            self.create()


if __name__ == "__main__":
    unittest.main()
