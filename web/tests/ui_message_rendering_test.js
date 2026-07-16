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

function loadGuidedFunctions() {
  const sandbox = {
    module: { exports: {} },
    document: {
      createElement() {
        return {
          _text: '',
          set textContent(value) { this._text = String(value); },
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
  const names = [
    'escapeHtml',
    'isSafeGuidedOpaqueId',
    'isSafeGuidedIdentifier',
    'isSafeGuidedVersion',
    'isSafeGuidedPlanHash',
    'isSafeGuidedCsrfToken',
    'boundedGuidedText',
    'guidedApiPath',
    'hasGuidedUnsupportedForwardIntent',
    'guidedOverridesFromExecutionText',
    'normalizeGuidedSession',
    'normalizeGuidedCatalog',
    'guidedIntegerValue',
    'guidedLearningRateValue',
    'guidedLearningRateFromMilli',
    'validateGuidedFwiForm',
    'makeGuidedForm',
    'normalizeGuidedPlanOutputs',
    'expectedGuidedFwiPlanOutputs',
    'hasExactGuidedFwiPlanOutputs',
    'normalizeGuidedTimeoutProjection',
    'normalizeGuidedReconciliationProjection',
    'normalizeGuidedTaskProjection',
    'isGuidedReviewReady',
    'isGuidedApprovedSubmitPending',
    'isGuidedApprovalCompleted',
    'normalizeGuidedArtifacts',
    'isSafeGuidedBlobUrl',
    'guidedDispatchExplanation',
    'guidedReconciliationExplanation',
    'guidedCancellationExplanation',
    'guidedTimeoutExplanation',
    'renderGuidedArtifactsHtml',
  ];
  const source = [
    "const GUIDED_API_PREFIX = '/api/scientific-runtime/v1';",
    ...names.map(extractFunction),
    `module.exports = { ${names.join(', ')} };`,
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  return sandbox.module.exports;
}

class GuidedFakeElement {
  constructor(tagName, options = {}) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = '';
    this.type = '';
    this.disabled = false;
    this._textContent = '';
    this._innerHTML = '';
    this.innerHTMLWrites = 0;
    this.scrollIntoViewCalls = [];
    this.onInnerHTML = options.onInnerHTML || null;
    const values = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => values.add(name)),
      remove: (...names) => names.forEach(name => values.delete(name)),
      contains: name => values.has(name),
    };
  }

  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent; }
  set innerHTML(value) {
    this.innerHTMLWrites += 1;
    this._innerHTML = String(value);
    if (this.onInnerHTML) this.onInnerHTML(this._innerHTML);
  }
  get innerHTML() { return this._innerHTML; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = [...children]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  scrollIntoView(options) { this.scrollIntoViewCalls.push(options); }
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
    'const CHAT_SCHEMA_VERSION = 3;',
    'const MAX_STORED_CHATS = 50;',
    'const MAX_STORED_MESSAGES = 200;',
    'const MAX_STORED_TASK_REFS = 50;',
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
    extractFunction('normalizeStoredTaskRef'),
    extractFunction('createChatRecord'),
    extractFunction('normalizeStoredChat'),
    extractFunction('parseStoredChatState'),
    extractFunction('buildClearedChatState'),
    extractFunction('serializeChatsForStorage'),
    `module.exports = {
      createStableId, createChatRecord, parseStoredChatState, buildClearedChatState,
      safeStorageRead, safeStorageWrite, safeStorageRemove, serializeChatsForStorage
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
  assert.equal(api.isFwiExecutionRequest('帮我做个 Marmousi FWI'), true);
  assert.equal(api.isFwiExecutionRequest('请进行 Marmousi 反演'), true);
  assert.equal(api.isFwiExecutionRequest('perform a Marmousi FWI'), true);
  assert.equal(api.isFwiExecutionRequest('帮我做一下 Marmousi 正演测试'), true);
  for (const action of ['运行', '执行', '提交', '启动', '开始']) {
    assert.equal(api.isFwiExecutionRequest(`${action} Marmousi FWI`), true, action);
  }
  for (const action of ['做一下', '做一个', '做个', '做一次', '进行', '开展']) {
    assert.equal(
      api.isFwiExecutionRequest(`${action} Marmousi 的反演实验`),
      true,
      action,
    );
  }
  assert.equal(api.isFwiExecutionRequest('做一次 Marmousi 的反演实验'), true);
  assert.equal(api.isFwiExecutionRequest('Run a Marmousi forward simulation'), true);
  const legacySubmitParityCorpus = [
    '介绍并运行 Marmousi FWI',
    'run Marmousi FWI display model',
    '运行 Marmousi FWI，分析模型',
  ];
  for (const request of legacySubmitParityCorpus) {
    assert.equal(
      api.isFwiExecutionRequest(request),
      true,
      `legacy submit parity must be intercepted: ${request}`,
    );
  }
  assert.equal(api.isFwiExecutionRequest('什么是 FWI？请解释其原理和公式'), false);
  assert.equal(api.isFwiExecutionRequest('请解释如何运行 FWI'), false);
  assert.equal(api.isFwiExecutionRequest('请解释 FWI 的运行原理'), false);
  assert.equal(api.isFwiExecutionRequest('如何进行 Marmousi FWI？'), false);
  assert.equal(api.isFwiExecutionRequest('How to perform a Marmousi FWI?'), false);
  assert.equal(api.isFwiExecutionRequest('How does one perform FWI in theory?'), false);
  assert.equal(api.isFwiExecutionRequest('你可以做 Marmousi FWI 反演吗？'), false);
  assert.equal(api.isFwiExecutionRequest('Can you run a Marmousi FWI demo?'), false);
  assert.equal(api.isFwiExecutionRequest('CAN YOU RUN Marmousi FWI?'), false);
  assert.equal(api.isFwiExecutionRequest('HOW TO RUN Marmousi FWI'), false);
  assert.equal(api.isFwiExecutionRequest('how to execute Marmousi FWI'), false);
  assert.equal(api.isFwiExecutionRequest('不要运行 Marmousi FWI，只解释原理'), false);
  assert.equal(api.isFwiExecutionRequest('查看 Marmousi FWI 的运行状态'), false);
  assert.equal(api.isFwiExecutionRequest('显示 Marmousi FWI 的运行结果'), false);
  assert.equal(api.isFwiExecutionRequest('Show the Marmousi FWI result'), false);
  assert.equal(api.isFwiExecutionRequest('Marmousi FWI runtime configuration'), false);
  assert.equal(api.isFwiExecutionRequest('Marmousi FWI startup guide'), false);
  assert.equal(api.isFwiExecutionRequest('run a marmousi_94_288 FWI demo'), true);
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
  const interceptAt = sendSource.indexOf('isFwiExecutionRequest(text)');
  const legacyDispatchAt = sendSource.indexOf("if (request.mode === 'http')");
  assert.ok(interceptAt >= 0 && interceptAt < legacyDispatchAt);
  assert.match(sendSource, /recordGuidedExecutionIntent\(chat\.id, text\)/);
  assert.match(sendSource, /openGuidedFwi\(\{ \.\.\.guidedOverridesFromExecutionText\(text\), linkChatId: chat\.id \}\)/);
  assert.match(sendSource, /response\.fwiPayload/);
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

function testLegacyFwiReceiptSectionOnlyAppearsForCompatibilityState() {
  const section = new GuidedFakeElement('section');
  section.classList.add('hidden');
  const container = new GuidedFakeElement('div');
  const sandbox = {
    module: { exports: {} },
    state: { fwiReceiptMissing: false, fwiJob: null },
    document: {
      getElementById(id) {
        if (id === 'legacyFwiReceiptSection') return section;
        if (id === 'experimentHistory') return container;
        return null;
      },
    },
    escapeHtml(value) { return String(value); },
  };
  vm.runInNewContext([
    extractFunction('normalizeFwiJobId'),
    extractFunction('renderExperimentHistory'),
    'module.exports = { renderExperimentHistory };',
  ].join('\n'), sandbox);
  const render = sandbox.module.exports.renderExperimentHistory;

  render();
  assert.equal(section.classList.contains('hidden'), true);
  assert.equal(container.innerHTML, '');

  sandbox.state.fwiJob = { job_id: 'fwi-legacy-1', status: 'succeeded' };
  render();
  assert.equal(section.classList.contains('hidden'), false);
  assert.match(container.innerHTML, /fwi-legacy-1/);

  sandbox.state.fwiJob = null;
  sandbox.state.fwiReceiptMissing = true;
  render();
  assert.equal(section.classList.contains('hidden'), false);
  assert.match(container.innerHTML, /未创建任务/);
  assert.doesNotMatch(extractFunction('renderExperimentHistory'), /尚未提交任务/);
  assert.match(html, /旧版 FWI 会话回执（兼容）/);
}

function testHonestFwiControlsAndNoPlaceholderFeatures() {
  assert.match(html, /id="fwiQuickActions"/);
  assert.match(html, /Deepwave 2D Acoustic FWI/);
  assert.match(html, /旧版 FWI 会话回执（兼容）/);
  assert.match(html, /id="legacyFwiReceiptSection" class="hidden/);
  assert.match(html, /marmousi_94_288/);
  assert.match(html, /id="guidedFwiPanel"/);
  assert.match(html, /openGuidedFwi\(\{ preset: 'fwi_smoke', device: 'cuda', iterations: 2 \}\)/);
  assert.match(html, /openGuidedFwi\(\{ preset: 'fwi_demo', device: 'cpu', iterations: 5 \}\)/);
  assert.doesNotMatch(html, /onclick="sendQuick\(/);
  assert.match(html, /自定义迭代/);
  assert.match(html, /1–10000 次/);
  assert.match(html, /运行 500 次迭代的 FWI/);
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
  assert.equal(restored.schemaVersion, 3, 'v2 envelopes migrate to schema v3');
  assert.equal(restored.activeChatId, first.id);
  assert.equal(restored.mode, 'grpc');
  assert.equal(restored.chats[first.id].title, 'restored chat');
  assert.equal(restored.chats[first.id].fwiJob.job_id, 'fwi-safe-1');

  const withStaleCache = api.parseStoredChatState(JSON.stringify({
    schemaVersion: 3,
    activeChatId: first.id,
    mode: 'http',
    chats: {
      [first.id]: {
        ...first,
        taskRefs: [{
          taskId: 'task-durable-1', linkedAt: 123, status: 'Succeeded',
          resultReady: true, progressCompleted: 500, progressTotal: 500,
          purgeState: 'purged', purgedAt: '2026-07-15T12:00:00Z',
        }],
      },
    },
  }), '', 'http');
  const migratedRef = withStaleCache.chats[first.id].taskRefs[0];
  assert.equal(migratedRef.taskId, 'task-durable-1');
  assert.equal(migratedRef.linkedAt, 123);
  assert.equal(migratedRef.status, '', 'local status cache must not survive reload as task truth');
  assert.equal(migratedRef.resultReady, false);
  assert.equal(migratedRef.purgeState, '', 'local purge cache must be reverified after reload');
  assert.equal(migratedRef.purgedAt, '');
  const serialized = api.serializeChatsForStorage({
    [first.id]: {
      ...first,
      taskRefs: [{
        taskId: 'task-durable-1', linkedAt: 123, status: 'Succeeded',
        resultReady: true, progressCompleted: 500, progressTotal: 500,
      }],
    },
  });
  assert.deepEqual(
    Object.keys(serialized[first.id].taskRefs[0]).sort(),
    ['linkedAt', 'taskId'],
    'localStorage payload keeps relationship identity only',
  );

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

function testExecutionIntentIsStoredBeforeIndependentDraft() {
  const stored = [];
  const rendered = [];
  const elements = {
    welcomeMsg: new GuidedFakeElement('div'),
    messages: new GuidedFakeElement('div'),
    chatTitle: new GuidedFakeElement('h2'),
  };
  const sandbox = {
    module: { exports: {} },
    state: { chats: { 'chat-1': { id: 'chat-1', title: 'FWI request' } } },
    document: { getElementById(id) { return elements[id]; } },
    appendStoredMessage(chatId, role, content) { stored.push({ chatId, role, content }); },
    appendMessage(role, content) { rendered.push({ role, content }); },
    renderHistory() {},
  };
  vm.runInNewContext([
    extractFunction('recordGuidedExecutionIntent'),
    'module.exports = { recordGuidedExecutionIntent };',
  ].join('\n'), sandbox);
  assert.equal(sandbox.module.exports.recordGuidedExecutionIntent('chat-1', '运行 500 次 FWI'), true);
  assert.deepEqual(stored.map(item => item.role), ['user', 'system']);
  assert.equal(stored[0].content, '运行 500 次 FWI');
  assert.match(stored[1].content, /只打开独立任务草稿、尚未创建\/运行任务/);
  assert.deepEqual(rendered.map(item => item.role), ['user', 'system']);
  const submitSource = extractFunction('submitGuidedDraft');
  assert.match(submitSource, /if \(!editing && state\.guided\.pendingLinkChatId\)/);
  assert.match(submitSource, /linkTaskToChat\(state\.guided\.pendingLinkChatId, task\)/);
  assert.doesNotMatch(html, /openGuidedFwi\(\{[^}]*linkChatId[^}]*\}\)[^<]*Demo CPU/);
}

function testConversationTaskLinksAreManyToManyAndIndependent() {
  const sandbox = {
    module: { exports: {} },
    Date,
    state: {
      chatId: 'chat-a',
      chats: {
        'chat-a': { id: 'chat-a', taskRefs: [], time: 1 },
        'chat-b': { id: 'chat-b', taskRefs: [], time: 2 },
      },
    },
    persistChatState: () => true,
    renderConversationTaskRefs() {},
    renderHistory() {},
  };
  vm.runInNewContext([
    'const MAX_STORED_TASK_REFS = 50;',
    extractFunction('isSafeConversationId'),
    extractFunction('normalizeStoredTaskRef'),
    extractFunction('taskRefFromGuidedTask'),
    extractFunction('isTaskReferencedByChat'),
    extractFunction('linkTaskToChat'),
    extractFunction('unlinkTaskFromChat'),
    extractFunction('markTaskRefsPurged'),
    'module.exports = { linkTaskToChat, unlinkTaskFromChat, markTaskRefsPurged, isTaskReferencedByChat };',
  ].join('\n'), sandbox);
  const task = {
    taskId: 'task-shared-1', status: 'Running', visibilityRevision: 2, trashedAt: '',
    draft: { goal: 'shared task', device: 'cuda', iterations: 500, optimizer: 'adam', learningRate: '10' },
    adapter: { completed: 12, total: 500 },
  };
  const api = sandbox.module.exports;
  assert.equal(api.linkTaskToChat('chat-a', task), true);
  assert.equal(api.linkTaskToChat('chat-b', task), true);
  assert.equal(api.isTaskReferencedByChat(sandbox.state.chats['chat-a'], task.taskId), true);
  assert.equal(api.isTaskReferencedByChat(sandbox.state.chats['chat-b'], task.taskId), true);
  assert.equal(api.markTaskRefsPurged(task.taskId, '2026-07-15T12:00:00Z'), true);
  assert.equal(sandbox.state.chats['chat-a'].taskRefs[0].purgeState, 'purged');
  assert.equal(sandbox.state.chats['chat-b'].taskRefs[0].purgeState, 'purged');
  assert.equal(sandbox.state.chats['chat-a'].taskRefs[0].resultReady, false);
  assert.equal(
    api.isTaskReferencedByChat(sandbox.state.chats['chat-a'], task.taskId),
    true,
    'permanent task deletion retains the conversation reference as a tombstone',
  );
  assert.equal(api.unlinkTaskFromChat('chat-a', task.taskId), true);
  assert.equal(api.isTaskReferencedByChat(sandbox.state.chats['chat-a'], task.taskId), false);
  assert.equal(api.isTaskReferencedByChat(sandbox.state.chats['chat-b'], task.taskId), true);
  assert.equal(task.status, 'Running', 'unlinking a conversation reference cannot mutate the task');
}

function testChatDeleteConfirmsAndRollsBackWhenStorageFails() {
  const toasts = [];
  const originalChats = {
    'chat-a': { id: 'chat-a', title: 'active', taskRefs: [] },
    'chat-b': { id: 'chat-b', title: 'delete me', taskRefs: [{ taskId: 'task-1', linkedAt: 1 }] },
  };
  const sandbox = {
    module: { exports: {} },
    window: { confirm: () => true },
    state: {
      chats: originalChats, chatId: 'chat-a', contextId: 'ctx-a', fwiJob: null,
      preferredMode: 'http', mode: 'http',
    },
    persistChatState: () => false,
    showToast(message) { toasts.push(message); },
    renderHistory() { throw new Error('failed deletion must not rerender as success'); },
    abortActiveRequest() {},
    createChatRecord() { throw new Error('replacement not expected'); },
    loadChat() { throw new Error('load not expected'); },
  };
  vm.runInNewContext([
    extractFunction('isSafeConversationId'),
    extractFunction('deleteChat'),
    'module.exports = { deleteChat };',
  ].join('\n'), sandbox);
  assert.equal(sandbox.module.exports.deleteChat('chat-b'), false);
  assert.equal(sandbox.state.chats, originalChats, 'storage failure restores the in-memory conversation set');
  assert.ok(sandbox.state.chats['chat-b']);
  assert.match(toasts[0], /未删除.*独立任务未受影响/);
  for (const name of ['deleteChat', 'clearChat', 'clearAllChats']) {
    const source = extractFunction(name);
    assert.match(source, /window\.confirm/);
    assert.doesNotMatch(source, /guidedApiRequest|trashGuidedTask|abandonGuidedFwi/);
  }
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

async function testLegacyFwiSubmitIsDeniedByEveryChatTransport() {
  let httpBody = null;
  const httpSandbox = {
    module: { exports: {} },
    CONFIG: { http: { url: '/a2a' } },
    fetch: async (_url, options) => {
      httpBody = JSON.parse(options.body);
      return {
        ok: true,
        headers: { get: () => 'application/json' },
        json: async () => ({ result: { answer: 'ok' } }),
      };
    },
    parseSSE: async () => { throw new Error('unexpected SSE'); },
    updateRequestContext() {},
    extractAnswer: () => 'ok',
    extractFwiPayload: () => null,
  };
  vm.runInNewContext([
    `async ${extractFunction('sendHttpStream')}`,
    'module.exports = { sendHttpStream };',
  ].join('\n'), httpSandbox);
  await httpSandbox.module.exports.sendHttpStream('hello', {
    requestId: 'request-http-deny',
    contextId: 'context-http-deny',
    controller: { signal: {} },
  });
  assert.equal(httpBody.params.metadata.allow_legacy_fwi_submit, 'false');
  assert.equal(typeof httpBody.params.metadata.allow_legacy_fwi_submit, 'string');

  let grpcBody = null;
  const grpcSandbox = {
    module: { exports: {} },
    state: { grpcAvailable: true },
    CONFIG: { grpc: { url: '/grpc-bridge' } },
    bindRequestController() {},
    fetch: async (_url, options) => {
      grpcBody = JSON.parse(options.body);
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({ status: 0, answer: 'ok' }),
      };
    },
    switchMode() {},
    updateRequestContext() {},
    extractFwiPayload: () => null,
    setStatus() {},
    setGrpcAvailability() {},
  };
  vm.runInNewContext([
    `async ${extractFunction('sendGrpc')}`,
    'module.exports = { sendGrpc };',
  ].join('\n'), grpcSandbox);
  await grpcSandbox.module.exports.sendGrpc('hello', {
    contextId: 'context-grpc-deny',
    controller: { signal: {} },
  });
  assert.equal(grpcBody.allow_legacy_fwi_submit, false);
  assert.equal(typeof grpcBody.allow_legacy_fwi_submit, 'boolean');
  assert.equal(Object.hasOwn(grpcBody, 'metadata'), false);

  assert.equal((html.match(/allow_legacy_fwi_submit/g) || []).length, 2);
  assert.doesNotMatch(html, /id=["'][^"']*allow_legacy_fwi_submit/i);
  assert.doesNotMatch(
    html,
    /allow_legacy_fwi_submit\s*[:=]\s*(?:state|document|localStorage|sessionStorage)/,
  );
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

function makeGuidedTask(overrides = {}) {
  const taskId = overrides.task_id || 'task-guided-1';
  const planHash = overrides.plan_hash || `sha256:${'a'.repeat(64)}`;
  const approval = overrides.approval
    ? {
        approval_id: 'approval-guided-1',
        plan_id: 'plan-guided-1',
        plan_hash: planHash,
        decision: 'approved',
        ...overrides.approval,
      }
    : null;
  return {
    task_id: taskId,
    status: overrides.status || 'AwaitingApproval',
    draft: {
      draft_id: 'draft-guided-1',
      revision: 1,
      status: 'AwaitingApproval',
      goal: '<img src=x onerror=alert(1)>',
      task_type: 'acoustic_fwi_2d',
      dataset: { id: 'marmousi_94_288', version: '1.0.0' },
      algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' },
      parameters: {
        preset: 'fwi_smoke', device: 'cpu', iterations: overrides.iterations ?? 2, seed: 0,
        optimizer: overrides.optimizer || 'adam',
        learning_rate_milli: overrides.learning_rate_milli ?? 10000,
      },
      resources: { wall_time_seconds: overrides.wall_time_seconds ?? 7200 },
    },
    plan: {
      plan_id: 'plan-guided-1',
      plan_hash: planHash,
      draft: { draft_id: 'draft-guided-1', revision: 1 },
      task_type: 'acoustic_fwi_2d',
      nodes: [{
        node_id: 'invert',
        algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' },
        outputs: [
          { port: 'inverted_model', data_type: 'inverted_velocity_model_2d' },
          { port: 'loss', data_type: 'loss_curve' },
          { port: 'true_model_figure', data_type: 'figure' },
          { port: 'initial_model_figure', data_type: 'figure' },
          { port: 'inverted_model_figure', data_type: 'figure' },
          { port: 'model_error_figure', data_type: 'figure' },
          { port: 'shot_gathers_figure', data_type: 'figure' },
          { port: 'loss_curve_figure', data_type: 'figure' },
        ],
      }],
    },
    approval,
    dispatch: overrides.dispatch
      ? { reconciliation: null, ...overrides.dispatch } : null,
    runtime_status: overrides.adapter_status || null,
    can_cancel: overrides.can_cancel ?? false,
    cancellation: Object.hasOwn(overrides, 'cancellation')
      ? overrides.cancellation : null,
    timeout: Object.hasOwn(overrides, 'timeout') ? overrides.timeout : null,
  };
}

function makeGuidedManifest(type, id, overrides = {}) {
  return {
    schema_version: '1.0.0',
    artifact_id: id,
    task_id: 'task-guided-1',
    node_id: 'invert',
    artifact_type: type,
    media_type: type === 'loss_curve' ? 'text/csv' : 'application/x-npy',
    location: { relative_path: '../../../../etc/passwd' },
    content_hash: `sha256:${'b'.repeat(64)}`,
    size_bytes: 42,
    display: {
      component: type === 'loss_curve' ? 'line_chart' : 'download',
      title: '<svg onload=alert(1)>',
      order: type === 'loss_curve' ? 1 : 0,
    },
    ...overrides,
  };
}

function testGuidedFormHasStrictBoundaries() {
  const api = loadGuidedFunctions();
  const base = {
    goal: 'Marmousi FWI',
    dataset_id: 'marmousi_94_288',
    dataset_version: '1.0.0',
    preset: 'fwi_smoke',
    device: 'cpu',
    iterations: 1,
    seed: 0,
    optimizer: 'adam',
    learning_rate: '10',
  };
  assert.equal(api.validateGuidedFwiForm(base).ok, true);
  assert.equal(api.validateGuidedFwiForm({
    ...base, preset: 'fwi_demo', device: 'cuda', iterations: 10000, seed: 2147483647,
  }).ok, true);
  for (const patch of [
    { iterations: 0 }, { iterations: 10001 }, { iterations: 1.5 }, { iterations: '01' },
    { seed: -1 }, { seed: 2147483648 }, { seed: '1.0' },
    { preset: 'custom' }, { device: 'auto' }, { dataset_id: '../secret' },
    { optimizer: 'rmsprop' }, { learning_rate: '0.01' }, { learning_rate: 101 },
    { dataset_version: 'latest' }, { goal: '' }, { goal: 'x'.repeat(2001) },
  ]) {
    assert.equal(api.validateGuidedFwiForm({ ...base, ...patch }).ok, false, JSON.stringify(patch));
  }
  const normalized = api.validateGuidedFwiForm({ ...base, iterations: '2', seed: '7' });
  assert.equal(normalized.ok, true);
  assert.equal(normalized.value.iterations, 2);
  assert.equal(normalized.value.seed, 7);
  assert.deepEqual(Object.keys(normalized.value), [
    'goal', 'dataset_id', 'dataset_version', 'preset', 'device', 'iterations', 'seed',
    'optimizer', 'learning_rate',
  ]);
  assert.equal(api.validateGuidedFwiForm({
    ...base, optimizer: 'sgd', learning_rate: '10000000',
  }).ok, true);
  assert.equal(api.validateGuidedFwiForm({
    ...base, optimizer: 'sgd', learning_rate: '10',
  }).ok, false);
  assert.equal(api.guidedLearningRateFromMilli(10000), '10');
  for (const [text, expected] of [
    ['FWI 学习率 10.0000', '10.0000'],
    ['FWI learning rate=1e1', '1e1'],
    ['FWI lr -10', '-10'],
    ['FWI lr +10', '+10'],
  ]) {
    const overrides = api.guidedOverridesFromExecutionText(text);
    assert.equal(overrides.learning_rate, expected);
    assert.equal(
      api.validateGuidedFwiForm({ ...base, learning_rate: overrides.learning_rate }).ok,
      false,
      text,
    );
  }
  for (const text of ['FWI optimizer RMSProp', '使用 RMSProp 做 FWI']) {
    const overrides = api.guidedOverridesFromExecutionText(text);
    assert.equal(overrides.optimizer, 'rmsprop');
    const invalidForm = api.makeGuidedForm(overrides, {
      datasets: [{ id: 'marmousi_94_288', version: '1.0.0' }],
    });
    assert.equal(invalidForm.optimizer, 'rmsprop');
    assert.equal(api.validateGuidedFwiForm({ ...base, ...invalidForm }).ok, false);
  }
  assert.equal(api.guidedOverridesFromExecutionText('请做 Marmousi FWI，迭代50次').iterations, '50');
  assert.equal(api.guidedOverridesFromExecutionText('Marmousi FWI，50次迭代').iterations, '50');
  assert.equal(api.guidedOverridesFromExecutionText('运行50轮 FWI').iterations, '50');
  assert.equal(api.guidedOverridesFromExecutionText('Run Marmousi FWI for 10 iterations').iterations, '10');
  assert.equal(api.guidedOverridesFromExecutionText('Run Marmousi FWI, iterations: 12').iterations, '12');
  const maximumIterations = api.guidedOverridesFromExecutionText('运行 Marmousi FWI，迭代 10000 次');
  assert.equal(maximumIterations.iterations, '10000');
  assert.equal(api.validateGuidedFwiForm({
    ...base, iterations: maximumIterations.iterations,
  }).ok, true);
  const excessiveIterations = api.guidedOverridesFromExecutionText('运行 Marmousi FWI，迭代 10001 次');
  assert.equal(excessiveIterations.iterations, '10001');
  assert.equal(api.validateGuidedFwiForm({
    ...base, iterations: excessiveIterations.iterations,
  }).ok, false);
  const negativeIterations = api.guidedOverridesFromExecutionText('运行 Marmousi FWI，迭代 -3 次');
  assert.equal(negativeIterations.iterations, '-3');
  const catalog = {
    datasets: [{ id: 'marmousi_94_288', version: '1.0.0' }],
  };
  assert.equal(api.makeGuidedForm(negativeIterations, catalog).iterations, '-3');
  assert.equal(api.validateGuidedFwiForm({
    ...base, iterations: negativeIterations.iterations,
  }).ok, false);
  const decimalIterations = api.guidedOverridesFromExecutionText('运行 Marmousi FWI，2.5 次迭代');
  assert.equal(decimalIterations.iterations, '2.5');
  assert.equal(api.makeGuidedForm(decimalIterations, catalog).iterations, '2.5');
  assert.equal(api.validateGuidedFwiForm({
    ...base, iterations: decimalIterations.iterations,
  }).ok, false);
  const forward = api.guidedOverridesFromExecutionText('运行 Marmousi 正演 / forward');
  assert.equal(forward.unsupported_operation, 'forward');
  assert.equal(forward.preset, 'forward');
  assert.notEqual(forward.preset, 'fwi_smoke');
  assert.equal(api.hasGuidedUnsupportedForwardIntent(forward.goal), true);
  const rejectedForward = api.validateGuidedFwiForm({
    ...base,
    goal: forward.goal,
    unsupported_operation: forward.unsupported_operation,
  });
  assert.equal(rejectedForward.ok, false);
  assert.match(rejectedForward.errors.join('；'), /P1 Guided 当前不支持正演\/forward/);
  assert.match(html, /id="guidedIterations"[^>]+min="1" max="10000" step="1"/);
  assert.match(html, /受支持的托管 Worker 运行后，任务卡会提供取消，只有状态变为 Cancelled 才表示已经停止/);
  assert.match(html, /自然语言未写 CUDA\/GPU 时安全默认 CPU，不会自动切换/);
  assert.match(html, /不支持：\$\{escapeHtml\(invalidOptimizer\)\}/);
  assert.match(html, /id="guidedSeed"[^>]+min="0" max="2147483647" step="1"/);
}

function testGuidedForwardIsExplicitlyBlocked() {
  const unsupportedSource = extractFunction('renderGuidedUnsupportedForwardHtml');
  assert.match(unsupportedSource, /P1 Guided 不支持正演 \/ forward/);
  assert.match(unsupportedSource, /不会被静默改成反演/);
  assert.match(unsupportedSource, /不会创建 Draft、Plan 或运行任务/);
  assert.doesNotMatch(unsupportedSource, /submitGuidedDraft|approveGuidedFwi|method:\s*'POST'/);

  const renderSource = extractFunction('renderGuidedFwi');
  assert.match(renderSource, /phase === 'unsupported_forward'/);
  assert.match(renderSource, /renderGuidedUnsupportedForwardHtml\(\)/);

  const openSource = extractFunction('openGuidedFwi');
  const blockAt = openSource.indexOf("overrides.unsupported_operation === 'forward'");
  const sessionAt = openSource.indexOf("guidedApiPath('session')");
  assert.ok(blockAt >= 0 && sessionAt > blockAt);
  assert.match(openSource, /state\.guided\.phase = 'unsupported_forward'/);
  assert.match(openSource, /return true/);

  const readSource = extractFunction('readGuidedFwiForm');
  assert.match(readSource, /unsupported_operation: state\.guided\.form\?\.unsupported_operation/);
  const submitSource = extractFunction('submitGuidedDraft');
  assert.ok(submitSource.indexOf('if (!validation.ok)') < submitSource.indexOf('guidedApiRequest(path'));
}

function testGuidedIdentifiersAndRoutesAreConstrained() {
  const api = loadGuidedFunctions();
  assert.equal(api.isSafeGuidedOpaqueId('task-safe:1'), true);
  assert.equal(api.isSafeGuidedOpaqueId("task' onclick='alert(1)"), false);
  assert.equal(api.isSafeGuidedIdentifier('marmousi_94_288'), true);
  assert.equal(api.isSafeGuidedIdentifier('../marmousi'), false);
  assert.equal(
    api.guidedApiPath('artifact', 'task-safe:1', 'artifact-loss-1'),
    '/api/scientific-runtime/v1/tasks/task-safe%3A1/artifacts/artifact-loss-1',
  );
  assert.equal(api.guidedApiPath('task', '../escape'), '');
  assert.equal(
    api.guidedApiPath('trash', 'task-safe:1'),
    '/api/scientific-runtime/v1/tasks/task-safe%3A1/trash',
  );
  assert.equal(
    api.guidedApiPath('restore', 'task-safe:1'),
    '/api/scientific-runtime/v1/tasks/task-safe%3A1/restore',
  );
  assert.equal(
    api.guidedApiPath('purge', 'task-safe:1'),
    '/api/scientific-runtime/v1/tasks/task-safe%3A1/purge',
  );
  assert.equal(
    api.guidedApiPath('cancel', 'task-safe:1'),
    '/api/scientific-runtime/v1/tasks/task-safe%3A1/cancel',
  );
  assert.equal(api.guidedApiPath('artifact', 'task-1', "bad'id"), '');
  assert.equal(api.guidedApiPath('unknown', 'task-1'), '');

  const downloadSource = extractFunction('downloadGuidedArtifact');
  assert.match(downloadSource, /guidedApiPath\('artifact', taskId, artifactId\)/);
  assert.match(downloadSource, /'X-Workbench-CSRF': state\.guided\.csrfToken/);
  assert.doesNotMatch(downloadSource, /location|relative_path|href\s*=\s*artifact/);
}

function testGuidedTaskAndCrashStatesAreHonest() {
  const api = loadGuidedFunctions();
  const catalog = {
    datasets: [{ id: 'marmousi_94_288', version: '1.0.0' }],
    algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' },
  };
  const reviewTask = api.normalizeGuidedTaskProjection(makeGuidedTask());
  assert.equal(api.isGuidedReviewReady(reviewTask, catalog), true);
  assert.equal(api.isGuidedApprovedSubmitPending(reviewTask, catalog), false);
  const approvedSubmitPendingTask = api.normalizeGuidedTaskProjection(makeGuidedTask({
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
  }));
  assert.equal(api.isGuidedApprovedSubmitPending(approvedSubmitPendingTask, catalog), true);
  const maximumProjection = api.normalizeGuidedTaskProjection(makeGuidedTask({
    iterations: 10000,
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    adapter_status: {
      status: 'Running', stage: 'invert', completed: 9999, total: 10000,
      message: 'long validation run',
    },
  }));
  assert.equal(maximumProjection.draft.iterations, 10000);
  assert.equal(maximumProjection.adapter.completed, 9999);
  assert.equal(maximumProjection.adapter.total, 10000);
  const cancellable = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: true,
  }));
  assert.equal(cancellable.canCancel, true);
  assert.equal(cancellable.cancellation, null);
  const armedTimeout = {
    state: 'armed', wall_time_seconds: 7200,
    started_at: '2026-07-16T12:00:00.000000Z',
    deadline_at: '2026-07-16T14:00:00.000000Z',
    resolved_at: null, failure_code: null, terminal_status: null,
  };
  const timeoutArmed = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: true,
    timeout: armedTimeout,
  }));
  assert.equal(timeoutArmed.canCancel, true);
  assert.equal(timeoutArmed.timeout.state, 'armed');
  assert.match(api.guidedTimeoutExplanation(timeoutArmed.timeout), /首次持久观察.*ready \+ running/);

  const requestedTimeout = { ...armedTimeout, state: 'requested' };
  const timeoutRequested = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    timeout: requestedTimeout,
  }));
  assert.equal(timeoutRequested.status, 'Running');
  assert.equal(timeoutRequested.timeout.state, 'requested');
  assert.match(api.guidedTimeoutExplanation(timeoutRequested.timeout), /尚未确认变为 Failed/);
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: true,
    timeout: requestedTimeout,
  })), null, 'timeout authorization closes cancellation');

  const timedOut = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Failed',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    timeout: {
      ...armedTimeout,
      state: 'timed_out',
      resolved_at: '2026-07-16T14:00:02.000000Z',
      failure_code: 'WALL_TIME_EXCEEDED',
      terminal_status: 'Failed',
    },
  }));
  assert.equal(timedOut.status, 'Failed');
  assert.equal(timedOut.timeout.failureCode, 'WALL_TIME_EXCEEDED');
  assert.match(api.guidedTimeoutExplanation(timedOut.timeout), /exact Worker.*WALL_TIME_EXCEEDED/);
  for (const malformedTimeout of [
    { ...armedTimeout, timeout_id: `timeout-${'a'.repeat(32)}` },
    { ...requestedTimeout, failure_code: 'WALL_TIME_EXCEEDED' },
    { ...armedTimeout, wall_time_seconds: 7199 },
    {
      ...armedTimeout, state: 'timed_out',
      resolved_at: '2026-07-16T14:00:02.000000Z',
      failure_code: 'WORKER_FAILED', terminal_status: 'Failed',
    },
  ]) {
    assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
      status: malformedTimeout.state === 'timed_out' ? 'Failed' : 'Running',
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
      dispatch: { state: 'dispatched' },
      can_cancel: false,
      timeout: malformedTimeout,
    })), null);
  }
  const requestedCancellation = {
    state: 'requested', reason: 'user_requested',
    requested_at: '2026-07-16T12:00:00Z', resolved_at: null, failure_code: null,
  };
  const cancelling = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    cancellation: requestedCancellation,
  }));
  assert.equal(cancelling.status, 'Running', 'a durable request is not terminal cancellation');
  assert.equal(cancelling.cancellation.state, 'requested');
  assert.match(api.guidedCancellationExplanation(cancelling.cancellation), /不能视为已取消/);
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    cancellation: requestedCancellation,
    timeout: armedTimeout,
  })), null, 'user cancellation must project the timeout window as suppressed');
  const timeoutSuppressed = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    cancellation: requestedCancellation,
    timeout: { ...armedTimeout, state: 'suppressed' },
  }));
  assert.equal(timeoutSuppressed.timeout.state, 'suppressed');
  assert.match(api.guidedTimeoutExplanation(timeoutSuppressed.timeout), /用户取消先取得/);
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: false,
    timeout: { ...armedTimeout, state: 'suppressed' },
  })), null, 'a suppressed timeout must retain its durable cancellation');
  const cancelled = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Cancelled',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    adapter_status: {
      status: 'Cancelled', stage: 'cancelled', completed: 1, total: 2,
      message: 'FWI job was cancelled',
    },
    cancellation: {
      ...requestedCancellation, state: 'cancelled', resolved_at: '2026-07-16T12:00:05Z',
    },
  }));
  assert.equal(cancelled.cancellation.state, 'cancelled');
  assert.equal(cancelled.adapter.status, 'Cancelled');
  const superseded = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Failed',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    cancellation: {
      ...requestedCancellation, state: 'superseded', resolved_at: '2026-07-16T12:00:03Z',
    },
    timeout: { ...armedTimeout, state: 'suppressed' },
  }));
  assert.equal(superseded.cancellation.state, 'superseded');
  assert.equal(superseded.timeout.state, 'suppressed');
  for (const malformedCancellation of [
    { ...requestedCancellation, reason: 'wall_time_exceeded' },
    { ...requestedCancellation, signal: 'SIGKILL' },
    { ...requestedCancellation, resolved_at: '2026-07-16T12:00:01Z' },
    { ...requestedCancellation, failure_code: '/root/private/status' },
    { ...requestedCancellation, state: 'cancelled' },
  ]) {
    assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
      status: 'Running',
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
      dispatch: { state: 'dispatched' },
      cancellation: malformedCancellation,
    })), null);
  }
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Running',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
    can_cancel: true,
    cancellation: requestedCancellation,
  })), null, 'the server cannot advertise another cancel after admission');
  assert.equal(api.normalizeGuidedTaskProjection({
    ...makeGuidedTask(), can_cancel: 'yes',
  }), null);
  assert.equal(api.isGuidedReviewReady({
    ...reviewTask, plan: { ...reviewTask.plan, nodeCount: 2 },
  }, catalog), false);
  for (const dispatchState of ['pending', 'dispatching', 'dispatched', 'reconciliation_required']) {
    const failureCode = 'DISPATCH_RECEIPT_INVALID';
    const task = api.normalizeGuidedTaskProjection(makeGuidedTask({
      status: 'Queued',
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
      dispatch: {
        state: dispatchState,
        failure_code: dispatchState === 'reconciliation_required' ? failureCode : null,
        reconciliation: dispatchState === 'reconciliation_required' ? {
          failure_code: failureCode,
          recorded_at: '2026-07-16T12:00:00.000000Z',
          state: 'action_required',
          result: null,
          evidence_kind: null,
          resolved_at: null,
        } : null,
      },
      adapter_status: {
        status: dispatchState === 'dispatched' ? 'Running' : 'Queued',
        stage: '<img onerror=alert(1)>', completed: 1, total: 2,
        message: '<script>alert(1)</script>',
      },
    }));
    assert.equal(task.dispatch.state, dispatchState);
    assert.match(api.guidedDispatchExplanation(dispatchState), /派发|Adapter|reconciliation|SQLite/);
    assert.equal(task.adapter.stage, '<img onerror=alert(1)>');
  }
  assert.match(api.guidedDispatchExplanation('pending'), /不会由浏览器重发/);
  assert.match(api.guidedDispatchExplanation('reconciliation_required'), /浏览器不会重试/);
  const resolvedReconciliation = {
    failure_code: 'DISPATCH_RECEIPT_UNKNOWN',
    recorded_at: '2026-07-16T12:00:00.000000Z',
    state: 'resolved',
    result: 'dispatched',
    evidence_kind: 'managed_worker_receipt',
    resolved_at: '2026-07-16T12:00:01.000000Z',
  };
  const reconciled = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Queued',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: {
      state: 'dispatched', failure_code: null,
      reconciliation: resolvedReconciliation,
    },
  }));
  assert.equal(reconciled.dispatch.state, 'dispatched');
  assert.deepEqual(JSON.parse(JSON.stringify(reconciled.dispatch.reconciliation)), {
    state: 'resolved',
    failureCode: 'DISPATCH_RECEIPT_UNKNOWN',
    recordedAt: '2026-07-16T12:00:00.000000Z',
    result: 'dispatched',
    evidenceKind: 'managed_worker_receipt',
    resolvedAt: '2026-07-16T12:00:01.000000Z',
  });
  assert.match(
    api.guidedReconciliationExplanation(reconciled.dispatch.reconciliation),
    /receipt adoption.*不是任务 retry/,
  );
  assert.equal(
    api.normalizeGuidedReconciliationProjection({
      ...resolvedReconciliation, evidence_kind: 'private_receipt',
    }).evidenceKind,
    'private_receipt',
  );
  for (const malformedReconciliation of [
    { ...resolvedReconciliation, handle: { job_id: 'private-job' } },
    { ...resolvedReconciliation, receipt_record_hash: `sha256:${'a'.repeat(64)}` },
    { ...resolvedReconciliation, resolved_at: '/root/private/run' },
    { ...resolvedReconciliation, state: 'action_required' },
    { ...resolvedReconciliation, evidence_kind: 'worker_pid' },
  ]) {
    assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask({
      status: 'Queued',
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
      dispatch: {
        state: 'dispatched', failure_code: null,
        reconciliation: malformedReconciliation,
      },
    })), null);
  }
  const completed = api.normalizeGuidedTaskProjection(makeGuidedTask({
    status: 'Queued',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'pending' },
  }));
  assert.ok(completed);
  assert.equal(api.isGuidedApprovalCompleted(completed, completed.plan.hash), true);
  const legacyProjection = makeGuidedTask({
    status: 'Succeeded',
    approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
  });
  legacyProjection.draft.algorithm.version = '1.1.0';
  legacyProjection.plan.nodes[0].algorithm.version = '1.1.0';
  legacyProjection.plan.nodes[0].outputs = legacyProjection.plan.nodes[0].outputs.slice(0, 2);
  delete legacyProjection.draft.parameters.optimizer;
  delete legacyProjection.draft.parameters.learning_rate_milli;
  const normalizedLegacy = api.normalizeGuidedTaskProjection(legacyProjection);
  assert.ok(normalizedLegacy, 'complete 1.0/1.1 projections remain readable');
  assert.equal(normalizedLegacy.draft.optimizer, '');
  assert.equal(normalizedLegacy.draft.learningRate, null);
  const currentMissingFigures = makeGuidedTask();
  currentMissingFigures.plan.nodes[0].outputs = currentMissingFigures.plan.nodes[0].outputs.slice(0, 2);
  assert.equal(
    api.normalizeGuidedTaskProjection(currentMissingFigures),
    null,
    'Algorithm 1.4 cannot silently fall back to the historical two-output contract',
  );
  const currentWrongFigurePort = makeGuidedTask();
  currentWrongFigurePort.plan.nodes[0].outputs[2].port = 'unexpected_figure';
  assert.equal(api.normalizeGuidedTaskProjection(currentWrongFigurePort), null);
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask()).plan.outputs.length, 8);
  const planHash = `sha256:${'a'.repeat(64)}`;
  for (const malformed of [
    { task_id: 'task-guided-1' },
    {
      task_id: 'task-guided-1', status: 'Queued', plan: { plan_hash: planHash },
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    },
    makeGuidedTask({
      status: 'Queued',
      approval: { approval_id: 'approval-guided-1', decision: 'approved' },
    }),
    makeGuidedTask({
      status: 'Queued',
      approval: {
        approval_id: 'approval-guided-1', decision: 'approved',
        plan_hash: `sha256:${'b'.repeat(64)}`,
      },
      dispatch: { state: 'pending' },
    }),
  ]) {
    assert.equal(api.normalizeGuidedTaskProjection(malformed), null);
  }
  assert.equal(api.normalizeGuidedTaskProjection(makeGuidedTask(), 'different-task'), null);
  assert.equal(api.normalizeGuidedTaskProjection({ ...makeGuidedTask(), task_id: '../bad' }), null);
  const taskSource = extractFunction('renderGuidedTaskHtml');
  assert.match(taskSource, /SQLite status/);
  assert.match(taskSource, /Adapter status/);
  assert.match(taskSource, /escapeHtml\(adapter\.stage/);
  assert.match(taskSource, /escapeHtml\(adapter\.message/);
  assert.match(taskSource, /onclick="cancelGuidedTask\(\)"/);
  assert.match(taskSource, /取消处理中…/);
  assert.match(taskSource, /task\.canCancel/);
  assert.match(taskSource, /task\.cancellation/);
  assert.match(taskSource, /已超时，安全停止中/);
  assert.match(taskSource, /guidedTimeoutExplanation\(task\.timeout\)/);
}

async function testGuidedRuntimeCancelUsesOneDurableMutation() {
  const taskId = 'task-guided-cancel-1';
  const activeTask = {
    taskId,
    status: 'Running',
    canCancel: true,
    cancellation: null,
    dispatch: { state: 'dispatched' },
  };
  const requestedTask = {
    ...activeTask,
    canCancel: false,
    cancellation: {
      state: 'requested', reason: 'user_requested',
      requestedAt: '2026-07-16T12:00:00Z', resolvedAt: null, failureCode: null,
    },
  };
  const requests = [];
  const toasts = [];
  let generatedKeys = 0;
  let confirmations = 0;
  let confirmResult = false;
  let responseMode = 'success';
  let scheduledPolls = 0;
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        task: activeTask,
        phase: 'monitoring',
        mutation: '',
        mutationKeys: Object.create(null),
        error: '',
        outcomeUnknown: false,
        cancelReplayAvailable: false,
      },
    },
    window: {
      confirm() { confirmations += 1; return confirmResult; },
    },
    createStableId(prefix) { generatedKeys += 1; return `${prefix}-stable-${generatedKeys}`; },
    isSafeGuidedOpaqueId(value) { return typeof value === 'string' && value === taskId; },
    guidedApiPath(resource, id) {
      return resource === 'cancel'
        ? `/api/scientific-runtime/v1/tasks/${id}/cancel` : '';
    },
    clearGuidedPoll() {},
    renderGuidedFwi() {},
    upsertGuidedTaskIndex() { return true; },
    async loadGuidedArtifacts() { throw new Error('cancel must not load artifacts'); },
    scheduleGuidedPoll() { scheduledPolls += 1; },
    showToast(message) { toasts.push(message); },
    normalizeGuidedTaskProjection(data, expectedTaskId) {
      return data && data.taskId === expectedTaskId ? data : null;
    },
    guidedInvalidMutationResponse(message) {
      const error = new Error(message);
      error.guidedOutcomeUnknown = true;
      return error;
    },
    async guidedApiRequest(path, options) {
      requests.push({
        path,
        method: options.method,
        body: JSON.parse(JSON.stringify(options.body)),
        idempotencyKey: options.idempotencyKey,
      });
      if (responseMode === 'unknown') {
        const error = new Error('connection closed');
        error.guidedOutcomeUnknown = true;
        throw error;
      }
      return requestedTask;
    },
  };
  vm.runInNewContext([
    extractFunction('guidedMutationKey'),
    extractFunction('continueGuidedCancel'),
    `async ${extractFunction('cancelGuidedTask')}`,
    'module.exports = { cancelGuidedTask, continueGuidedCancel };',
  ].join('\n'), sandbox);
  const cancel = sandbox.module.exports.cancelGuidedTask;
  const continueCancel = sandbox.module.exports.continueGuidedCancel;

  assert.equal(await cancel(), false, 'rejecting confirmation cannot create a mutation');
  assert.equal(requests.length, 0);
  assert.equal(generatedKeys, 0);

  confirmResult = true;
  assert.equal(await cancel(), true);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].body, { reason: 'user_requested' });
  assert.equal(requests[0].method, 'POST');
  assert.equal(
    requests[0].path,
    `/api/scientific-runtime/v1/tasks/${taskId}/cancel`,
  );
  assert.match(requests[0].idempotencyKey, /^guided-cancel:task-guided-cancel-1-stable-1$/);
  assert.equal(sandbox.state.guided.task.status, 'Running');
  assert.equal(sandbox.state.guided.task.cancellation.state, 'requested');
  assert.equal(sandbox.state.guided.phase, 'monitoring');
  assert.match(toasts.at(-1), /尚未确认停止/);
  assert.equal(await cancel(), false, 'a requested cancellation cannot submit again');
  assert.equal(requests.length, 1);

  sandbox.state.guided.task = { ...activeTask };
  sandbox.state.guided.phase = 'monitoring';
  sandbox.state.guided.mutationKeys = Object.create(null);
  sandbox.state.guided.cancelReplayAvailable = false;
  responseMode = 'unknown';
  const generatedBeforeUnknown = generatedKeys;
  const confirmationsBeforeUnknown = confirmations;
  assert.equal(await cancel(), false);
  assert.equal(requests.length, 2, 'the unknown path sends exactly one POST');
  assert.equal(sandbox.state.guided.phase, 'outcome_unknown');
  assert.equal(sandbox.state.guided.cancelReplayAvailable, true);
  assert.equal(scheduledPolls >= 1, true, 'unknown outcome schedules only a GET audit');
  const unknownKey = requests[1].idempotencyKey;
  assert.equal(generatedKeys, generatedBeforeUnknown + 1);

  responseMode = 'success';
  assert.equal(await continueCancel(), true);
  assert.equal(requests.length, 3);
  assert.equal(requests[2].idempotencyKey, unknownKey, 'explicit replay reuses the original key');
  assert.equal(generatedKeys, generatedBeforeUnknown + 1, 'replay cannot generate a new key');
  assert.equal(confirmations, confirmationsBeforeUnknown + 1, 'replay button does not open another confirm');
  assert.equal(sandbox.state.guided.cancelReplayAvailable, false);
  assert.equal(sandbox.state.guided.outcomeUnknown, false);

  assert.doesNotMatch(extractFunction('closeGuidedFwi'), /cancelGuidedTask|guidedApiPath\('cancel'/);
  assert.doesNotMatch(extractFunction('abandonGuidedFwi'), /cancelGuidedTask|guidedApiPath\('cancel'/);
  assert.doesNotMatch(extractFunction('changeGuidedTaskVisibility'), /cancelGuidedTask|guidedApiPath\('cancel'/);
}

function testGuidedArtifactManifestsUseControlledDownloads() {
  const api = loadGuidedFunctions();
  const raw = {
    artifacts: [
      makeGuidedManifest('inverted_velocity_model_2d', 'artifact-model-1'),
      makeGuidedManifest('loss_curve', 'artifact-loss-1'),
      makeGuidedManifest('loss_curve', "artifact'unsafe"),
      makeGuidedManifest('loss_curve', 'artifact-other-task', { task_id: 'other-task' }),
    ],
  };
  assert.equal(api.normalizeGuidedArtifacts(raw, 'task-guided-1').length, 0);
  const artifacts = api.normalizeGuidedArtifacts(raw.artifacts.slice(0, 2), 'task-guided-1');
  assert.equal(artifacts.length, 2);
  const rendered = api.renderGuidedArtifactsHtml('task-guided-1', artifacts);
  assert.equal((rendered.match(/data-guided-artifact=/g) || []).length, 2);
  assert.match(rendered, /ArtifactManifest/);
  assert.match(rendered, /downloadGuidedArtifact\('task-guided-1','artifact-model-1'\)/);
  assert.doesNotMatch(rendered, /\.\.\/\.\.\/etc\/passwd|relative_path/);
  assert.doesNotMatch(rendered, /<svg onload=alert\(1\)>/);
  assert.match(rendered, /&lt;svg onload=alert\(1\)&gt;/);
  const incomplete = api.renderGuidedArtifactsHtml('task-guided-1', artifacts.slice(0, 1));
  assert.match(incomplete, /尚未就绪/);
  assert.match(incomplete, /onclick="loadGuidedArtifacts\(\)"/);
  assert.match(incomplete, /重新获取 artifacts（GET）/);
  assert.match(incomplete, /不会重试 Worker 或创建新任务/);
}

function makeCurrentGuidedArtifacts() {
  const outputs = [
    { port: 'inverted_model', dataType: 'inverted_velocity_model_2d' },
    { port: 'loss', dataType: 'loss_curve' },
    { port: 'true_model_figure', dataType: 'figure' },
    { port: 'initial_model_figure', dataType: 'figure' },
    { port: 'inverted_model_figure', dataType: 'figure' },
    { port: 'model_error_figure', dataType: 'figure' },
    { port: 'shot_gathers_figure', dataType: 'figure' },
    { port: 'loss_curve_figure', dataType: 'figure' },
  ];
  const artifacts = outputs.map((output, index) => {
    const isFigure = output.dataType === 'figure';
    return makeGuidedManifest(output.dataType, `artifact-${output.port}`, {
      artifact_type: output.dataType,
      media_type: isFigure ? 'image/png'
        : (output.dataType === 'loss_curve' ? 'text/csv' : 'application/x-npy'),
      size_bytes: isFigure ? 4 : 42,
      display: {
        component: isFigure ? 'image'
          : (output.dataType === 'loss_curve' ? 'line_chart' : 'download'),
        title: `result ${index}`,
        order: index,
      },
      extensions: {
        'org.agent_rpc.adapter': { output_port: output.port },
        ...(isFigure ? {
          'org.agent_rpc.figure': { width_px: 1440, height_px: 608 },
        } : {}),
      },
    });
  });
  return { outputs, artifacts };
}

function testCurrentEightAndHistoricalTwoArtifactContracts() {
  const api = loadGuidedFunctions();
  const current = makeCurrentGuidedArtifacts();
  const normalized = api.normalizeGuidedArtifacts(
    { artifacts: current.artifacts }, 'task-guided-1', current.outputs,
  );
  assert.equal(normalized.length, 8);
  assert.equal(normalized.filter(item => item.mediaType === 'image/png').length, 6);
  const objectUrls = Object.fromEntries(
    normalized.filter(item => item.mediaType === 'image/png')
      .map(item => [item.artifactId, `blob:guided/${item.artifactId}`]),
  );
  const rendered = api.renderGuidedArtifactsHtml(
    'task-guided-1', normalized, current.outputs, objectUrls, {},
  );
  assert.equal((rendered.match(/<img src="blob:/g) || []).length, 6);
  assert.equal((rendered.match(/data-guided-artifact=/g) || []).length, 8);
  assert.doesNotMatch(rendered, /\/fwi-artifacts|worker_job_id|relative_path/);

  const historicalOutputs = current.outputs.slice(0, 2);
  const historical = api.normalizeGuidedArtifacts(
    { artifacts: current.artifacts.slice(0, 2) }, 'task-guided-1', historicalOutputs,
  );
  assert.equal(historical.length, 2, 'historical exact two-output plans remain readable');

  const duplicatePort = structuredClone(current.artifacts);
  duplicatePort[7].extensions['org.agent_rpc.adapter'].output_port = 'true_model_figure';
  assert.equal(api.normalizeGuidedArtifacts(
    { artifacts: duplicatePort }, 'task-guided-1', current.outputs,
  ).length, 0, 'duplicate output_port fails closed');
  const wrongComponent = structuredClone(current.artifacts);
  wrongComponent[2].display.component = 'download';
  assert.equal(api.normalizeGuidedArtifacts(
    { artifacts: wrongComponent }, 'task-guided-1', current.outputs,
  ).length, 0, 'PNG outputs must use the image component');
}

async function testGuidedImageBlobLoadingIsBoundedAndRevoked() {
  const current = makeCurrentGuidedArtifacts();
  const images = current.artifacts.slice(2).map(item => ({
    artifactId: item.artifact_id,
    taskId: item.task_id,
    mediaType: 'image/png',
    sizeBytes: item.size_bytes,
  }));
  const requests = [];
  const revoked = [];
  let counter = 0;
  let inFlight = 0;
  let maxInFlight = 0;
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        taskId: 'task-guided-1', generation: 4, csrfToken: 'csrf-token-1234567890',
        artifacts: images, artifactObjectUrls: Object.create(null),
        artifactImageErrors: Object.create(null),
      },
    },
    URL: {
      createObjectURL() { counter += 1; return `blob:guided/image-${counter}`; },
      revokeObjectURL(value) { revoked.push(value); },
    },
    guidedApiPath(_resource, taskId, artifactId) {
      return `/api/scientific-runtime/v1/tasks/${taskId}/artifacts/${artifactId}`;
    },
    async fetch(path, options) {
      requests.push({ path, options });
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      const mismatch = path.endsWith(images[2].artifactId);
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'image/png' },
        async blob() {
          inFlight -= 1;
          return { size: mismatch ? 5 : 4 };
        },
      };
    },
    renderGuidedFwi() {},
  };
  vm.runInNewContext([
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedBlobUrl'),
    extractFunction('revokeGuidedArtifactObjectUrls'),
    `async ${extractFunction('loadGuidedArtifactImages')}`,
    'module.exports = { loadGuidedArtifactImages, revokeGuidedArtifactObjectUrls };',
  ].join('\n'), sandbox);
  const api = sandbox.module.exports;
  assert.equal(await api.loadGuidedArtifactImages({ render: false }), false);
  assert.equal(requests.length, 6);
  assert.equal(maxInFlight, 1, 'image loading stays sequential and memory-bounded');
  assert.equal(Object.keys(sandbox.state.guided.artifactObjectUrls).length, 5);
  assert.equal(sandbox.state.guided.artifactImageErrors[images[2].artifactId], true);
  requests.forEach(({ path, options }) => {
    assert.match(path, /^\/api\/scientific-runtime\/v1\/tasks\/task-guided-1\/artifacts\//);
    assert.doesNotMatch(path, /fwi-artifacts/);
    assert.equal(options.headers['X-Workbench-CSRF'], 'csrf-token-1234567890');
  });
  api.revokeGuidedArtifactObjectUrls();
  assert.equal(revoked.length, 5);
  assert.equal(Object.keys(sandbox.state.guided.artifactObjectUrls).length, 0);
  assert.match(extractFunction('closeGuidedFwi'), /revokeGuidedArtifactObjectUrls\(\)/);
  assert.match(extractFunction('reopenGuidedTask'), /revokeGuidedArtifactObjectUrls\(\)/);
  assert.match(extractFunction('changeGuidedTaskVisibility'), /revokeGuidedArtifactObjectUrls\(\)/);
  assert.match(extractFunction('purgeGuidedTask'), /revokeGuidedArtifactObjectUrls\(\)/);
}

async function testGuidedImageRetriesAreSingleFlightAndStaleSafe() {
  const imageArtifact = {
    taskId: 'task-guided-1', artifactId: 'artifact-figure-1',
    mediaType: 'image/png', sizeBytes: 4,
  };
  const requests = [];
  const revoked = [];
  let created = 0;
  let gate = null;
  const makeGate = () => {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
  };
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        taskId: 'task-guided-1', generation: 4, csrfToken: 'csrf-token-1234567890',
        artifacts: [imageArtifact], artifactObjectUrls: Object.create(null),
        artifactImageErrors: Object.create(null), artifactImageLoadInFlight: false,
        artifactImageGeneration: 0,
      },
    },
    URL: {
      createObjectURL() { created += 1; return `blob:guided/single-flight-${created}`; },
      revokeObjectURL(value) { revoked.push(value); },
    },
    guidedApiPath(_resource, taskId, artifactId) {
      return `/api/scientific-runtime/v1/tasks/${taskId}/artifacts/${artifactId}`;
    },
    async fetch(path) {
      requests.push(path);
      const currentGate = gate;
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'image/png' },
        async blob() {
          await currentGate.promise;
          return { size: 4 };
        },
      };
    },
    renderGuidedFwi() {},
  };
  vm.runInNewContext([
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedBlobUrl'),
    extractFunction('revokeGuidedArtifactObjectUrls'),
    `async ${extractFunction('loadGuidedArtifactImages')}`,
    'module.exports = { loadGuidedArtifactImages, revokeGuidedArtifactObjectUrls };',
  ].join('\n'), sandbox);
  const api = sandbox.module.exports;

  gate = makeGate();
  const first = api.loadGuidedArtifactImages({ render: false });
  await Promise.resolve();
  assert.equal(requests.length, 1);
  assert.equal(
    await api.loadGuidedArtifactImages({ render: false }),
    false,
    'a repeated retry must join/reject the existing global image load instead of fetching again',
  );
  assert.equal(requests.length, 1);
  sandbox.state.guided.artifactObjectUrls[imageArtifact.artifactId] = 'blob:guided/older-url';
  gate.resolve();
  assert.equal(await first, true);
  assert.ok(revoked.includes('blob:guided/older-url'), 'a replaced Blob URL is revoked');
  assert.equal(
    sandbox.state.guided.artifactObjectUrls[imageArtifact.artifactId],
    'blob:guided/single-flight-1',
  );

  api.revokeGuidedArtifactObjectUrls();
  gate = makeGate();
  const stale = api.loadGuidedArtifactImages({ render: false });
  await Promise.resolve();
  const createdBeforeInvalidation = created;
  api.revokeGuidedArtifactObjectUrls();
  gate.resolve();
  assert.equal(await stale, false);
  assert.equal(created, createdBeforeInvalidation, 'an invalidated response cannot create a Blob URL');
  assert.equal(Object.keys(sandbox.state.guided.artifactObjectUrls).length, 0);
}

function testGuidedCatalogProjectionDoesNotExposePaths() {
  const api = loadGuidedFunctions();
  const session = api.normalizeGuidedSession({
    csrf_token: 'csrf-token-1234567890+/=',
    mode: 'guided',
    task_type: 'acoustic_fwi_2d',
    features: { approval_required: true, running_cancel: true },
    capabilities: {
      cancel: true,
      startup_dispatch_recovery: false,
      startup_receipt_recovery: false,
      startup_status_catchup: false,
      supervised_runtime_scheduling: true,
      continuous_status_supervision: true,
      supervisor_leases: true,
      positive_receipt_reconciliation: true,
      automatic_reconciliation: false,
    },
  });
  assert.equal(session.mode, 'guided');
  assert.equal(session.taskType, 'acoustic_fwi_2d');
  assert.deepEqual(
    JSON.parse(JSON.stringify(session.capabilities)),
    [
      'cancel',
      'supervised_runtime_scheduling',
      'continuous_status_supervision',
      'supervisor_leases',
      'positive_receipt_reconciliation',
    ],
  );
  const catalog = api.normalizeGuidedCatalog({
    datasets: [{
      id: 'marmousi_94_288', version: '1.0.0', immutable: true,
      content_hash: `sha256:${'c'.repeat(64)}`,
      relative_path: '/root/private/model.npy',
      metadata: {
        shape: [94, 288], dtype: 'float32', units: 'm/s',
        physics: '2d_acoustic_constant_density', parameter: 'vp',
        grid_spacing_m: { dx: 10, dz: 10 }, value_range: { minimum: 1500, maximum: 4500 },
        path: '/root/private/model.npy',
      },
    }],
    algorithm: { id: 'deepwave.acoustic_fwi', version: '1.1.0', entrypoint: '/root/run.py' },
  });
  assert.equal(catalog.datasets[0].metadata.path, undefined);
  assert.equal(catalog.datasets[0].relative_path, undefined);
  assert.deepEqual(
    JSON.parse(JSON.stringify(catalog.algorithm)),
    { id: 'deepwave.acoustic_fwi', version: '1.1.0' },
  );
  assert.doesNotMatch(extractFunction('renderGuidedCatalogPreview'), /relative_path|entrypoint|JSON\.stringify/);
}

function testGuidedApprovalCannotBeBypassedOrReplayedAutomatically() {
  const quickActions = html.match(/onclick="openGuidedFwi\(/g) || [];
  assert.equal(quickActions.length, 12);
  assert.doesNotMatch(html, /onclick="sendQuick\(/);

  const sendSource = extractFunction('sendMessage');
  assert.ok(sendSource.indexOf('isFwiExecutionRequest(text)') < sendSource.indexOf('state.activeRequest = request'));
  assert.match(sendSource, /openGuidedFwi\(\{ \.\.\.guidedOverridesFromExecutionText\(text\), linkChatId: chat\.id \}\)/);
  assert.equal(loadUiFunctions().api.isFwiExecutionRequest('什么是 FWI？请解释原理'), false);

  const approveSource = extractFunction('approveGuidedFwi');
  assert.match(approveSource, /returnPhase === 'review'/);
  assert.match(approveSource, /returnPhase === 'approval_incomplete'/);
  assert.match(approveSource, /isGuidedReviewReady\(task, state\.guided\.catalog\)/);
  assert.match(approveSource, /guidedMutationKey\('approve', body\)/);
  assert.match(approveSource, /isGuidedApprovalCompleted\(updated, body\.plan_hash\)/);
  assert.match(approveSource, /state\.guided\.approvalReplayAvailable = true/);
  assert.match(approveSource, /state\.guided\.phase = 'outcome_unknown'/);
  assert.doesNotMatch(approveSource, /if \(error && error\.guidedOutcomeUnknown\)/);
  const mutationKeySource = extractFunction('guidedMutationKey');
  assert.match(mutationKeySource, /existing\.signature === signature/);
  const continueSource = extractFunction('continueGuidedApprovedSubmit');
  assert.match(continueSource, /phase !== 'approval_incomplete'/);
  assert.match(continueSource, /return approveGuidedFwi\(\)/);
  assert.doesNotMatch(continueSource, /guidedMutationKey|randomUUID|method:\s*'POST'/);

  const requestSource = extractFunction('guidedApiRequest');
  assert.match(requestSource, /headers\['X-Workbench-CSRF'\]/);
  assert.match(requestSource, /headers\['Idempotency-Key'\]/);
  assert.match(requestSource, /credentials: 'same-origin'/);
  assert.match(requestSource, /error\.guidedOutcomeUnknown = mutation/);
  assert.match(requestSource, /mutation && response\.status >= 500/);

  assert.match(extractFunction('openGuidedFwi'), /\['outcome_unknown', 'approval_incomplete'\]/);
  assert.match(extractFunction('closeGuidedFwi'), /phase === 'outcome_unknown'/);
  assert.match(extractFunction('closeGuidedFwi'), /approvalReplayAvailable === true/);
  assert.match(extractFunction('reopenGuidedTask'), /approvalReplayAvailable === true/);

  const refreshSource = extractFunction('refreshGuidedTask');
  assert.match(refreshSource, /guidedApiPath\('task', taskId\)/);
  assert.match(refreshSource, /approvedSubmitPending/);
  assert.match(refreshSource, /state\.guided\.phase = 'approval_incomplete'/);
  assert.doesNotMatch(refreshSource, /submitGuidedDraft|approveGuidedFwi|abandonGuidedFwi|method:\s*'POST'|method:\s*'PUT'/);
  const approvalBranchStart = refreshSource.indexOf('if (approvedSubmitPending)');
  const approvalBranchEnd = refreshSource.indexOf("} else if (task.status === 'Cancelled'", approvalBranchStart);
  assert.ok(approvalBranchStart >= 0 && approvalBranchEnd > approvalBranchStart);
  assert.doesNotMatch(refreshSource.slice(approvalBranchStart, approvalBranchEnd), /scheduleGuidedPoll/);
  const unknownSource = extractFunction('renderGuidedFwi');
  assert.match(unknownSource, /不会更换 Idempotency-Key 或自动重发/);
  assert.match(unknownSource, /renderGuidedApprovedSubmitPendingHtml/);
  assert.match(unknownSource, /continueGuidedCancel\(\)/);
  assert.doesNotMatch(unknownSource, /retryGuided|EventSource/);

  const pendingSource = extractFunction('renderGuidedApprovedSubmitPendingHtml');
  assert.match(pendingSource, /continueGuidedApprovedSubmit\(\)/);
  assert.match(pendingSource, /复用原 Idempotency-Key/);
  assert.match(pendingSource, /不是任务 retry/);

  const artifactSource = extractFunction('loadGuidedArtifacts');
  assert.match(artifactSource, /guidedApiPath\('artifacts', taskId\)/);
  assert.match(artifactSource, /state\.guided\.error = ''/);
  assert.doesNotMatch(artifactSource, /method:\s*'POST'|method:\s*'PUT'|approveGuidedFwi|submitGuidedDraft|retryGuided/);
}

async function testGuidedApprovedSubmitPendingStopsPolling() {
  let scheduledPolls = 0;
  let artifactLoads = 0;
  const approvedTask = {
    taskId: 'task-guided-1',
    status: 'AwaitingApproval',
    approval: { id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: '' },
  };
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        taskId: approvedTask.taskId,
        pollInFlight: false,
        generation: 7,
        task: null,
        catalog: {},
        error: '',
        outcomeUnknown: true,
        phase: 'outcome_unknown',
      },
    },
    isSafeGuidedOpaqueId: value => value === approvedTask.taskId,
    clearGuidedPoll() {},
    guidedApiPath: () => '/api/scientific-runtime/v1/tasks/task-guided-1',
    guidedApiRequest: async () => ({ task_id: approvedTask.taskId }),
    normalizeGuidedTaskProjection: () => approvedTask,
    isGuidedApprovedSubmitPending: () => true,
    loadGuidedArtifacts: async () => { artifactLoads += 1; },
    scheduleGuidedPoll: () => { scheduledPolls += 1; },
    upsertGuidedTaskIndex() {},
    renderGuidedFwi() {},
  };
  vm.runInNewContext([
    `async ${extractFunction('refreshGuidedTask')}`,
    'module.exports = { refreshGuidedTask };',
  ].join('\n'), sandbox);

  assert.equal(await sandbox.module.exports.refreshGuidedTask(), true);
  assert.equal(sandbox.state.guided.phase, 'approval_incomplete');
  assert.equal(sandbox.state.guided.outcomeUnknown, false);
  assert.equal(sandbox.state.guided.pollInFlight, false);
  assert.equal(scheduledPolls, 0);
  assert.equal(artifactLoads, 0);

  let generatedKeys = 0;
  const keySandbox = {
    module: { exports: {} },
    state: { guided: { mutationKeys: Object.create(null) } },
    createStableId: prefix => `${prefix}-${++generatedKeys}`,
  };
  vm.runInNewContext([
    extractFunction('guidedMutationKey'),
    'module.exports = { guidedMutationKey };',
  ].join('\n'), keySandbox);
  const firstKey = keySandbox.module.exports.guidedMutationKey('approve', {
    plan_hash: `sha256:${'a'.repeat(64)}`,
  });
  const continuedKey = keySandbox.module.exports.guidedMutationKey('approve', {
    plan_hash: `sha256:${'a'.repeat(64)}`,
  });
  assert.equal(continuedKey, firstKey);
  assert.equal(generatedKeys, 1);
}

async function testGuidedApproveFourXxRetainsOriginalKeyThroughGetRecovery() {
  const taskId = 'task-guided-approve-1';
  const planHash = `sha256:${'b'.repeat(64)}`;
  const catalog = {
    datasets: [{ id: 'marmousi_94_288', version: '1.0.0' }],
    algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' },
  };
  const noApprovalProjection = makeGuidedTask({ task_id: taskId, plan_hash: planHash });
  const approvedSubmitPendingProjection = makeGuidedTask({
    task_id: taskId,
    plan_hash: planHash,
    approval: { approval_id: 'approval-guided-approve-1', decision: 'approved' },
  });
  const submittedProjection = makeGuidedTask({
    task_id: taskId,
    plan_hash: planHash,
    status: 'Queued',
    approval: { approval_id: 'approval-guided-approve-1', decision: 'approved' },
    dispatch: { state: 'pending' },
  });
  // This is the exact partial shape that previously passed the permissive
  // normalizer and was misclassified as a completed approval.
  const malformedSubmittedProjection = {
    task_id: taskId,
    status: 'Queued',
    plan: { plan_hash: planHash },
    approval: { approval_id: 'approval-guided-approve-1', decision: 'approved' },
  };
  const reviewTask = loadGuidedFunctions().normalizeGuidedTaskProjection(
    noApprovalProjection, taskId,
  );
  assert.ok(reviewTask);
  let generatedKeys = 0;
  let getCount = 0;
  let postCount = 0;
  let returnMalformedPost = false;
  const fetchCalls = [];
  const scheduledPolls = [];
  const toasts = [];
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        phase: 'review',
        csrfToken: 'csrf-token-abcdefghijkl',
        task: reviewTask,
        taskId,
        catalog,
        mutation: '',
        mutationKeys: Object.create(null),
        pollInFlight: false,
        generation: 11,
        error: '',
        outcomeUnknown: false,
        approvalReplayAvailable: false,
      },
    },
    createStableId(prefix) {
      generatedKeys += 1;
      return `${prefix}-stable-key-${generatedKeys}`;
    },
    guidedApiPath(resource) {
      return resource === 'approve'
        ? `/api/scientific-runtime/v1/tasks/${taskId}/approve`
        : `/api/scientific-runtime/v1/tasks/${taskId}`;
    },
    async fetch(path, options) {
      fetchCalls.push({ path, method: options.method, key: options.headers['Idempotency-Key'] });
      if (options.method === 'POST') {
        postCount += 1;
        if (returnMalformedPost) {
          return {
            ok: true,
            status: 200,
            async json() { return { ok: true, data: malformedSubmittedProjection }; },
          };
        }
        if (postCount === 1) {
          return {
            ok: false,
            status: 409,
            async json() {
              return { ok: false, error: { code: 'SUBMIT_CONFLICT', message: 'submit conflict after approval' } };
            },
          };
        }
        return {
          ok: true,
          status: 200,
          async json() { return { ok: true, data: submittedProjection }; },
        };
      }
      getCount += 1;
      const projections = [
        malformedSubmittedProjection,
        noApprovalProjection,
        approvedSubmitPendingProjection,
      ];
      return {
        ok: true,
        status: 200,
        async json() {
          return { ok: true, data: projections[getCount - 1] };
        },
      };
    },
    guidedInvalidMutationResponse(message) {
      const error = new Error(message);
      error.guidedOutcomeUnknown = true;
      return error;
    },
    upsertGuidedTaskIndex() {},
    renderGuidedFwi() {},
    clearGuidedPoll() {},
    scheduleGuidedPoll(delay) { scheduledPolls.push(delay); },
    async loadGuidedArtifacts() { throw new Error('queued task must not load artifacts'); },
    showToast(message) { toasts.push(message); },
  };
  vm.runInNewContext([
    "const GUIDED_API_PREFIX = '/api/scientific-runtime/v1';",
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedIdentifier'),
    extractFunction('isSafeGuidedVersion'),
    extractFunction('isSafeGuidedPlanHash'),
    extractFunction('isSafeGuidedCsrfToken'),
    extractFunction('boundedGuidedText'),
    extractFunction('hasGuidedUnsupportedForwardIntent'),
    extractFunction('guidedIntegerValue'),
    extractFunction('guidedLearningRateValue'),
    extractFunction('guidedLearningRateFromMilli'),
    extractFunction('validateGuidedFwiForm'),
    extractFunction('normalizeGuidedPlanOutputs'),
    extractFunction('expectedGuidedFwiPlanOutputs'),
    extractFunction('hasExactGuidedFwiPlanOutputs'),
    extractFunction('normalizeGuidedTimeoutProjection'),
    extractFunction('normalizeGuidedTaskProjection'),
    extractFunction('isGuidedReviewReady'),
    extractFunction('isGuidedApprovedSubmitPending'),
    extractFunction('guidedMutationKey'),
    extractFunction('isGuidedApprovalCompleted'),
    `async ${extractFunction('guidedApiRequest')}`,
    extractFunction('continueGuidedApprovedSubmit'),
    `async ${extractFunction('approveGuidedFwi')}`,
    `async ${extractFunction('refreshGuidedTask')}`,
    extractFunction('closeGuidedFwi'),
    `async ${extractFunction('reopenGuidedTask')}`,
    'module.exports = { approveGuidedFwi, continueGuidedApprovedSubmit, refreshGuidedTask, closeGuidedFwi, reopenGuidedTask };',
  ].join('\n'), sandbox);

  const api = sandbox.module.exports;
  assert.equal(await api.approveGuidedFwi(), false);
  assert.equal(postCount, 1);
  assert.equal(generatedKeys, 1);
  const retainedApprove = sandbox.state.guided.mutationKeys.approve;
  assert.equal(retainedApprove.key, fetchCalls[0].key);
  assert.equal(sandbox.state.guided.phase, 'outcome_unknown');
  assert.equal(sandbox.state.guided.approvalReplayAvailable, true);
  assert.equal(sandbox.state.guided.outcomeUnknown, true);
  assert.match(sandbox.state.guided.error, /submit conflict after approval/);
  assert.deepEqual(scheduledPolls, [0]);

  assert.equal(api.closeGuidedFwi(), false);
  assert.equal(await api.reopenGuidedTask('task-other'), false);
  assert.equal(sandbox.state.guided.mutationKeys.approve, retainedApprove);
  assert.match(toasts[0], /Mutation 尚未闭环/);
  assert.match(toasts[1], /原 Idempotency-Key/);

  assert.equal(await api.refreshGuidedTask(), false);
  assert.equal(sandbox.state.guided.phase, 'outcome_unknown');
  assert.equal(sandbox.state.guided.approvalReplayAvailable, true);
  assert.equal(sandbox.state.guided.outcomeUnknown, true);
  assert.match(sandbox.state.guided.error, /task response 无效/);
  assert.equal(sandbox.state.guided.task, reviewTask, 'malformed GET must not replace the last legal task');
  assert.equal(api.closeGuidedFwi(), false);
  assert.equal(await api.reopenGuidedTask('task-other'), false);
  assert.equal(sandbox.state.guided.mutationKeys.approve, retainedApprove);

  assert.equal(await api.refreshGuidedTask(), true);
  assert.equal(sandbox.state.guided.phase, 'outcome_unknown');
  assert.equal(sandbox.state.guided.approvalReplayAvailable, true);
  assert.match(sandbox.state.guided.error, /尚未证明原 approve mutation.*保留原 Idempotency-Key.*不会生成新 key/);
  assert.equal(api.continueGuidedApprovedSubmit(), false);
  assert.equal(generatedKeys, 1);
  assert.equal(sandbox.state.guided.mutationKeys.approve, retainedApprove);

  assert.equal(await api.refreshGuidedTask(), true);
  assert.equal(sandbox.state.guided.phase, 'approval_incomplete');
  assert.equal(sandbox.state.guided.approvalReplayAvailable, true);
  assert.equal(api.closeGuidedFwi(), false);
  assert.equal(await api.reopenGuidedTask('task-other'), false);
  assert.equal(sandbox.state.guided.mutationKeys.approve, retainedApprove);

  assert.equal(await api.continueGuidedApprovedSubmit(), true);
  assert.equal(postCount, 2);
  assert.equal(generatedKeys, 1);
  assert.equal(fetchCalls.at(-1).key, retainedApprove.key);
  assert.equal(sandbox.state.guided.phase, 'monitoring');
  assert.equal(sandbox.state.guided.outcomeUnknown, false);
  assert.equal(sandbox.state.guided.approvalReplayAvailable, false);

  returnMalformedPost = true;
  sandbox.state.guided.phase = 'review';
  sandbox.state.guided.task = reviewTask;
  sandbox.state.guided.mutation = '';
  sandbox.state.guided.mutationKeys = Object.create(null);
  sandbox.state.guided.error = '';
  sandbox.state.guided.outcomeUnknown = false;
  sandbox.state.guided.approvalReplayAvailable = false;
  assert.equal(await api.approveGuidedFwi(), false);
  assert.equal(postCount, 3);
  assert.equal(generatedKeys, 2);
  const malformedResponseKey = sandbox.state.guided.mutationKeys.approve;
  assert.equal(malformedResponseKey.key, fetchCalls.at(-1).key);
  assert.equal(sandbox.state.guided.phase, 'outcome_unknown');
  assert.equal(sandbox.state.guided.outcomeUnknown, true);
  assert.equal(sandbox.state.guided.approvalReplayAvailable, true);
  assert.match(sandbox.state.guided.error, /缺少合法 task projection/);
  assert.equal(api.closeGuidedFwi(), false);
  assert.equal(await api.reopenGuidedTask('task-other'), false);
  assert.equal(sandbox.state.guided.mutationKeys.approve, malformedResponseKey);
}

function testGuidedStateIsNotPersistedWithChats() {
  const persistenceSource = extractFunction('persistChatState');
  assert.doesNotMatch(persistenceSource, /guided/);
  assert.match(html, /active card stays in session memory/);
  assert.match(html, /discovery index is loaded[\s\S]*scope-bound SQLite API/);
  assert.doesNotMatch(extractFunction('scheduleGuidedPoll'), /localStorage|sessionStorage/);
}

function testGuidedPollingPreservesReaderScrollPosition() {
  function makeHarness(scrollTop) {
    const chatArea = { scrollTop, scrollHeight: 1000, clientHeight: 200 };
    const panel = new GuidedFakeElement('section', {
      onInnerHTML() { chatArea.scrollHeight = 1200; },
    });
    const sandbox = {
      module: { exports: {} },
      state: { guided: { phase: 'loading' } },
      document: {
        getElementById(id) {
          if (id === 'guidedFwiPanel') return panel;
          if (id === 'chatArea') return chatArea;
          return null;
        },
      },
      renderGuidedFormHtml() { return ''; },
      renderGuidedUnsupportedForwardHtml() { return ''; },
      renderGuidedReviewHtml() { return ''; },
      renderGuidedApprovedSubmitPendingHtml() { return ''; },
      renderGuidedTaskHtml() { return ''; },
      renderGuidedArtifactsHtml() { return ''; },
      escapeHtml(value) { return String(value); },
    };
    vm.runInNewContext([
      extractFunction('renderGuidedFwi'),
      'module.exports = { renderGuidedFwi };',
    ].join('\n'), sandbox);
    return { render: sandbox.module.exports.renderGuidedFwi, chatArea, panel };
  }

  const reader = makeHarness(500);
  reader.render();
  assert.equal(reader.chatArea.scrollTop, 500, 'poll render must preserve an exact scrolled-up position');
  assert.equal(reader.panel.scrollIntoViewCalls.length, 0);

  const follower = makeHarness(770);
  follower.render();
  assert.equal(follower.chatArea.scrollTop, 1200, 'a reader within 48px should remain bottom-sticky');
  assert.equal(follower.panel.scrollIntoViewCalls.length, 0);

  const explicitReveal = makeHarness(400);
  explicitReveal.render({ reveal: true });
  assert.equal(explicitReveal.panel.scrollIntoViewCalls.length, 1);
  assert.equal(explicitReveal.panel.scrollIntoViewCalls[0].block, 'start');
  assert.equal(explicitReveal.chatArea.scrollTop, 400);
  explicitReveal.render();
  assert.equal(explicitReveal.panel.scrollIntoViewCalls.length, 1, 'ordinary refresh must not reveal again');
  assert.equal(explicitReveal.chatArea.scrollTop, 400);
}

async function testGuidedSucceededRefreshRendersArtifactsOnce() {
  const taskId = 'task-guided-1';
  const succeededTask = {
    taskId,
    status: 'Succeeded',
    approval: { id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: 'dispatched' },
  };
  const calls = [];
  let renders = 0;
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        taskId,
        generation: 8,
        pollInFlight: false,
        artifactLoadInFlight: false,
        task: null,
        catalog: {},
        artifacts: [],
        error: '',
        outcomeUnknown: false,
        phase: 'monitoring',
      },
    },
    isSafeGuidedOpaqueId: value => value === taskId,
    clearGuidedPoll() {},
    guidedApiPath(resource) {
      return resource === 'artifacts'
        ? `/api/scientific-runtime/v1/tasks/${taskId}/artifacts`
        : `/api/scientific-runtime/v1/tasks/${taskId}`;
    },
    async guidedApiRequest(path) {
      calls.push(path);
      return path.endsWith('/artifacts') ? { artifacts: [{}, {}] } : { task_id: taskId };
    },
    normalizeGuidedTaskProjection: () => succeededTask,
    normalizeGuidedArtifacts: (_data, expectedTaskId) => [
      { taskId: expectedTaskId, artifactId: 'artifact-model' },
      { taskId: expectedTaskId, artifactId: 'artifact-loss' },
    ],
    revokeGuidedArtifactObjectUrls() {},
    async loadGuidedArtifactImages() { return true; },
    isGuidedApprovedSubmitPending: () => false,
    upsertGuidedTaskIndex() {},
    scheduleGuidedPoll() {},
    renderGuidedFwi() { renders += 1; },
  };
  vm.runInNewContext([
    `async ${extractFunction('loadGuidedArtifacts')}`,
    `async ${extractFunction('refreshGuidedTask')}`,
    'module.exports = { refreshGuidedTask };',
  ].join('\n'), sandbox);

  assert.equal(await sandbox.module.exports.refreshGuidedTask(), true);
  assert.deepEqual(calls, [
    `/api/scientific-runtime/v1/tasks/${taskId}`,
    `/api/scientific-runtime/v1/tasks/${taskId}/artifacts`,
  ]);
  assert.equal(sandbox.state.guided.artifacts.length, 2);
  assert.equal(renders, 1, 'Succeeded refresh should render once after the non-rendering artifact GET');
}

async function testGuidedPermanentPurgeIsStrongConfirmedAndStaleSafe() {
  const taskId = 'task-guided-purge-1';
  const summary = {
    taskId,
    status: 'Succeeded',
    goal: 'purge completed FWI',
    visibilityRevision: 7,
    trashedAt: '2026-07-15T10:00:00Z',
    purgeState: '',
  };
  const makeGuidedState = open => ({
    phase: open ? 'results' : 'closed',
    taskId: open ? taskId : '',
    task: open ? { taskId } : null,
    artifacts: open ? [{ artifactId: 'artifact-loss' }] : [],
    artifactObjectUrls: open
      ? { 'artifact-loss': 'blob:guided/purge-me' }
      : Object.create(null),
    mutation: '',
    mutationKeys: Object.create(null),
    generation: 4,
    pollTimer: open ? 99 : null,
    taskIndex: [{ ...summary }],
    taskIndexCursor: '',
    taskIndexPage: 0,
    taskIndexLoading: false,
    taskIndexLoaded: true,
    taskIndexError: '',
    taskIndexView: 'trash',
  });
  const prompts = [];
  const requests = [];
  const toasts = [];
  const marks = [];
  const loads = [];
  const revocations = [];
  let promptValue = null;
  let responseMode = 'success';
  let generatedKeys = 0;
  let guidedRenders = 0;
  let taskIndexRenders = 0;
  let pollClears = 0;
  const successData = {
    task_id: taskId,
    purge_state: 'purged',
    purged_at: '2026-07-15T12:00:00Z',
    local_run_state: 'deleted',
    audit_retained: true,
    replayed: false,
  };
  const sandbox = {
    module: { exports: {} },
    window: {
      prompt(message, defaultValue) {
        prompts.push({ message, defaultValue });
        return promptValue;
      },
      confirm() { throw new Error('purge must use one strong typed confirmation'); },
    },
    state: { guided: makeGuidedState(true) },
    isSafeGuidedOpaqueId: value => value === taskId,
    guidedTaskSummary: () => summary,
    createStableId(prefix) {
      generatedKeys += 1;
      return `${prefix}-stable-${generatedKeys}`;
    },
    guidedApiPath(resource, id) {
      return resource === 'purge'
        ? `/api/scientific-runtime/v1/tasks/${id}/purge` : '';
    },
    clearGuidedPoll() {
      pollClears += 1;
      sandbox.state.guided.pollTimer = null;
    },
    revokeGuidedArtifactObjectUrls() {
      revocations.push(Object.values(sandbox.state.guided.artifactObjectUrls || {}));
      sandbox.state.guided.artifactObjectUrls = Object.create(null);
    },
    renderGuidedFwi() { guidedRenders += 1; },
    renderGuidedTaskIndex() { taskIndexRenders += 1; },
    showToast(message) { toasts.push(message); },
    markTaskRefsPurged(id, purgedAt) {
      marks.push({ taskId: id, purgedAt });
      return true;
    },
    createGuidedFwiState(generation, retained) {
      return {
        ...retained,
        phase: 'closed',
        taskId: '',
        task: null,
        artifacts: [],
        artifactObjectUrls: Object.create(null),
        mutation: '',
        mutationKeys: Object.create(null),
        generation,
        pollTimer: null,
      };
    },
    async loadGuidedTaskIndex(options) {
      loads.push(options);
      return true;
    },
    async guidedApiRequest(path, options) {
      requests.push({
        path,
        method: options.method,
        body: JSON.parse(JSON.stringify(options.body)),
        idempotencyKey: options.idempotencyKey,
        generation: sandbox.state.guided.generation,
        artifacts: sandbox.state.guided.artifacts.length,
        objectUrls: Object.keys(sandbox.state.guided.artifactObjectUrls).length,
      });
      if (responseMode === 'malformed') return { ...successData, audit_retained: false };
      return { ...successData };
    },
  };
  vm.runInNewContext([
    extractFunction('guidedMutationKey'),
    extractFunction('guidedInvalidMutationResponse'),
    `async ${extractFunction('purgeGuidedTask')}`,
    'module.exports = { purgeGuidedTask };',
  ].join('\n'), sandbox);
  const purge = sandbox.module.exports.purgeGuidedTask;

  assert.equal(await purge(taskId), false, 'cancelling typed confirmation must do nothing');
  assert.equal(requests.length, 0);
  promptValue = 'task-guided-purge-WRONG';
  assert.equal(await purge(taskId), false, 'a mismatched task ID must do nothing');
  assert.equal(requests.length, 0);
  assert.match(toasts.at(-1), /任务 ID 不匹配/);

  promptValue = taskId;
  assert.equal(await purge(taskId), true);
  assert.equal(prompts.length, 3, 'each purge attempt uses exactly one typed prompt');
  assert.match(prompts.at(-1).message, /本机 Worker 运行目录和结果文件不可恢复/);
  assert.match(prompts.at(-1).message, /SQLite 任务审计记录仍保留/);
  assert.match(prompts.at(-1).message, /对话内容不会删除/);
  assert.match(prompts.at(-1).message, /请输入完整任务 ID/);
  assert.equal(prompts.at(-1).defaultValue, '');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, `/api/scientific-runtime/v1/tasks/${taskId}/purge`);
  assert.equal(requests[0].method, 'POST');
  assert.deepEqual(requests[0].body, {
    expected_visibility_revision: 7,
    confirmation_task_id: taskId,
  });
  assert.match(requests[0].idempotencyKey, /^guided-purge:task-guided-purge-1-stable-1$/);
  assert.equal(requests[0].generation, 5, 'opening task is invalidated before the purge request');
  assert.equal(requests[0].artifacts, 0);
  assert.equal(requests[0].objectUrls, 0);
  assert.deepEqual(revocations[0], ['blob:guided/purge-me']);
  assert.equal(revocations.length, 2, 'success closes the task through a second defensive revoke');
  assert.equal(pollClears, 2);
  assert.equal(sandbox.state.guided.phase, 'closed');
  assert.equal(sandbox.state.guided.taskId, '');
  assert.equal(sandbox.state.guided.generation, 6);
  assert.equal(sandbox.state.guided.taskIndex.length, 0);
  assert.deepEqual(marks, [{ taskId, purgedAt: successData.purged_at }]);
  assert.equal(loads[0].reset, true);
  assert.equal(loads[0].supersede, true);
  assert.match(toasts.at(-1), /本地运行结果已永久删除.*SQLite 任务审计记录与对话保留/);
  assert.equal(guidedRenders >= 2, true);
  assert.equal(taskIndexRenders >= 1, true);

  sandbox.state.guided = makeGuidedState(false);
  responseMode = 'malformed';
  assert.equal(await purge(taskId), false, 'a malformed success body must fail closed');
  assert.equal(sandbox.state.guided.taskIndex[0].purgeState, 'pending');
  assert.match(toasts.at(-1), /永久删除结果尚未确认.*继续永久删除/);
  const pendingKey = requests.at(-1).idempotencyKey;
  assert.equal(generatedKeys, 2);
  assert.equal(marks.length, 1, 'an invalid response cannot mark conversation refs purged');

  responseMode = 'success';
  assert.equal(await purge(taskId), true);
  assert.equal(requests.at(-1).idempotencyKey, pendingKey);
  assert.equal(generatedKeys, 2, 'continuation must reuse the original purge idempotency key');
  assert.equal(sandbox.state.guided.taskIndex.length, 0);
  assert.equal(marks.length, 2);
}

function testGuidedTaskIndexUsesSafeNormalizedDomText() {
  const created = [];
  const container = new GuidedFakeElement('div');
  const sandbox = {
    module: { exports: {} },
    document: {
      getElementById(id) { return id === 'guidedTaskHistory' ? container : null; },
      createElement(tagName) {
        const element = new GuidedFakeElement(tagName);
        created.push(element);
        return element;
      },
    },
  };
  const source = [
    "const state = { guided: { taskId: '', taskIndex: [], taskIndexCursor: '', taskIndexPage: 0, taskIndexLoading: false, taskIndexError: '', taskIndexView: 'active' } };",
    'const reopenCalls = []; const taskIndexLoads = []; const trashCalls = []; const restoreCalls = []; const purgeCalls = [];',
    'function reopenGuidedTask(taskId) { reopenCalls.push(taskId); }',
    'function loadGuidedTaskIndex(options) { taskIndexLoads.push(options); }',
    'function trashGuidedTask(taskId) { trashCalls.push(taskId); } function restoreGuidedTask(taskId) { restoreCalls.push(taskId); }',
    'function purgeGuidedTask(taskId) { purgeCalls.push(taskId); }',
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedIdentifier'),
    extractFunction('isSafeGuidedVersion'),
    extractFunction('boundedGuidedText'),
    extractFunction('guidedIntegerValue'),
    extractFunction('guidedLearningRateFromMilli'),
    extractFunction('normalizeGuidedTimeoutProjection'),
    extractFunction('normalizeGuidedTaskIndexPage'),
    extractFunction('isGuidedTerminalState'),
    extractFunction('renderGuidedTaskIndex'),
    'module.exports = { state, reopenCalls, taskIndexLoads, trashCalls, restoreCalls, purgeCalls, normalizeGuidedTaskIndexPage, renderGuidedTaskIndex };',
  ].join('\n');
  vm.runInNewContext(source, sandbox);
  const api = sandbox.module.exports;
  const maliciousGoal = '<img src=x onerror=alert(1)>';
  const page = api.normalizeGuidedTaskIndexPage({
    tasks: [
      {
        task_id: 'task-safe:1', status: 'Running', goal: maliciousGoal,
        algorithm: { id: '<script>alert(1)</script>', version: '1.4.0' },
        preset: 'fwi_smoke', device: 'cuda', iterations: 50, seed: 0,
        optimizer: 'adam', learning_rate_milli: 10000,
        wall_time_seconds: 7200, timeout: null,
        purge_state: '',
        relative_path: '../../../../etc/passwd', artifact_url: 'javascript:alert(1)',
      },
      { task_id: "bad' onclick='alert(1)", status: 'Running', goal: 'skip me' },
      { task_id: 'task-unknown-status', status: '<img>', goal: 'skip me too' },
      {
        task_id: 'task-invalid-purge-state', status: 'Succeeded', goal: 'skip purged item',
        purge_state: 'purged',
      },
    ],
    next_cursor: '../../unsafe',
  });
  assert.equal(page.tasks.length, 1);
  assert.equal(page.nextCursor, '');
  assert.equal(page.tasks[0].goal, maliciousGoal);
  assert.equal(page.tasks[0].algorithmId, '');
  assert.equal(page.tasks[0].learningRate, '10');
  assert.equal(Object.hasOwn(page.tasks[0], 'relative_path'), false);
  assert.equal(Object.hasOwn(page.tasks[0], 'artifact_url'), false);
  assert.equal(page.tasks[0].purgeState, '');

  const maxTaskId = 't'.repeat(128);
  for (const viewPrefix of ['a', 't']) {
    const encoded = Buffer.from(`${viewPrefix}:${maxTaskId}`, 'ascii')
      .toString('base64url');
    const maxCursor = `v1_${encoded}`;
    assert.equal(encoded.length, 174);
    assert.equal(
      api.normalizeGuidedTaskIndexPage({ tasks: [], next_cursor: maxCursor }).nextCursor,
      maxCursor,
      'the frontend must retain a maximum-length active/trash view-bound cursor',
    );
  }
  assert.equal(
    api.normalizeGuidedTaskIndexPage({
      tasks: [], next_cursor: `v1_${'A'.repeat(176)}`,
    }).nextCursor,
    '',
  );

  api.state.guided.taskIndex = page.tasks;
  api.renderGuidedTaskIndex();
  assert.equal(container.children.length, 1);
  const row = container.children[0];
  const button = row.children[0];
  assert.equal(row.children.length, 1, 'a Running task cannot be moved to trash');
  const header = button.children[0];
  const label = header.children[0];
  assert.equal(label.textContent, maliciousGoal);
  assert.equal(created.some(element => element.tagName === 'img' || element.tagName === 'script'), false);
  assert.equal(created.reduce((sum, element) => sum + element.innerHTMLWrites, 0), 0);
  assert.equal(button.dataset.taskId, 'task-safe:1');
  button.listeners.click();
  assert.deepEqual(Array.from(api.reopenCalls), ['task-safe:1']);

  api.state.guided.taskIndex[0].status = 'Succeeded';
  api.renderGuidedTaskIndex();
  const terminalRow = container.children[0];
  assert.equal(terminalRow.children[1].textContent, '删除');
  terminalRow.children[1].listeners.click();
  assert.deepEqual(Array.from(api.trashCalls), ['task-safe:1']);
  api.state.guided.taskIndexView = 'trash';
  api.renderGuidedTaskIndex();
  const trashRow = container.children[0];
  assert.equal(trashRow.children[1].textContent, '恢复');
  trashRow.children[1].listeners.click();
  assert.deepEqual(Array.from(api.restoreCalls), ['task-safe:1']);
  assert.equal(trashRow.children[2].textContent, '永久删除');
  trashRow.children[2].listeners.click();
  assert.deepEqual(Array.from(api.purgeCalls), ['task-safe:1']);
  api.state.guided.taskIndex[0].purgeState = 'pending';
  api.renderGuidedTaskIndex();
  const pendingPurgeRow = container.children[0];
  assert.equal(pendingPurgeRow.children[1].disabled, true);
  assert.equal(pendingPurgeRow.children[2].textContent, '继续永久删除');
  pendingPurgeRow.children[2].listeners.click();
  assert.deepEqual(Array.from(api.purgeCalls), ['task-safe:1', 'task-safe:1']);
  const visibilitySource = extractFunction('changeGuidedTaskVisibility');
  assert.match(visibilitySource, /expected_visibility_revision: summary\.visibilityRevision/);
  assert.match(visibilitySource, /isGuidedTerminalState\(summary\.status\)/);
  assert.match(visibilitySource, /summary\.purgeState === 'pending'/);
  assert.match(extractFunction('loadGuidedTaskIndex'), /\?view=\$\{view\}&limit=20/);
  assert.match(extractFunction('setGuidedTaskIndexView'), /supersede: true/);

  api.state.guided.taskIndexView = 'active';
  api.state.guided.taskIndexPage = 5;
  api.state.guided.taskIndexCursor = 'v1_ab';
  api.renderGuidedTaskIndex();
  const navigation = container.children[0];
  assert.equal(navigation.children[0].textContent, '更早任务 · 第 6 页');
  assert.equal(navigation.children[1].textContent, '回到最近任务');
  assert.equal(container.children.at(-1).textContent, '查看下一页（更早）');
  navigation.children[1].listeners.click();
  assert.equal(api.taskIndexLoads.length, 1);
  assert.equal(api.taskIndexLoads[0].reset, true);
}

async function testGuidedTaskIndexDiscardsStaleCrossViewResponses() {
  const pending = Object.create(null);
  const requests = [];
  const refUpdates = [];
  const makeDeferred = () => {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
  };
  pending.active = makeDeferred();
  pending.trash = makeDeferred();
  const sandbox = {
    module: { exports: {} },
    state: {
      guided: {
        csrfToken: 'csrf-token-abcdefghijkl', session: {}, taskIndex: [],
        taskIndexCursor: '', taskIndexPage: 0, taskIndexLoading: false,
        taskIndexLoaded: false, taskIndexError: '', taskIndexView: 'active',
        taskIndexRequestGeneration: 0,
      },
    },
    isSafeGuidedCsrfToken: () => true,
    guidedApiPath: () => '/api/scientific-runtime/v1/tasks',
    renderGuidedTaskIndex() {},
    renderConversationTaskRefs() {},
    updateTaskRefCaches(task) { refUpdates.push(task.taskId); },
    normalizeGuidedTaskIndexPage(value) { return value; },
    async guidedApiRequest(path) {
      requests.push(path);
      return path.includes('view=trash') ? pending.trash.promise : pending.active.promise;
    },
  };
  vm.runInNewContext([
    `async ${extractFunction('loadGuidedTaskIndex')}`,
    'module.exports = { loadGuidedTaskIndex };',
  ].join('\n'), sandbox);
  const load = sandbox.module.exports.loadGuidedTaskIndex;

  const activeRequest = load({ reset: true });
  sandbox.state.guided.taskIndexView = 'trash';
  const trashRequest = load({ reset: true, supersede: true });
  assert.deepEqual(requests, [
    '/api/scientific-runtime/v1/tasks?view=active&limit=20',
    '/api/scientific-runtime/v1/tasks?view=trash&limit=20',
  ]);
  pending.trash.resolve({ tasks: [{ taskId: 'task-trash' }], nextCursor: '' });
  assert.equal(await trashRequest, true);
  assert.deepEqual(
    sandbox.state.guided.taskIndex.map(item => item.taskId),
    ['task-trash'],
  );
  pending.active.resolve({ tasks: [{ taskId: 'task-active-stale' }], nextCursor: '' });
  assert.equal(await activeRequest, false);
  assert.deepEqual(
    sandbox.state.guided.taskIndex.map(item => item.taskId),
    ['task-trash'],
    'the superseded active response cannot land in the trash tab',
  );
  assert.deepEqual(refUpdates, ['task-trash'], 'stale list data cannot refresh conversation caches');
  assert.equal(sandbox.state.guided.taskIndexLoading, false);
}

async function testGuidedTaskIndexTraversesMoreThanOneHundredWithoutHiddenCursorAdvance() {
  const allTasks = Array.from({ length: 125 }, (_, index) => ({
    task_id: `task-page-${String(index).padStart(3, '0')}`,
    status: index % 2 === 0 ? 'Succeeded' : 'Running',
    goal: `durable task ${index}`,
    algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' },
    preset: 'fwi_smoke',
    device: 'cuda',
    iterations: 2,
    seed: index,
    optimizer: 'adam',
    learning_rate_milli: 10000,
    wall_time_seconds: 7200,
    timeout: null,
    created_at: '2026-07-15T01:00:00Z',
    updated_at: '2026-07-15T01:01:00Z',
  }));
  const pageSize = 20;
  const pageCount = Math.ceil(allTasks.length / pageSize);
  const pageCursors = [];
  const cursorToPage = Object.create(null);
  for (let pageIndex = 0; pageIndex < pageCount; pageIndex += 1) {
    const end = Math.min((pageIndex + 1) * pageSize, allTasks.length);
    if (end < allTasks.length) {
      const cursor = `v1_${Buffer.from(allTasks[end - 1].task_id).toString('base64url')}`;
      pageCursors[pageIndex] = cursor;
      cursorToPage[cursor] = pageIndex + 1;
    } else {
      pageCursors[pageIndex] = '';
    }
  }

  const requests = [];
  let renders = 0;
  const sandbox = {
    module: { exports: {} },
    allTasks,
    pageCursors,
    cursorToPage,
    pageSize,
    state: {
      guided: {
        csrfToken: 'csrf-token-1234567890',
        session: null,
        taskIndex: [],
        taskIndexCursor: '',
        taskIndexPage: 0,
        taskIndexLoading: false,
        taskIndexLoaded: false,
        taskIndexError: '',
        taskIndexView: 'active',
      },
    },
    renderGuidedTaskIndex() { renders += 1; },
    updateTaskRefCaches() {},
    renderConversationTaskRefs() {},
    async guidedApiRequest(path) {
      requests.push(path);
      const match = /[?&]cursor=([^&]+)/.exec(path);
      const cursor = match ? decodeURIComponent(match[1]) : '';
      const pageIndex = cursor ? cursorToPage[cursor] : 0;
      if (!Number.isInteger(pageIndex)) throw new Error(`unexpected cursor ${cursor}`);
      const start = pageIndex * pageSize;
      return {
        tasks: allTasks.slice(start, start + pageSize),
        next_cursor: pageCursors[pageIndex] || null,
      };
    },
  };
  vm.runInNewContext([
    "const GUIDED_API_PREFIX = '/api/scientific-runtime/v1';",
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedIdentifier'),
    extractFunction('isSafeGuidedVersion'),
    extractFunction('isSafeGuidedCsrfToken'),
    extractFunction('boundedGuidedText'),
    extractFunction('guidedApiPath'),
    extractFunction('guidedIntegerValue'),
    extractFunction('guidedLearningRateFromMilli'),
    extractFunction('normalizeGuidedTimeoutProjection'),
    extractFunction('normalizeGuidedTaskIndexPage'),
    `async ${extractFunction('loadGuidedTaskIndex')}`,
    'module.exports = { loadGuidedTaskIndex };',
  ].join('\n'), sandbox);

  const load = sandbox.module.exports.loadGuidedTaskIndex;
  const visible = new Set();
  for (let pageIndex = 0; pageIndex < pageCount; pageIndex += 1) {
    const loaded = await load({ reset: pageIndex === 0 });
    assert.equal(loaded, true);
    const expected = allTasks
      .slice(pageIndex * pageSize, (pageIndex + 1) * pageSize)
      .map(item => item.task_id);
    const actual = Array.from(sandbox.state.guided.taskIndex, item => item.taskId);
    assert.deepEqual(actual, expected, `page ${pageIndex + 1} must be fully visible`);
    actual.forEach(taskId => visible.add(taskId));
    assert.equal(sandbox.state.guided.taskIndex.length <= pageSize, true);
    assert.equal(sandbox.state.guided.taskIndexPage, pageIndex);
  }
  assert.equal(visible.size, allTasks.length);
  assert.equal(sandbox.state.guided.taskIndexCursor, '');
  assert.equal(requests.length, pageCount);

  assert.equal(await load({ reset: false }), false);
  assert.equal(requests.length, pageCount, 'an exhausted cursor must not issue or advance a GET');

  assert.equal(await load({ reset: true }), true);
  assert.equal(sandbox.state.guided.taskIndexPage, 0);
  assert.deepEqual(
    Array.from(sandbox.state.guided.taskIndex, item => item.taskId),
    allTasks.slice(0, pageSize).map(item => item.task_id),
  );
  assert.equal(renders > 0, true);
}

function createGuidedRecoveryHarness(task, approvedSubmitPending = false) {
  const requests = [];
  const toasts = [];
  let scheduledPolls = 0;
  let taskIndexRenders = 0;
  let guidedRenders = 0;
  const welcome = new GuidedFakeElement('div');
  const initialSummary = {
    taskId: task.taskId,
    status: task.status,
    goal: task.draft.goal,
    algorithmId: task.draft.algorithmId,
    algorithmVersion: task.draft.algorithmVersion,
    preset: task.draft.preset,
    device: task.draft.device,
    iterations: task.draft.iterations,
    seed: task.draft.seed,
    optimizer: task.draft.optimizer,
    learningRate: task.draft.learningRate,
    learningRateMilli: task.draft.learningRateMilli,
    createdAt: task.createdAt,
    updatedAt: task.updatedAt,
  };
  const sandbox = {
    module: { exports: {} },
    window: { innerWidth: 1024 },
    document: {
      getElementById(id) {
        if (id === 'welcomeMsg') return welcome;
        return null;
      },
    },
    state: {
      guided: {
        phase: 'monitoring',
        csrfToken: 'csrf-token-1234567890',
        session: { mode: 'guided' },
        catalog: { algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' } },
        form: null,
        task,
        taskId: task.taskId,
        artifacts: [],
        mutation: '',
        mutationKeys: Object.create(null),
        pollTimer: null,
        pollInFlight: false,
        artifactLoadInFlight: false,
        generation: 3,
        error: '',
        outcomeUnknown: false,
        approvalReplayAvailable: false,
        taskIndex: [initialSummary],
        taskIndexCursor: 'v1_ab',
        taskIndexPage: 0,
        taskIndexLoading: false,
        taskIndexLoaded: true,
        taskIndexError: '',
        taskIndexView: 'active',
        artifactObjectUrls: Object.create(null),
        artifactImageErrors: Object.create(null),
      },
    },
    clearGuidedPoll() {},
    revokeGuidedArtifactObjectUrls() {},
    updateTaskRefCaches() {},
    renderGuidedFwi() { guidedRenders += 1; },
    renderGuidedTaskIndex() { taskIndexRenders += 1; },
    showToast(message) { toasts.push(message); },
    async guidedApiRequest(path, options) {
      requests.push({ path, method: options && options.method ? options.method : 'GET' });
      if (path.endsWith('/session')) return { csrf_token: 'unused-by-mock' };
      if (path.endsWith('/catalog')) return { algorithms: [] };
      if (path.endsWith(`/tasks/${task.taskId}`)) return { task_id: task.taskId };
      throw new Error(`unexpected path ${path}`);
    },
    normalizeGuidedSession: () => ({ csrfToken: 'csrf-token-abcdefghijkl', mode: 'guided' }),
    normalizeGuidedCatalog: () => ({ algorithm: { id: 'deepwave.acoustic_fwi', version: '1.4.0' } }),
    normalizeGuidedTaskProjection: (_data, expectedTaskId) => (
      expectedTaskId === task.taskId ? task : null
    ),
    isGuidedApprovedSubmitPending: () => approvedSubmitPending,
    isGuidedReviewReady: () => false,
    async loadGuidedArtifacts() { throw new Error('artifacts must not load in this scenario'); },
    scheduleGuidedPoll() { scheduledPolls += 1; },
    toggleSidebar() { throw new Error('desktop recovery must not toggle the sidebar'); },
  };
  vm.runInNewContext([
    "const GUIDED_API_PREFIX = '/api/scientific-runtime/v1';",
    extractFunction('isSafeGuidedOpaqueId'),
    extractFunction('isSafeGuidedCsrfToken'),
    extractFunction('boundedGuidedText'),
    extractFunction('guidedApiPath'),
    extractFunction('createGuidedFwiState'),
    extractFunction('guidedTaskSummary'),
    extractFunction('upsertGuidedTaskIndex'),
    extractFunction('closeGuidedFwi'),
    `async ${extractFunction('reopenGuidedTask')}`,
    'module.exports = { closeGuidedFwi, reopenGuidedTask };',
  ].join('\n'), sandbox);
  return {
    api: sandbox.module.exports,
    sandbox,
    requests,
    toasts,
    counts: {
      get scheduledPolls() { return scheduledPolls; },
      get taskIndexRenders() { return taskIndexRenders; },
      get guidedRenders() { return guidedRenders; },
    },
  };
}

function makeRecoveredGuidedTask(status = 'Running') {
  return {
    taskId: 'task-guided-1',
    status,
    createdAt: '2026-07-15T01:00:00Z',
    updatedAt: '2026-07-15T01:01:00Z',
    draft: {
      goal: 'durable task',
      datasetId: 'marmousi_94_288',
      datasetVersion: '1.0.0',
      algorithmId: 'deepwave.acoustic_fwi',
      algorithmVersion: '1.2.0',
      preset: 'fwi_smoke',
      device: 'cuda',
      iterations: 50,
      seed: 0,
      optimizer: 'adam',
      learningRate: '10',
      learningRateMilli: 10000,
    },
    plan: { id: 'plan-guided-1', hash: `sha256:${'a'.repeat(64)}`, nodeCount: 1 },
    approval: { id: 'approval-guided-1', decision: 'approved' },
    dispatch: { state: status === 'Running' ? 'dispatched' : '' },
    adapter: null,
  };
}

async function testGuidedCloseRetainsIndexAndReopensThroughGets() {
  const task = makeRecoveredGuidedTask('Running');
  const harness = createGuidedRecoveryHarness(task);
  const retainedIndex = harness.sandbox.state.guided.taskIndex;

  assert.equal(harness.api.closeGuidedFwi(), true);
  assert.equal(harness.sandbox.state.guided.phase, 'closed');
  assert.equal(harness.sandbox.state.guided.taskId, '');
  assert.equal(harness.sandbox.state.guided.taskIndex, retainedIndex);
  assert.equal(harness.sandbox.state.guided.taskIndex[0].taskId, task.taskId);
  assert.equal(harness.sandbox.state.guided.taskIndexCursor, 'v1_ab');
  assert.match(harness.toasts.at(-1), /任务未取消.*左栏持久任务/);

  assert.equal(await harness.api.reopenGuidedTask(task.taskId), true);
  assert.deepEqual(harness.requests, [
    { path: '/api/scientific-runtime/v1/session', method: 'GET' },
    { path: '/api/scientific-runtime/v1/catalog', method: 'GET' },
    { path: `/api/scientific-runtime/v1/tasks/${task.taskId}`, method: 'GET' },
  ]);
  assert.equal(harness.sandbox.state.guided.taskId, task.taskId);
  assert.equal(harness.sandbox.state.guided.task, task);
  assert.equal(harness.sandbox.state.guided.catalog.algorithm.version, '1.4.0');
  assert.equal(
    harness.sandbox.state.guided.task.draft.algorithmVersion,
    '1.2.0',
    'a historical 1.2 task remains readable after the current catalog advances',
  );
  assert.equal(harness.sandbox.state.guided.phase, 'monitoring');
  assert.equal(harness.sandbox.state.guided.taskIndex.length, 1);
  assert.equal(harness.sandbox.state.guided.taskIndex[0].taskId, task.taskId);
  assert.equal(harness.counts.scheduledPolls, 1);
}

async function testRecoveredApprovedPendingTaskIsFailClosed() {
  const task = makeRecoveredGuidedTask('AwaitingApproval');
  const harness = createGuidedRecoveryHarness(task, true);

  assert.equal(await harness.api.reopenGuidedTask(task.taskId), true);
  assert.equal(harness.sandbox.state.guided.phase, 'approval_incomplete');
  assert.equal(harness.sandbox.state.guided.approvalReplayAvailable, false);
  assert.match(harness.sandbox.state.guided.error, /缺少原 approve mutation.*不会生成新 key 重发/);
  assert.equal(harness.counts.scheduledPolls, 0);
  assert.equal(Object.keys(harness.sandbox.state.guided.mutationKeys).length, 0);
  assert.equal(harness.requests.every(request => request.method === 'GET'), true);
  assert.equal(harness.api.closeGuidedFwi(), true);
  assert.equal(harness.sandbox.state.guided.phase, 'closed');
  assert.equal(harness.sandbox.state.guided.taskId, '');
  assert.equal(harness.sandbox.state.guided.taskIndex[0].taskId, task.taskId);
  assert.match(harness.toasts.at(-1), /任务未取消.*左栏持久任务/);

  const replayable = createGuidedRecoveryHarness(task, true);
  replayable.sandbox.state.guided.phase = 'approval_incomplete';
  replayable.sandbox.state.guided.approvalReplayAvailable = true;
  assert.equal(await replayable.api.reopenGuidedTask('task-other'), false);
  assert.equal(replayable.api.closeGuidedFwi(), false);
  assert.equal(replayable.sandbox.state.guided.phase, 'approval_incomplete');
  assert.equal(replayable.sandbox.state.guided.taskId, task.taskId);
  assert.equal(replayable.requests.length, 0);
  assert.match(replayable.toasts[0], /原 Idempotency-Key/);
  assert.match(replayable.toasts[1], /Mutation 尚未闭环/);
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
  testLegacyFwiReceiptSectionOnlyAppearsForCompatibilityState();
  testHonestFwiControlsAndNoPlaceholderFeatures();
  testGrpcModeIsHealthGatedAndFallsBack();
  testConversationStorageRecoveryIdentityAndClearing();
  testHistoryUsesSafeDomAndDataAttributes();
  testExecutionIntentIsStoredBeforeIndependentDraft();
  testConversationTaskLinksAreManyToManyAndIndependent();
  testChatDeleteConfirmsAndRollsBackWhenStorageFails();
  testRequestsAreBoundAndStreamFailuresAreNotReplayed();
  await testHttpFailureIsNotAutomaticallyReplayed();
  await testLegacyFwiSubmitIsDeniedByEveryChatTransport();
  testChatAndSystemTextAreEscaped();
  testEmbeddingStatusUsesSameOriginHealthProxy();
  testGuidedFormHasStrictBoundaries();
  testGuidedForwardIsExplicitlyBlocked();
  testGuidedIdentifiersAndRoutesAreConstrained();
  testGuidedTaskAndCrashStatesAreHonest();
  await testGuidedRuntimeCancelUsesOneDurableMutation();
  testGuidedArtifactManifestsUseControlledDownloads();
  testCurrentEightAndHistoricalTwoArtifactContracts();
  await testGuidedImageBlobLoadingIsBoundedAndRevoked();
  await testGuidedImageRetriesAreSingleFlightAndStaleSafe();
  testGuidedCatalogProjectionDoesNotExposePaths();
  testGuidedApprovalCannotBeBypassedOrReplayedAutomatically();
  await testGuidedApprovedSubmitPendingStopsPolling();
  await testGuidedApproveFourXxRetainsOriginalKeyThroughGetRecovery();
  testGuidedStateIsNotPersistedWithChats();
  testGuidedPollingPreservesReaderScrollPosition();
  await testGuidedSucceededRefreshRendersArtifactsOnce();
  await testGuidedPermanentPurgeIsStrongConfirmedAndStaleSafe();
  testGuidedTaskIndexUsesSafeNormalizedDomText();
  await testGuidedTaskIndexDiscardsStaleCrossViewResponses();
  await testGuidedTaskIndexTraversesMoreThanOneHundredWithoutHiddenCursorAdvance();
  await testGuidedCloseRetainsIndexAndReopensThroughGets();
  await testRecoveredApprovedPendingTaskIsFailClosed();
  console.log('ui message rendering tests passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
