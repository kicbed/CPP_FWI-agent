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

function loadUiFunctions(options = {}) {
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

  if (options.libraries !== false) {
    sandbox.window.marked = {
      setOptions(markedOptions) {
        sandbox.markedOptions = markedOptions;
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
  }

  const source = [
    extractFunction('escapeHtml'),
    extractFunction('extractAnswer'),
    extractFunction('extractStreamText'),
    extractFunction('extractFwiPayload'),
    extractFunction('isFwiExecutionRequest'),
    extractFunction('renderMissingFwiReceiptHtml'),
    extractFunction('normalizeFwiJobId'),
    extractFunction('sanitizeFwiArtifactUrl'),
    extractFunction('renderKeyValueGrid'),
    extractFunction('renderFwiSubmissionHtml'),
    extractFunction('renderFwiResultHtml'),
    extractFunction('handleFwiImageError'),
    extractFunction('protectMathExpressions'),
    extractFunction('restoreProtectedMath'),
    extractFunction('renderMarkdownFallback'),
    extractFunction('renderMarkdown'),
    extractFunction('markMathFallback'),
    extractFunction('unwrapMathExpression'),
    `module.exports = {
      escapeHtml, extractAnswer, extractStreamText, extractFwiPayload,
      isFwiExecutionRequest, renderMissingFwiReceiptHtml,
      normalizeFwiJobId, sanitizeFwiArtifactUrl, renderFwiSubmissionHtml,
      renderFwiResultHtml, handleFwiImageError, protectMathExpressions,
      restoreProtectedMath, renderMarkdownFallback, renderMarkdown, markMathFallback,
      unwrapMathExpression
    };`,
  ].join('\n');

  vm.runInNewContext(source, sandbox);
  return { api: sandbox.module.exports, sandbox };
}

function loadSseParser() {
  const sandbox = {
    module: { exports: {} },
    TextDecoder,
    JSON,
    Error,
  };
  const source = [
    'const state = { activeRequest: null };',
    'function isRequestCurrent() { return true; }',
    "function createRequestAbortError() { const error = new Error('aborted'); error.name = 'AbortError'; return error; }",
    'function updateRequestContext() { return true; }',
    "function appendMessage() { return { remove() {} }; }",
    'function updateStreamMessage() {}',
    'function finalizeStreamMessage() {}',
    extractFunction('extractAnswer'),
    extractFunction('extractStreamText'),
    extractFunction('extractFwiPayload'),
    `async ${extractFunction('parseSSE')}`,
    'module.exports = { parseSSE };',
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return sandbox.module.exports;
}

function loadModeFunctions() {
  const elements = {
    modeHttp: { className: '' },
    modeGrpc: {
      className: '',
      disabled: true,
      title: '',
      attributes: {},
      setAttribute(name, value) { this.attributes[name] = String(value); },
    },
    modeTag: { textContent: '' },
    currentModeLabel: { textContent: '' },
  };
  const storage = new Map();
  const sandbox = {
    module: { exports: {} },
    document: { getElementById(id) { return elements[id] || null; } },
    localStorage: {
      setItem(key, value) { storage.set(key, String(value)); },
      getItem(key) { return storage.get(key) || null; },
    },
  };
  const source = [
    "const state = { mode: 'http', preferredMode: 'http', grpcAvailable: false, contextId: 'old-context', chatId: 'chat-1', chats: { 'chat-1': { mode: 'http' } }, streaming: false };",
    "const CONFIG = { http: { label: 'HTTP :5000' }, grpc: { label: 'gRPC :50052' } };",
    'const toasts = [];',
    'function showToast(message) { toasts.push(message); }',
    "function persistChatState() { localStorage.setItem('agent-mode', state.preferredMode || state.mode); }",
    "function abortActiveRequest() { state.streaming = false; return true; }",
    extractFunction('switchMode'),
    extractFunction('updateModeControls'),
    extractFunction('setGrpcAvailability'),
    'module.exports = { state, toasts, switchMode, setGrpcAvailability };',
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return { api: sandbox.module.exports, elements, storage };
}

function loadConversationStorageFunctions(withCrypto = true) {
  let uuidCounter = 0;
  const sandbox = {
    module: { exports: {} },
    console: { ...console, warn() {} },
    Date,
    Math,
  };
  if (withCrypto) {
    sandbox.crypto = {
      randomUUID() {
        uuidCounter += 1;
        return `00000000-0000-4000-8000-${String(uuidCounter).padStart(12, '0')}`;
      },
    };
  }
  const source = [
    'const CHAT_SCHEMA_VERSION = 2;',
    'const MAX_STORED_CHATS = 50;',
    'const MAX_STORED_MESSAGES = 200;',
    'const MAX_MESSAGE_CHARACTERS = 250000;',
    'let fallbackIdCounter = 0;',
    extractFunction('normalizeMode'),
    extractFunction('createStableId'),
    extractFunction('isSafeConversationId'),
    extractFunction('isSafeContextId'),
    extractFunction('safeStorageRead'),
    extractFunction('safeStorageWrite'),
    extractFunction('safeStorageRemove'),
    extractFunction('normalizeStoredMessage'),
    extractFunction('normalizeStoredFwiJob'),
    extractFunction('createChatRecord'),
    extractFunction('normalizeStoredChat'),
    extractFunction('parseStoredChatState'),
    extractFunction('buildClearedChatState'),
    `module.exports = {
      createStableId, createChatRecord, parseStoredChatState, buildClearedChatState,
      safeStorageRead, safeStorageWrite, safeStorageRemove
    };`,
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return sandbox.module.exports;
}

function loadHistoryRenderer() {
  class FakeElement {
    constructor(tagName) {
      this.tagName = tagName;
      this.children = [];
      this.dataset = {};
      this.attributes = {};
      this.textContent = '';
      this.className = '';
      this.listeners = {};
    }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); }
    replaceChildren(...children) { this.children = [...children]; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
  }

  const container = new FakeElement('div');
  const sandbox = {
    module: { exports: {} },
    container,
    document: {
      getElementById(id) {
        assert.equal(id, 'chatHistory');
        return container;
      },
      createElement(tagName) { return new FakeElement(tagName); },
    },
  };
  const source = [
    'const MAX_STORED_CHATS = 50;',
    extractFunction('isSafeConversationId'),
    `const state = {
      chatId: 'chat-safe',
      chats: {
        'chat-safe': { title: '<img src=x onerror=alert(1)>', mode: 'http', time: 2 },
        "bad'id": { title: 'must be skipped', mode: 'http', time: 1 }
      }
    };`,
    'const loaded = []; const deleted = [];',
    'function loadChat(id) { loaded.push(id); }',
    'function deleteChat(id) { deleted.push(id); }',
    extractFunction('renderHistory'),
    'renderHistory(); module.exports = { container, loaded, deleted };',
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return sandbox.module.exports;
}

function loadHttpDispatcher() {
  const sandbox = { module: { exports: {} } };
  const source = [
    'let streamCalls = 0;',
    'function bindRequestController(request) { request.controller = { signal: {} }; }',
    "async function sendHttpStream() { streamCalls += 1; throw new Error('stream failed after dispatch'); }",
    extractFunction('sendHttp'),
    'module.exports = { sendHttp, getStreamCalls: () => streamCalls };',
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return sandbox.module.exports;
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

  assert.doesNotMatch(sandbox.lastMarkedInput, /\$E=mc\^2\$/);
  assert.match(sandbox.lastMarkedInput, /MATHPLACEHOLDER/);
  assert.equal(sandbox.markedOptions.gfm, true);
  assert.equal(sandbox.markedOptions.breaks, true);
  assert.match(sandbox.lastSanitizedInput, /<script>alert/);
  assert.doesNotMatch(rendered, /<script>/);
  assert.match(rendered, /class="math-source math-inline"/);
  assert.match(rendered, /\$E=mc\^2\$/);
}

function testMarkdownFallbackRejectsActiveUrlSchemes() {
  const { api } = loadUiFunctions();
  const dangerous = api.renderMarkdownFallback(
    '[script](javascript:alert(1)) [data](data:text/html,boom) [file](file:///etc/passwd)',
  );
  assert.doesNotMatch(dangerous, /href="(?:javascript|data|file):/i);
  assert.match(dangerous, /script \(javascript:alert\(1\)\)/);

  const safe = api.renderMarkdownFallback('[docs](https://example.invalid/docs)');
  assert.match(safe, /href="https:\/\/example\.invalid\/docs"/);
  assert.match(safe, /rel="noopener noreferrer"/);
}

function testMathDelimitersCodeAndOrdinaryText() {
  const { api } = loadUiFunctions();
  const input = String.raw`行内 $E=mc^2$，括号形式 \(a+b\)。

$$\frac{1}{v^2}\frac{\partial^2 p}{\partial t^2}=\nabla^2p$$

\[J(m)=\frac{1}{2}\lVert d_{cal}-d_{obs}\rVert_2^2\]`;
  const protectedMath = api.protectMathExpressions(input);

  assert.equal(protectedMath.expressions.length, 4);
  assert.deepEqual(
    Array.from(protectedMath.expressions, expression => expression.display),
    [false, false, true, true],
  );
  assert.doesNotMatch(protectedMath.source, /E=mc\^2/);

  const rendered = api.renderMarkdown(input);
  assert.equal((rendered.match(/math-inline/g) || []).length, 2);
  assert.equal((rendered.match(/math-display/g) || []).length, 2);
  assert.match(rendered, /\\frac\{1\}\{v\^2\}/);

  const literalCode = api.protectMathExpressions('`$x^2$`\n```tex\n$$y^2$$\n```');
  assert.equal(literalCode.expressions.length, 0);
  assert.match(literalCode.source, /\$x\^2\$/);

  const ordinaryCurrency = api.protectMathExpressions('价格是 $5 元，预算约 $10 元。');
  assert.equal(ordinaryCurrency.expressions.length, 0);
  assert.equal(ordinaryCurrency.source, '价格是 $5 元，预算约 $10 元。');

  assert.equal(api.unwrapMathExpression(String.raw`$$x^2$$`), 'x^2');
  assert.equal(api.unwrapMathExpression(String.raw`\[x^2\]`), 'x^2');
  assert.equal(api.unwrapMathExpression(String.raw`\(x^2\)`), 'x^2');
  assert.equal(api.unwrapMathExpression('$x^2$'), 'x^2');
}

function testMathFallbackIsReadableAndCannotInjectHtml() {
  const { api } = loadUiFunctions({ libraries: false });
  const rendered = api.renderMarkdown(
    String.raw`公式 \(\frac{a}{b}\)，恶意内容 $<img src=x onerror=alert(1)>$`,
  );

  assert.match(rendered, /class="math-source math-inline"/);
  assert.match(rendered, /\\frac\{a\}\{b\}/);
  assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(rendered, /<img[\s>]/i);

  const node = {
    classList: { added: [], add(value) { this.added.push(value); } },
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  api.markMathFallback([node]);
  assert.deepEqual(node.classList.added, ['math-fallback-visible']);
  assert.match(node.attributes.title, /原始 TeX/);
}

function testKatexIsSafeLazyAndBounded() {
  assert.match(html, /KATEX_SCRIPT_URL = ['"]https:\/\/cdn\.jsdelivr\.net\/npm\/katex@0\.17\.0\/dist\/katex\.min\.js/);
  assert.match(html, /KATEX_STYLESHEET_URL = ['"]https:\/\/cdn\.jsdelivr\.net\/npm\/katex@0\.17\.0\/dist\/katex\.min\.css/);
  assert.doesNotMatch(html, /<script[^>]+src="[^"]*katex\.min\.js/);
  assert.doesNotMatch(html, /<link[^>]+href="[^"]*katex\.min\.css/);
  assert.match(html, /KATEX_SCRIPT_INTEGRITY = 'sha384-/);
  assert.match(html, /KATEX_STYLESHEET_INTEGRITY = 'sha384-/);
  const loader = extractFunction('ensureKatexLoaded');
  assert.match(loader, /createElement\('link'\)/);
  assert.match(loader, /crossOrigin = 'anonymous'/);
  const queue = extractFunction('queueMathTypeset');
  assert.match(queue, /querySelectorAll\('\.math-source'\)/);
  assert.match(queue, /ensureKatexLoaded\(\)/);
  assert.match(queue, /katex\.render/);
  assert.match(queue, /trust:\s*false/);
  assert.match(queue, /maxSize:\s*20/);
  assert.match(queue, /maxExpand:\s*1000/);
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

  const nestedToolEnvelope = {
    jsonrpc: '2.0',
    result: {
      output: {
        structuredContent: {
          tool_result: JSON.stringify(submitted),
        },
      },
    },
  };
  assert.equal(api.extractFwiPayload(nestedToolEnvelope).job_id, submitted.job_id);

  const rawSse = [
    'event: message',
    `data: ${JSON.stringify({ jsonrpc: '2.0', id: 'r1', result: { type: 'intent', intent: 'fwi' } })}`,
    '',
    `data: ${JSON.stringify({ jsonrpc: '2.0', id: 'r1', result: { payload: submitted } })}`,
    '',
  ].join('\n');
  assert.equal(api.extractFwiPayload(rawSse).job_id, submitted.job_id);
}

async function testSseTransportPreservesStructuredFwiReceipt() {
  const { parseSSE } = loadSseParser();
  const submitted = {
    type: 'fwi_job_submitted',
    job_id: 'fwi-sse-50',
    status: 'queued',
    status_url: '/fwi-artifacts/fwi-sse-50/status.json',
  };
  const events = [
    { jsonrpc: '2.0', id: 'req-sse', result: { type: 'chunk', content: '任务已提交' } },
    { jsonrpc: '2.0', id: 'req-sse', result: { type: 'tool_event', data: { output: submitted } } },
    { jsonrpc: '2.0', id: 'req-sse', result: { type: 'stream_end', message: '{}' } },
  ];
  const encoded = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''));
  const splitAt = Math.floor(encoded.length / 2);
  const chunks = [encoded.slice(0, splitAt), encoded.slice(splitAt)];
  const reader = {
    async read() {
      return chunks.length > 0 ? { done: false, value: chunks.shift() } : { done: true };
    },
    async cancel() {},
  };
  const response = { body: { getReader() { return reader; } } };
  const request = {
    requestId: 'req-sse',
    controller: { signal: { aborted: false }, abort() {} },
  };
  const result = await parseSSE(response, request);
  assert.equal(result.answer, '任务已提交');
  assert.equal(result.fwiPayload.job_id, 'fwi-sse-50');
  assert.equal(result.fwiPayload.status, 'queued');
}

function testFwiExecutionWithoutReceiptIsReportedHonestly() {
  const { api } = loadUiFunctions();
  assert.equal(
    api.isFwiExecutionRequest('使用 marmousi_94_288 在 CUDA 上运行 50 次迭代的 FWI 并展示结果'),
    true,
  );
  assert.equal(
    api.isFwiExecutionRequest('做一下marmousi的反演测试，迭代50次，完成后展示结果'),
    true,
  );
  assert.equal(api.isFwiExecutionRequest('帮我做一下 Marmousi 正演测试'), true);
  assert.equal(api.isFwiExecutionRequest('什么是 FWI？请解释其原理和公式'), false);
  assert.equal(api.isFwiExecutionRequest('请解释 cycle skipping'), false);

  const warning = api.renderMissingFwiReceiptHtml();
  assert.match(warning, /data-fwi-view="missing-receipt"/);
  assert.match(warning, /FWI 任务未提交/);
  assert.match(warning, /没有 <code>fwi_job_submitted<\/code>/);
  assert.match(warning, /job_id/);
  assert.match(warning, /不会把文本说明或 Python 代码当成已执行的反演/);

  const sidebarSource = extractFunction('renderExperimentHistory');
  assert.match(sidebarSource, /fwiReceiptMissing/);
  assert.match(sidebarSource, /未创建任务/);
  assert.match(sidebarSource, /不要把代码回复当作执行成功/);

  const sendSource = extractFunction('sendMessage');
  assert.match(sendSource, /response\.fwiPayload/);
  assert.match(sendSource, /isFwiExecutionRequest\(text\)/);
  assert.match(sendSource, /renderMissingFwiReceiptHtml\(\)/);
  assert.match(extractFunction('parseSSE'), /extractFwiPayload\(event\)/);
  assert.match(extractFunction('sendGrpc'), /fwiPayload:\s*extractFwiPayload\(data\)/);
  assert.match(extractFunction('sendHttpStream'), /fwiPayload:\s*extractFwiPayload\(data\)/);
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

function testHonestFwiControlsAndNoPlaceholderFeatures() {
  assert.match(html, /id="fwiQuickActions"/);
  assert.match(html, /Deepwave 2D Acoustic FWI/);
  assert.match(html, /最近 FWI 任务/);
  assert.match(html, /marmousi_94_288/);
  assert.match(html, /运行两次迭代的二维声学 FWI smoke test/);
  assert.match(html, /运行二维声学 FWI demo/);
  assert.match(html, /自定义迭代/);
  assert.match(html, /1–100 次/);
  assert.match(html, /运行 50 次迭代的 FWI/);
  assert.doesNotMatch(html, /CUDA-MPI FWI/);
  assert.doesNotMatch(html, /marmousi2 dry-run/);
  assert.doesNotMatch(html, /queued draft/);
  assert.doesNotMatch(html, /dry-run research state/);
  assert.doesNotMatch(html, /id="algorithmList"/);
}

function testGrpcModeIsHealthGatedAndFallsBack() {
  const { api, elements, storage } = loadModeFunctions();

  assert.equal(api.switchMode('grpc'), false);
  assert.equal(api.state.mode, 'http');
  assert.equal(storage.get('agent-mode'), 'grpc');
  assert.equal(api.state.contextId, 'old-context');
  assert.match(api.toasts.at(-1), /\.\/start\.sh --grpc/);

  api.setGrpcAvailability(true);
  assert.equal(elements.modeGrpc.disabled, false);
  assert.equal(elements.modeGrpc.attributes['aria-disabled'], 'false');
  assert.equal(api.switchMode('grpc'), true);
  assert.equal(api.state.mode, 'grpc');
  assert.equal(elements.modeTag.textContent, 'gRPC 桥');

  api.setGrpcAvailability(false, 'bridge offline');
  assert.equal(api.state.mode, 'http');
  assert.equal(elements.modeGrpc.disabled, true);
  assert.equal(storage.get('agent-mode'), 'grpc');
  assert.equal(api.state.contextId, 'old-context');
  assert.equal(api.toasts.at(-1), 'bridge offline');

  assert.match(html, /health\.status === 'ok'/);
  assert.match(html, /health\.transport === 'grpc'/);
}

function testConversationStorageRecoveryIdentityAndClearing() {
  const api = loadConversationStorageFunctions();
  const blockedStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { const error = new Error('quota'); error.name = 'QuotaExceededError'; throw error; },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(api.safeStorageRead(blockedStorage, 'agent-chats'), '');
  assert.equal(api.safeStorageWrite(blockedStorage, 'agent-chats', '{}'), false);
  assert.equal(api.safeStorageRemove(blockedStorage, 'agent-chats'), false);
  const corrupted = api.parseStoredChatState('{not valid json', '', 'grpc');
  assert.equal(corrupted.mode, 'grpc');
  assert.equal(Object.keys(corrupted.chats).length, 1);
  const recovered = corrupted.chats[corrupted.activeChatId];
  assert.match(recovered.id, /^chat-/);
  assert.match(recovered.contextId, /^web-/);
  assert.notEqual(recovered.contextId, 'default');

  const first = api.createChatRecord('http');
  const second = api.createChatRecord('http');
  assert.notEqual(first.id, second.id);
  assert.notEqual(first.contextId, second.contextId);

  const fallbackApi = loadConversationStorageFunctions(false);
  assert.notEqual(fallbackApi.createStableId('web'), fallbackApi.createStableId('web'));

  const envelope = JSON.stringify({
    schemaVersion: 2,
    activeChatId: first.id,
    mode: 'grpc',
    chats: {
      [first.id]: {
        ...first,
        title: 'restored chat',
        fwiJob: {
          type: 'fwi_job_submitted',
          job_id: 'fwi-safe-1',
          status: 'running',
          status_url: '/fwi-artifacts/fwi-safe-1/status.json',
        },
      },
      [second.id]: second,
    },
  });
  const restored = api.parseStoredChatState(envelope, '', 'http');
  assert.equal(restored.activeChatId, first.id);
  assert.equal(restored.mode, 'grpc');
  assert.equal(restored.chats[first.id].title, 'restored chat');
  assert.equal(restored.chats[first.id].fwiJob.job_id, 'fwi-safe-1');

  const cleared = api.buildClearedChatState(restored.chats, first.id, 'grpc', false);
  assert.equal(cleared.chats[first.id], undefined);
  assert.ok(cleared.chats[second.id]);
  assert.ok(cleared.chats[cleared.chatId]);
  assert.notEqual(cleared.contextId, first.contextId);

  const clearedAll = api.buildClearedChatState(restored.chats, first.id, 'http', true);
  assert.equal(Object.keys(clearedAll.chats).length, 1);
  assert.ok(clearedAll.chats[clearedAll.chatId]);

  const migratedLegacy = api.parseStoredChatState(JSON.stringify({
    legacy: { title: 'legacy', contextId: 'default', mode: 'http', messages: [], time: 1 },
  }), 'legacy', 'http');
  assert.notEqual(migratedLegacy.chats.legacy.contextId, 'default');
  assert.match(migratedLegacy.chats.legacy.contextId, /^web-/);

  const migratedInvalidContext = api.parseStoredChatState(JSON.stringify({
    legacy: { title: 'legacy', contextId: 'redis:key', mode: 'http', messages: [], time: 1 },
  }), 'legacy', 'http');
  assert.notEqual(migratedInvalidContext.chats.legacy.contextId, 'redis:key');
  assert.match(migratedInvalidContext.chats.legacy.contextId, /^web-/);
}

function testHistoryUsesSafeDomAndDataAttributes() {
  const { container } = loadHistoryRenderer();
  assert.equal(container.children.length, 1);
  const row = container.children[0];
  const loadButton = row.children[0];
  const deleteButton = row.children[1];
  assert.equal(loadButton.dataset.chatId, 'chat-safe');
  assert.equal(deleteButton.dataset.chatId, 'chat-safe');
  assert.equal(loadButton.children[1].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(typeof loadButton.listeners.click, 'function');
  assert.equal(typeof deleteButton.listeners.click, 'function');

  const historySource = extractFunction('renderHistory');
  assert.doesNotMatch(historySource, /onclick=.*loadChat/);
  assert.match(historySource, /dataset\.chatId/);
  assert.match(historySource, /addEventListener/);
}

function testRequestsAreBoundAndStreamFailuresAreNotReplayed() {
  const sendSource = extractFunction('sendMessage');
  const httpSource = extractFunction('sendHttp');
  const streamSource = extractFunction('sendHttpStream');
  const parserSource = extractFunction('parseSSE');
  const modeSource = extractFunction('switchMode');
  const fwiSource = extractFunction('handleFwiPayload');

  assert.match(sendSource, /chatId: chat\.id/);
  assert.match(sendSource, /contextId: chat\.contextId/);
  assert.match(sendSource, /appendStoredMessage\(request\.chatId, 'user'/);
  assert.match(sendSource, /isRequestCurrent\(request\)/);
  assert.match(sendSource, /'cancelled' : 'error'/);
  assert.match(httpSource, /return sendHttpStream\(text, request\)/);
  assert.doesNotMatch(httpSource, /sendHttpSync/);
  assert.doesNotMatch(html, /function sendHttpSync\(/);
  assert.match(streamSource, /contextId: request\.contextId/);
  assert.doesNotMatch(streamSource, /contextId: state\.contextId/);
  assert.match(parserSource, /未自动重发以避免重复执行任务/);
  assert.match(parserSource, /reader\.cancel\(\)/);
  assert.doesNotMatch(modeSource, /contextId\s*=\s*null/);
  assert.match(fwiSource, /persistFwiJobForChat\(ownerChatId/);
  assert.match(sendSource, /outcome_unknown/);
  assert.doesNotMatch(extractFunction('finalizeStreamMessage'), /processFwiPayloadFromAnswer/);
}

async function testHttpFailureIsNotAutomaticallyReplayed() {
  const api = loadHttpDispatcher();
  await assert.rejects(
    api.sendHttp('submit one FWI job', { requestId: 'req-1' }),
    /stream failed after dispatch/,
  );
  assert.equal(api.getStreamCalls(), 1);
}

function testChatAndSystemTextAreEscaped() {
  const appendSource = extractFunction('appendMessage');
  assert.match(appendSource, /escapeHtml\(content\)/);
  assert.match(appendSource, /queueMathTypeset\(body\)/);
  assert.match(extractFunction('finalizeStreamMessage'), /queueMathTypeset\(body\)/);
}

function testEmbeddingStatusUsesSameOriginHealthProxy() {
  const source = extractFunction('checkEmbeddingStatus');
  assert.match(source, /fetch\('\/api\/embedding-health'/);
  assert.doesNotMatch(source, /localhost:6000|127\.0\.0\.1:6000/);
  assert.match(source, /未启用（FWI 知识库仍可用）/);
  assert.match(source, /health\.model_loaded === true/);
}

async function main() {
  testStreamChunkExtraction();
  testMarkdownRendererUsesLibraryAndSanitizer();
  testMarkdownFallbackRejectsActiveUrlSchemes();
  testMathDelimitersCodeAndOrdinaryText();
  testMathFallbackIsReadableAndCannotInjectHtml();
  testKatexIsSafeLazyAndBounded();
  testFwiSubmittedAndWrappedResultParsing();
  await testSseTransportPreservesStructuredFwiReceipt();
  testFwiExecutionWithoutReceiptIsReportedHonestly();
  testFwiResultMetricsImagesAndEscaping();
  testFwiMissingImageFallback();
  testHonestFwiControlsAndNoPlaceholderFeatures();
  testGrpcModeIsHealthGatedAndFallsBack();
  testConversationStorageRecoveryIdentityAndClearing();
  testHistoryUsesSafeDomAndDataAttributes();
  testRequestsAreBoundAndStreamFailuresAreNotReplayed();
  await testHttpFailureIsNotAutomaticallyReplayed();
  testChatAndSystemTextAreEscaped();
  testEmbeddingStatusUsesSameOriginHealthProxy();
  console.log('ui message rendering tests passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
