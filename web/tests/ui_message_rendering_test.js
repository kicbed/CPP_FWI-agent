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
    extractFunction('renderMarkdownFallback'),
    extractFunction('renderMarkdown'),
    'module.exports = { escapeHtml, extractAnswer, extractStreamText, renderMarkdownFallback, renderMarkdown };',
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

testStreamChunkExtraction();
testMarkdownRendererUsesLibraryAndSanitizer();
testMathJaxConfigSkipsCode();
console.log('ui message rendering tests passed');
