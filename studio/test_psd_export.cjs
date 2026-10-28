'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { readPsd, initializeCanvas } = require('ag-psd');
const { exportPsd } = require('./psd_export.cjs');

const folder = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'psd-export-test-')));
try {
  const raw = (name, data) => {
    fs.writeFileSync(path.join(folder, name), Buffer.from(data));
    return name;
  };
  const background = Buffer.from(Array(12).fill([24, 48, 72, 255]).flat());
  const logo = Buffer.from([231, 17, 39, 0, 8, 120, 240, 128, 250, 60, 3, 255, 9, 8, 7, 0]);
  const textPixels = Buffer.from([20, 40, 60, 255, 20, 40, 60, 0]);
  const manifest = {
    width: 4, height: 3, dpi: 300,
    layers: [
      { name: 'Editable text Tiếng Việt', left: 1, top: 0, width: 2, height: 1,
        rgba_file: raw('text.rgba', textPixels),
        text: { value: 'Hello\nPOD 👕\n', font_name: 'ArialMT', font_size: 12.5, color: '#14283C',
          baseline_x: 0.25, baseline_y: 2.75 } },
      { name: 'Transparent logo', left: -1, top: 1, width: 2, height: 2,
        rgba_file: raw('logo.rgba', logo), hidden: true },
      { name: 'Background', left: 0, top: 0, width: 4, height: 3,
        rgba_file: raw('background.rgba', background) },
    ],
    composite: { width: 4, height: 3, rgba_file: raw('composite.rgba', background) },
  };
  const input = path.join(folder, 'input.json');
  const output = path.join(folder, 'result.psd');
  const save = value => fs.writeFileSync(input, JSON.stringify(value));
  save(manifest);
  const cli = (...args) => spawnSync(process.execPath, [path.join(__dirname, 'psd_export.cjs'), ...args],
    { encoding: 'utf8' });
  const run = cli(input, output);
  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.stderr, '');
  const summary = JSON.parse(run.stdout);
  assert.equal(summary.verified, true);
  assert.deepEqual([summary.width, summary.height, summary.dpi], [4, 3, 300]);
  assert.deepEqual(summary.layers.map(layer => layer.name), manifest.layers.map(layer => layer.name));
  assert.deepEqual(summary.layers[0].text, manifest.layers[0].text);
  assert(!/imageData|canvas|rgba_file/.test(run.stdout), 'summary must not contain pixels');

  const bytes = fs.readFileSync(output);
  assert.equal(bytes.subarray(0, 4).toString(), '8BPS');
  // First prove the required structure-only path works before any pixel allocator exists.
  const structure = readPsd(bytes, {
    skipLayerImageData: true, skipCompositeImageData: true, skipThumbnail: true,
  });
  assert.equal(structure.imageData, undefined);
  assert(structure.children.every(layer => !layer.imageData && !layer.canvas));
  assert.equal(structure.imageResources.resolutionInfo.verticalResolution, 300);
  assert.equal(structure.children[0].text.style.font.name, 'ArialMT');
  assert.deepEqual(structure.children[0].text.transform, [1, 0, 0, 1, 0.25, 2.75]);
  assert.equal(structure.children[1].hidden, true);

  // Decode tiny fixtures with plain typed arrays, never a canvas or extra dependency.
  initializeCanvas(() => { throw new Error('Canvas must not be used'); },
    (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }));
  const decoded = readPsd(bytes, { useImageData: true, skipThumbnail: true });
  for (const [index, expected] of [textPixels, logo, background].entries()) {
    assert.deepEqual(Buffer.from(decoded.children[index].imageData.data), expected, `layer ${index} pixels`);
  }
  assert.deepEqual(Buffer.from(decoded.imageData.data), background);

  // Successful replacement is atomic; invalid exports preserve the previous destination.
  assert.equal(exportPsd(input, output).verified, true);
  const previous = fs.readFileSync(output);
  function rejects(change, pattern) {
    const invalid = structuredClone(manifest);
    change(invalid);
    save(invalid);
    assert.throws(() => exportPsd(input, output), pattern);
    assert.deepEqual(fs.readFileSync(output), previous);
    assert(!fs.readdirSync(folder).some(name => name.startsWith('.psd-export-')), 'temporary files cleaned');
  }
  rejects(m => { m.width = 30001; }, /30000/);
  rejects(m => { m.width = 10000; m.height = 5001; }, /50M/);
  rejects(m => { m.width = 1.5; }, /integer/);
  rejects(m => { m.height = 0; }, /integer/);
  rejects(m => { m.dpi = 0; }, /dpi/);
  rejects(m => { m.dpi = 32768; }, /dpi/);
  rejects(m => { m.layers = []; }, /1\.\.20/);
  rejects(m => { m.layers = Array(21).fill(m.layers[0]); }, /1\.\.20/);
  rejects(m => { m.composite.width = 3; }, /composite dimensions/);
  rejects(m => { m.layers[1].width = 30001; }, /30000/);
  rejects(m => { m.layers[1].width = 10000; m.layers[1].height = 5001; }, /50M/);
  rejects(m => { m.layers[0].left = 0.5; }, /integer/);
  rejects(m => { m.layers[0].hidden = 'false'; }, /boolean/);
  rejects(m => { m.layers[0].text.font_name = ''; }, /font_name/);
  rejects(m => { m.layers[0].text.font_size = -1; }, /font_size/);
  rejects(m => { m.layers[0].text.color = '#GG0000'; }, /#RRGGBB/);
  rejects(m => { m.layers[0].text.baseline_x = null; }, /finite/);
  raw('short.rgba', [0, 0, 0, 0]);
  raw('long.rgba', Buffer.alloc(9));
  rejects(m => { m.layers[0].rgba_file = 'short.rgba'; }, /exactly 8/);
  rejects(m => { m.layers[0].rgba_file = 'long.rgba'; }, /exactly 8/);
  rejects(m => { m.composite.rgba_file = 'short.rgba'; }, /exactly 48/);
  rejects(m => { m.layers[0].rgba_file = '../outside.rgba'; }, /manifest folder/);
  const outsideCli = cli(input, output);
  assert.equal(outsideCli.status, 1);
  assert.equal(outsideCli.stdout, '');
  assert.match(JSON.parse(outsideCli.stderr).error, /manifest folder/);
  assert.deepEqual(fs.readFileSync(output), previous);
  rejects(m => { m.composite.rgba_file = path.join(path.dirname(folder), 'outside.rgba'); }, /manifest folder/);
  fs.symlinkSync(path.join(folder, 'text.rgba'), path.join(folder, 'link.rgba'));
  rejects(m => { m.layers[0].rgba_file = 'link.rgba'; }, /symlink/);
  fs.symlinkSync(os.tmpdir(), path.join(folder, 'linked-directory'));
  rejects(m => { m.layers[0].rgba_file = 'linked-directory/outside.rgba'; }, /manifest folder/);
  rejects(m => { m.layers[0].rgba_file = '.'; }, /manifest folder/);
  save(manifest);
  assert.throws(() => exportPsd(input, input), /must not overwrite/);
  assert.throws(() => exportPsd(input, path.join(folder, 'text.rgba')), /must not overwrite/);
  fs.symlinkSync(folder, path.join(folder, 'folder-alias'));
  assert.throws(() => exportPsd(input, path.join(folder, 'folder-alias', 'text.rgba')), /must not overwrite/);
  assert.deepEqual(fs.readFileSync(path.join(folder, 'text.rgba')), textPixels);
  const blockedOutput = path.join(folder, 'existing-directory');
  fs.mkdirSync(blockedOutput);
  assert.throws(() => exportPsd(input, blockedOutput));
  assert(fs.statSync(blockedOutput).isDirectory());
  assert(!fs.readdirSync(folder).some(name => name.startsWith('.psd-export-')));
  const usage = cli();
  assert.equal(usage.status, 1);
  assert.equal(usage.stdout, '');
  assert.match(JSON.parse(usage.stderr).error, /Usage:/);
  save({ ...manifest, dpi: 72 });
  const at72 = exportPsd(input, output);
  assert.equal(at72.dpi, 72);
  assert.equal(at72.layers[0].text.font_size, 12.5, 'DPI must not double-scale text');
  assert.equal(at72.layers[0].text.baseline_y, 2.75);
  process.stdout.write('PSD export tests passed (CLI, native text, RGBA caches, 300 DPI, validation, atomic writes).\n');
} finally {
  fs.rmSync(folder, { recursive: true, force: true });
}
