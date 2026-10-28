'use strict';

// Manifest text sizes and baselines are document pixels, not typographic points.
// Callers with point sizes must convert once: pixels = points * dpi / 72.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readPsd, writePsdBuffer } = require('ag-psd');

function dimensions(value, label) {
  assert(value && typeof value === 'object', `${label} must be an object`);
  for (const key of ['width', 'height']) {
    assert(Number.isInteger(value[key]) && value[key] > 0 && value[key] <= 30000,
      `${label}.${key} must be an integer in 1..30000`);
  }
  assert(value.width * value.height <= 50000000, `${label} exceeds 50M pixels`);
}

function coordinate(value, label, integer = false) {
  assert(Number.isFinite(value) && Math.abs(value) <= 30000 && (!integer || Number.isInteger(value)),
    `${label} must be ${integer ? 'an integer' : 'finite'} within +/-30000 pixels`);
}

function exportPsd(inputFile, outputFile) {
  const manifestFile = fs.realpathSync(inputFile);
  const folder = path.dirname(manifestFile);
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const inputs = new Set([manifestFile]);
  dimensions(manifest, 'manifest');
  // ag-psd stores resolution as signed 16.16 fixed point.
  assert(Number.isFinite(manifest.dpi) && manifest.dpi >= 1 / 65536 && manifest.dpi < 32768,
    'dpi must be positive and representable as signed 16.16');
  assert(Array.isArray(manifest.layers) && manifest.layers.length >= 1 && manifest.layers.length <= 20,
    'layers must contain 1..20 layers');
  dimensions(manifest.composite, 'composite');
  assert(manifest.composite.width === manifest.width && manifest.composite.height === manifest.height,
    'composite dimensions must match manifest');

  function pixels(value, label) {
    assert(typeof value.rgba_file === 'string' && value.rgba_file.length > 0,
      `${label}.rgba_file must be a file name`);
    const filename = path.resolve(folder, value.rgba_file);
    assert(path.dirname(filename) === folder, `${label}.rgba_file must be in the manifest folder`);
    assert(!fs.lstatSync(filename).isSymbolicLink(), `${label}.rgba_file must not be a symlink`);
    // O_NOFOLLOW also rejects a symlink swapped in between lstat and open.
    const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    try {
      const stat = fs.fstatSync(fd);
      assert(stat.isFile(), `${label}.rgba_file must be a regular file`);
      const length = value.width * value.height * 4;
      assert(stat.size === length, `${label}.rgba_file must contain exactly ${length} RGBA8 bytes`);
      const data = fs.readFileSync(fd);
      assert(data.length === length, `${label}.rgba_file changed size while reading`);
      inputs.add(filename);
      return { width: value.width, height: value.height, data };
    } finally {
      fs.closeSync(fd);
    }
  }

  // children follow the manifest's Photoshop top-to-bottom order.
  const children = manifest.layers.map((layer, index) => {
    const label = `layers[${index}]`;
    dimensions(layer, label);
    assert(typeof layer.name === 'string' && layer.name.length > 0, `${label}.name must be nonempty`);
    coordinate(layer.left, `${label}.left`, true);
    coordinate(layer.top, `${label}.top`, true);
    assert(layer.hidden === undefined || typeof layer.hidden === 'boolean', `${label}.hidden must be boolean`);
    const child = {
      name: layer.name, left: layer.left, top: layer.top,
      hidden: layer.hidden ?? false, imageData: pixels(layer, label),
    };
    if (layer.text !== undefined) {
      const text = layer.text;
      assert(text && typeof text === 'object', `${label}.text must be an object`);
      assert(typeof text.value === 'string', `${label}.text.value must be a string`);
      assert(typeof text.font_name === 'string' && text.font_name.trim().length > 0,
        `${label}.text.font_name must be a PostScript font name`);
      assert(Number.isFinite(text.font_size) && text.font_size > 0 && text.font_size <= 30000,
        `${label}.text.font_size must be in (0, 30000] pixels`);
      assert(typeof text.color === 'string' && /^#[0-9a-f]{6}$/i.test(text.color),
        `${label}.text.color must be #RRGGBB`);
      coordinate(text.baseline_x, `${label}.text.baseline_x`);
      coordinate(text.baseline_y, `${label}.text.baseline_y`);
      child.text = {
        text: text.value.replace(/\r\n?/g, '\n'),
        shapeType: 'point', pointBase: [0, 0], orientation: 'horizontal',
        // The baseline is absolute; layer.left/top belong only to its raster cache.
        transform: [1, 0, 0, 1, text.baseline_x, text.baseline_y],
        paragraphStyle: { justification: 'left' },
        style: {
          font: { name: text.font_name }, fontSize: text.font_size,
          fillColor: {
            r: parseInt(text.color.slice(1, 3), 16),
            g: parseInt(text.color.slice(3, 5), 16),
            b: parseInt(text.color.slice(5, 7), 16),
          },
        },
      };
    }
    return child;
  });
  const psd = {
    width: manifest.width, height: manifest.height, children,
    imageData: pixels(manifest.composite, 'composite'),
    imageResources: {
      resolutionInfo: {
        horizontalResolution: manifest.dpi, verticalResolution: manifest.dpi,
        horizontalResolutionUnit: 'PPI', verticalResolutionUnit: 'PPI',
        widthUnit: 'Inches', heightUnit: 'Inches',
      },
    },
  };
  const output = path.resolve(outputFile);
  const destination = path.join(fs.realpathSync(path.dirname(output)), path.basename(output));
  assert(!inputs.has(destination), 'output must not overwrite the manifest or an RGBA input');
  const bytes = writePsdBuffer(psd, { noBackground: true, trimImageData: false, generateThumbnail: false });
  const tempFolder = fs.mkdtempSync(path.join(path.dirname(destination), '.psd-export-'));
  try {
    const tempFile = path.join(tempFolder, 'output.psd');
    fs.writeFileSync(tempFile, bytes, { flag: 'wx', mode: 0o600 });
    const verified = readPsd(fs.readFileSync(tempFile), {
      skipLayerImageData: true, skipCompositeImageData: true, skipThumbnail: true,
    });
    assert.equal(verified.width, manifest.width, 'PSD width roundtrip');
    assert.equal(verified.height, manifest.height, 'PSD height roundtrip');
    const resolution = verified.imageResources?.resolutionInfo;
    for (const axis of ['horizontal', 'vertical']) {
      assert(Math.abs(resolution?.[`${axis}Resolution`] - manifest.dpi) < 1 / 65536,
        `PSD ${axis} DPI roundtrip`);
      assert.equal(resolution[`${axis}ResolutionUnit`], 'PPI');
    }
    assert.deepEqual(verified.children?.map(layer => layer.name), children.map(layer => layer.name),
      'PSD layer order/name roundtrip');
    const layers = verified.children.map((layer, index) => {
      const expected = children[index];
      assert.deepEqual([layer.left, layer.top, layer.right - layer.left, layer.bottom - layer.top],
        [expected.left, expected.top, expected.imageData.width, expected.imageData.height], 'PSD raster bounds roundtrip');
      assert.equal(layer.hidden, expected.hidden, 'PSD visibility roundtrip');
      const result = {
        name: layer.name, left: layer.left, top: layer.top,
        width: layer.right - layer.left, height: layer.bottom - layer.top, hidden: layer.hidden,
      };
      if (expected.text) {
        assert.equal(layer.text?.text, expected.text.text, 'PSD text value roundtrip');
        assert.equal(layer.text?.style?.font?.name, expected.text.style.font.name, 'PSD text font roundtrip');
        assert(Math.abs(layer.text.style.fontSize - expected.text.style.fontSize) < 0.00001, 'PSD font size roundtrip');
        assert.deepEqual(layer.text.transform, expected.text.transform, 'PSD baseline transform roundtrip');
        assert.equal(layer.text.shapeType, 'point', 'PSD point text roundtrip');
        const color = layer.text.style.fillColor;
        for (const channel of ['r', 'g', 'b']) {
          assert.equal(Math.round(color[channel]), expected.text.style.fillColor[channel], 'PSD text color roundtrip');
        }
        result.text = {
          value: layer.text.text, font_name: layer.text.style.font.name, font_size: layer.text.style.fontSize,
          color: manifest.layers[index].text.color,
          baseline_x: layer.text.transform[4], baseline_y: layer.text.transform[5],
        };
      } else {
        assert.equal(layer.text, undefined, 'raster layer must remain raster');
      }
      return result;
    });
    fs.renameSync(tempFile, destination);
    return { verified: true, width: verified.width, height: verified.height, dpi: resolution.horizontalResolution, layers };
  } finally {
    fs.rmSync(tempFolder, { recursive: true, force: true });
  }
}

module.exports = { exportPsd };

if (require.main === module) {
  try {
    assert(process.argv.length === 4, 'Usage: node psd_export.cjs input.json output.psd');
    process.stdout.write(`${JSON.stringify(exportPsd(process.argv[2], process.argv[3]))}\n`);
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ error: error.message })}\n`);
    process.exitCode = 1;
  }
}
