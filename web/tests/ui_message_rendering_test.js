const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(repoRoot, 'web', 'index.html'), 'utf8');

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = html.indexOf(marker);
  assert.notEqual(start, -1, `${name} function is missing`);

  const braceStart = html.indexOf('{', start);
  assert.notEqual(braceStart, -1, `${name} function has no body`);

  let depth = 0;
  for (let index = braceStart; index < html.length; index += 1) {
    const char = html[index];
    if (char === '{') depth += 1;
    if (char === '}') depth -= 1;
    if (depth === 0) return html.slice(start, index + 1);
  }
  throw new Error(`${name} function body is incomplete`);
}

function loadUiFunctions() {
  const sandbox = {
    module: { exports: {} },
    console,
    setTimeout,
    clearTimeout,
    window: {},
    document: {
      createElement() {
        return {
          _text: '',
          set textContent(value) {
            this._text = String(value);
          },
          get innerHTML() {
            return this._text
              .replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;');
          },
        };
      },
    },
  };

  sandbox.window.marked = {
    setOptions(options) {
      sandbox.markedOptions = options;
    },
    parse(markdown) {
      sandbox.lastMarkedInput = markdown;
      return `<article>${markdown}</article><script>alert('x')</script>`;
    },
  };
  sandbox.window.DOMPurify = {
    sanitize(rendered) {
      sandbox.lastSanitizedInput = rendered;
      return rendered.replace(/<script[\s\S]*?<\/script>/g, '');
    },
  };

  const source = [
    extractFunction('escapeHtml'),
    extractFunction('extractAnswer'),
    extractFunction('extractStreamText'),
    extractFunction('extractFwiPayload'),
    extractFunction('normalizeFwiJobId'),
    extractFunction('sanitizeFwiArtifactUrl'),
    extractFunction('renderKeyValueGrid'),
    extractFunction('renderFwiSubmissionHtml'),
    extractFunction('renderFwiResultHtml'),
    extractFunction('handleFwiImageError'),
    extractFunction('renderMarkdownFallback'),
    extractFunction('renderMarkdown'),
    `module.exports = {
      escapeHtml, extractAnswer, extractStreamText, extractFwiPayload,
      normalizeFwiJobId, sanitizeFwiArtifactUrl, renderFwiSubmissionHtml,
      renderFwiResultHtml, handleFwiImageError, renderMarkdownFallback, renderMarkdown
    };`,
  ].join('\n');

  vm.runInNewContext(source, sandbox);
  return { api: sandbox.module.exports, sandbox };
}

function testStreamChunkExtraction() {
  const { api } = loadUiFunctions();

  assert.equal(
    api.extractStreamText({ type: 'chunk', content: 'HTTP chunk text' }, ''),
    'HTTP chunk text',
  );

  const finalMessage = JSON.stringify({
    role: 'agent',
    parts: [{ kind: 'text', text: 'final fallback text' }],
  });
  assert.equal(
    api.extractStreamText({ type: 'stream_end', message: finalMessage }, ''),
    'final fallback text',
  );
  assert.equal(
    api.extractStreamText({ type: 'stream_end', message: finalMessage }, 'already streamed'),
    '',
  );
}

function testMarkdownRendererUsesLibraryAndSanitizer() {
  const { api, sandbox } = loadUiFunctions();

  const rendered = api.renderMarkdown('公式 $E=mc^2$\\n\\n- item');

  assert.match(sandbox.lastMarkedInput, /\$E=mc\^2\$/);
  assert.equal(sandbox.markedOptions.gfm, true);
  assert.equal(sandbox.markedOptions.breaks, true);
  assert.match(sandbox.lastSanitizedInput, /<script>alert/);
  assert.doesNotMatch(rendered, /<script>/);
}

function testMathJaxConfigSkipsCode() {
  assert.match(html, /tex-chtml\.js/);
  assert.match(html, /skipHtmlTags:[\s\S]*code/);
  assert.match(html, /inlineMath:[\s\S]*\$/);
}

function makeFwiManifest(overrides = {}) {
  const jobId = 'fwi-20260714-demo';
  const figureIds = [
    'true_model',
    'initial_model',
    'inverted_model',
    'model_error',
    'shot_gathers',
    'loss_curve',
  ];
  return {
    type: 'fwi_result',
    schema_version: '1',
    job_id: jobId,
    status: 'succeeded',
    summary: 'Synthetic FWI result',
    metrics: {
      model_shape: [94, 288],
      dx_m: 10,
      dz_m: 10,
      source_frequency_hz: 8,
      dt_s: 0.001,
      nt: 2000,
      n_shots: 3,
      n_receivers: 96,
      iterations: 2,
      initial_loss: 12.5,
      final_loss: 8.5,
      loss_reduction_fraction: 0.32,
      initial_model_relative_l2: 0.18,
      final_model_relative_l2: 0.15,
      observed_predicted_relative_l2: 0.12,
      device_name: 'NVIDIA test device',
      elapsed_seconds: 4.2,
    },
    figures: figureIds.map(id => ({
      id,
      title: id === 'true_model' ? '<img src=x onerror="alert(1)">' : id,
      url: `/fwi-artifacts/${jobId}/figures/${id}.png`,
      mime_type: 'image/png',
    })),
    ...overrides,
  };
}

function testFwiSubmittedAndWrappedResultParsing() {
  const { api } = loadUiFunctions();
  const submitted = {
    type: 'fwi_job_submitted',
    job_id: 'fwi-20260714-demo',
    status: 'queued',
    status_url: '/fwi-artifacts/fwi-20260714-demo/status.json',
  };
  const mcpEnvelope = {
    result: {
      content: [{ type: 'text', text: JSON.stringify(submitted) }],
    },
  };
  assert.deepEqual(
    JSON.parse(JSON.stringify(api.extractFwiPayload(mcpEnvelope))),
    submitted,
  );

  const submittedHtml = api.renderFwiSubmissionHtml(submitted);
  assert.match(submittedHtml, /data-fwi-view="submitted"/);
  assert.match(submittedHtml, /fwi-20260714-demo/);
  assert.match(submittedHtml, /refreshFwiStatusForCurrentJob/);

  const manifest = makeFwiManifest();
  const naturalLanguage = `任务完成，结果如下：\n${JSON.stringify(manifest)}\n请查看图片。`;
  assert.equal(api.extractFwiPayload(naturalLanguage).type, 'fwi_result');
  assert.equal(api.extractFwiPayload('什么是 FWI？'), null);
}

function testFwiResultMetricsImagesAndEscaping() {
  const { api } = loadUiFunctions();
  const manifest = makeFwiManifest();
  const rendered = api.renderFwiResultHtml(manifest);

  assert.match(rendered, /94 × 288/);
  assert.match(rendered, /32\.00%/);
  assert.match(rendered, /data-fwi-figure="shot_gathers"/);
  assert.match(rendered, /\/fwi-artifacts\/fwi-20260714-demo\/figures\/loss_curve\.png/);
  assert.match(rendered, /onerror="handleFwiImageError\(this\)"/);
  assert.doesNotMatch(rendered, /<img src=x onerror="alert\(1\)">/);
  assert.match(rendered, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);

  const unsafe = makeFwiManifest({
    figures: [{
      id: 'true_model',
      title: 'unsafe URL',
      url: 'https://example.invalid/stolen.png',
    }],
  });
  const unsafeRendered = api.renderFwiResultHtml(unsafe);
  assert.match(unsafeRendered, /已拒绝加载/);
  assert.doesNotMatch(unsafeRendered, /example\.invalid/);
  assert.match(unsafeRendered, /manifest 未提供该图片/);
}

function testFwiMissingImageFallback() {
  const { api } = loadUiFunctions();
  const removedClasses = [];
  const addedClasses = [];
  const errorElement = {
    classList: {
      remove(value) { removedClasses.push(value); },
    },
  };
  const image = {
    classList: {
      add(value) { addedClasses.push(value); },
    },
    parentElement: {
      querySelector(selector) {
        assert.equal(selector, '.fwi-image-error');
        return errorElement;
      },
    },
  };

  api.handleFwiImageError(image);
  assert.deepEqual(addedClasses, ['hidden']);
  assert.deepEqual(removedClasses, ['hidden']);
}

testStreamChunkExtraction();
testMarkdownRendererUsesLibraryAndSanitizer();
testMathJaxConfigSkipsCode();
testFwiSubmittedAndWrappedResultParsing();
testFwiResultMetricsImagesAndEscaping();
testFwiMissingImageFallback();
console.log('ui message rendering tests passed');
