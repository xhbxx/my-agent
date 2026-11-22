import { createAPI } from './api.js';
import { createManagement } from './management.js';
import { byId, renderSource, renderResult, renderEvidence, renderMarkdown, downloadCSV } from './render.js';

const state = { api: null, canManage: false, sources: [], busy: false, requestID: null, controller: null, pending: null, result: null, sql: '', failed: false, startedAt: 0, timer: 0 };
const management = createManagement(() => state.api);
let knowledgeRequest;
let documentRequest;
let uploading = false;
let startupLoading = true;
let selectedPreset;
const presetDocument = '销售查询示例.md';
const presets = {
  region: '2026 年 1 月，哪个区域的有效销售额最高？',
  trend: '2026 年 1 月，每日有效销售额走势如何？',
  orders: '2026 年 1 月，每个区域有多少有效订单？',
};
const stageNames = { retrieval: '正在检索表结构与业务知识…', generation: '正在生成查询 SQL…', validation: '正在检查访问权限与 SQL…', cost_check: '正在评估查询成本…', execution: '正在执行只读查询…', explanation: '查询完成，正在解释结果…' };

function toggleNavigation(open) {
  byId('navigation').classList.toggle('is-open', open);
  byId('toggle-navigation').setAttribute('aria-expanded', String(open));
  byId('toggle-navigation').textContent = open ? '关闭菜单' : '菜单';
}

function showView(view) {
  toggleNavigation(false);
  for (const button of document.querySelectorAll('[data-view]')) {
    const selected = button.dataset.view === view;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
    byId(`view-${button.dataset.view}`).hidden = !selected;
  }
  byId('analysis-layout').classList.toggle('management-layout', ['sources', 'settings'].includes(view));
  byId('analysis-layout').classList.toggle('is-analysis', view === 'analysis');
  byId('analysis-layout').classList.toggle('is-knowledge', view === 'knowledge');
  byId('workspace-title').textContent = { analysis: '分析工作台', sources: '工作区 / 数据源', knowledge: '工作区 / 知识库', settings: '工作区 / 设置' }[view];
  const target = byId({ analysis: 'question', sources: 'sources-heading', knowledge: 'knowledge-heading', settings: 'settings-heading' }[view]);
  if (view === 'knowledge') {
    syncKnowledgeView();
    target.scrollIntoView({ block: 'nearest' });
  }
  target.focus({ preventScroll: view === 'knowledge' });
  if (view === 'sources') void management.loadConnections();
  if (view === 'settings') void management.loadModel();
}

function syncKnowledgeView() {
  const source = state.sources.find(item => item.name === byId('source').value);
  const availability = byId('knowledge-availability');
  byId('knowledge-content').hidden = !source;
  availability.hidden = Boolean(source);
  if (!source) {
    byId('knowledge-source').textContent = '';
    availability.textContent = startupLoading
      ? '正在加载数据源…'
      : state.api
        ? '当前没有可分析的数据源。关联并授权数据源后，才能查看它的知识库。'
        : '数据源未加载。请在页面上方重试。';
    if (!startupLoading && !state.api) {
      byId('startup-alert').scrollIntoView({ block: 'nearest' });
    }
  }
}

function resetKnowledge() {
  knowledgeRequest?.abort();
  knowledgeRequest = undefined;
  byId('knowledge-search').disabled = false;
  byId('knowledge-status').textContent = '';
  byId('knowledge-results').replaceChildren();
}

function closeDocument() {
  documentRequest?.abort();
  documentRequest = undefined;
  byId('document-preview').hidden = true;
  byId('document-preview-body').replaceChildren();
}

async function openDocument(filename) {
  if (!state.api) return;
  closeDocument();
  const request = new AbortController();
  documentRequest = request;
  byId('document-preview').hidden = false;
  byId('document-preview-title').textContent = filename;
  byId('document-preview-title').focus();
  const status = byId('document-preview-status');
  status.textContent = '正在读取文档…';
  try {
    const data = await state.api.document(byId('source').value, filename, request.signal);
    if (documentRequest !== request) return;
    renderMarkdown(data.excerpt, byId('document-preview-body'));
    status.textContent = data.truncated ? '预览仅显示文档开头。' : '';
  } catch (error) {
    if (documentRequest === request) status.textContent = error.message;
  }
}

function clearPreset() {
  selectedPreset = undefined;
  document.querySelectorAll('[data-preset]').forEach(button => button.setAttribute('aria-pressed', 'false'));
}

function feedback(message, notice = false) {
  byId('feedback').hidden = !message;
  byId('feedback').textContent = message;
  byId('feedback').classList.toggle('notice', notice);
}

function stopTimer() {
  clearInterval(state.timer);
  state.timer = 0;
}

function startTimer() {
  stopTimer();
  state.startedAt = Date.now();
  byId('elapsed').textContent = '已用时 0 秒';
  state.timer = setInterval(() => {
    byId('elapsed').textContent = `已用时 ${Math.floor((Date.now() - state.startedAt) / 1000)} 秒`;
  }, 1000);
}

function setBusy(busy) {
  state.busy = busy;
  for (const id of ['submit', 'source', 'skill', 'question', 'new-analysis', 'explain']) byId(id).disabled = busy || !state.sources.length;
  document.querySelectorAll('[data-preset]').forEach(button => { button.disabled = busy; });
  byId('stop').hidden = !busy;
  byId('submit').textContent = busy ? '分析中…' : '开始分析';
  byId('activity').setAttribute('aria-busy', String(busy));
}

function tab(name) {
  document.querySelectorAll('[data-tab]').forEach(button => {
    const selected = button.dataset.tab === name;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  for (const item of ['result', 'sql', 'evidence']) byId(`${item}-panel`).hidden = item !== name;
}

function reset() {
  stopTimer();
  state.pending = null;
  state.result = null;
  state.sql = '';
  state.failed = false;
  for (const id of ['confirmation', 'activity', 'output', 'feedback', 'result-content', 'explanation', 'chart']) byId(id).hidden = true;
  byId('view-analysis').classList.remove('has-analysis');
  byId('result-placeholder').hidden = false;
  byId('result-placeholder').textContent = '正在处理，查询结果将在这里显示。';
  byId('explanation').replaceChildren();
  byId('sql').textContent = '尚未生成 SQL。';
  byId('copy-sql').textContent = '复制 SQL';
  byId('evidence').replaceChildren();
  byId('elapsed').textContent = '';
  document.querySelectorAll('[data-stage]').forEach(item => item.classList.remove('current', 'complete'));
  tab('result');
}

function sourceChanged() {
  if (state.busy) return;
  reset();
  resetKnowledge();
  closeDocument();
  if (selectedPreset) byId('question').value = '';
  clearPreset();
  const source = state.sources.find(item => item.name === byId('source').value);
  if (source) {
    renderSource(source, openDocument);
    byId('analysis-presets').hidden = source.name !== 'sales_demo';
    byId('open-preset-document').hidden = !source.documents.includes(presetDocument);
    const allowed = state.canManage && source.can_upload;
    for (const id of ['add-knowledge-document', 'upload-help']) byId(id).hidden = !allowed;
    byId('knowledge-upload-status').textContent = '';
  }
  syncKnowledgeView();
}

async function initialize() {
  startupLoading = true;
  byId('retry-startup').disabled = true;
  byId('startup-alert').hidden = true;
  syncKnowledgeView();
  try {
    const api = createAPI();
    const data = await api.sources();
    state.api = api;
    state.canManage = data.can_manage;
    for (const view of ['settings', 'sources']) {
      document.querySelector(`[data-view="${view}"]`).hidden = !state.canManage;
    }
    state.sources = data.details;
    byId('source').replaceChildren(...data.sources.map(name => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      return option;
    }));
    if (!data.sources.length) byId('source').append(new Option('暂无可分析的数据源', ''));
    sourceChanged();
    if (!data.sources.length) feedback(state.canManage ? '还没有可分析的数据源。请先在「数据源」添加连接，并配置只读访问与表字段白名单。' : '还没有可分析的数据源。请联系管理员配置授权数据源。', true);
    setBusy(false);
    if (!byId('view-settings').hidden) void management.loadModel();
    if (!byId('view-sources').hidden) void management.loadConnections();
  } catch (error) {
    byId('startup-error').textContent = error.message;
    byId('startup-alert').hidden = false;
  } finally {
    startupLoading = false;
    byId('startup-status').hidden = true;
    byId('retry-startup').disabled = false;
    syncKnowledgeView();
  }
}

function finishStages(complete) {
  document.querySelectorAll('[data-stage].current').forEach(item => {
    item.classList.remove('current');
    if (complete) item.classList.add('complete');
  });
}

function processEvent(item, body) {
  const data = item.data;
  switch (item.event) {
    case 'started': state.requestID = data.request_id; break;
    case 'stage': {
      const previous = document.querySelector('[data-stage].current');
      if (previous) { previous.classList.remove('current'); previous.classList.add('complete'); }
      document.querySelector(`[data-stage="${data.name}"]`)?.classList.add('current');
      byId('activity-status').textContent = stageNames[data.name] || '正在分析…';
      break;
    }
    case 'context': renderEvidence(data); break;
    case 'sql': state.sql = data.sql; byId('sql').textContent = data.sql; break;
    case 'result': state.result = data; renderResult(data); break;
    case 'explanation': renderMarkdown(data.text); break;
    case 'clarification': feedback(data.message, true); byId('activity-status').textContent = '请补充问题后重新分析'; break;
    case 'warning': feedback(data.message, true); break;
    case 'error': state.failed = true; feedback(data.message); byId('activity-status').textContent = '分析未完成'; break;
    case 'confirmation_required': {
      state.pending = { ...body, sql: data.sql, approval_id: data.approval_id, expires: Date.now() + data.expires_in * 1000 };
      byId('confirmation').hidden = false;
      byId('confirmation-sql').textContent = data.sql;
      byId('risk-reasons').replaceChildren(...data.cost.reasons.map(reason => {
        const li = document.createElement('li'); li.textContent = reason; return li;
      }));
      byId('confirmation-expiry').textContent = '确认有效期 5 分钟；确认后会重新检查权限与执行计划。';
      byId('activity-status').textContent = '等待你的执行确认 · 尚未查询数据';
      byId('confirmation').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      byId('approve').focus({ preventScroll: true });
      break;
    }
    case 'done':
      finishStages(!state.failed);
      if (state.result && !state.failed) byId('activity-status').textContent = '分析完成';
      break;
  }
}

async function run(body, continuing = false) {
  if (!state.api || state.busy) return;
  if (!continuing) reset();
  state.pending = null;
  state.failed = false;
  byId('confirmation').hidden = true;
  byId('activity').hidden = false;
  byId('output').hidden = false;
  byId('view-analysis').classList.add('has-analysis');
  feedback('');
  state.controller = new AbortController();
  setBusy(true);
  startTimer();
  try {
    await state.api.query(body, state.controller.signal, item => processEvent(item, body));
    if (!state.result && !state.failed && !state.pending) {
      byId('result-placeholder').textContent = '没有生成可显示的查询结果。可换一种问法，或查看「检索依据」确认相关表和指标已被召回。';
    }
  } catch (error) {
    state.failed = true;
    finishStages(false);
    const cancelled = error.name === 'AbortError';
    byId('activity-status').textContent = cancelled ? '已请求停止' : '分析未完成';
    byId('result-placeholder').textContent = cancelled ? '分析已停止。' : '分析没有完成，请查看上方提示后重试。';
    feedback(cancelled ? '已停止接收结果，并请求终止查询。' : error.message, cancelled);
  } finally {
    stopTimer();
    state.controller = null;
    state.requestID = null;
    setBusy(false);
  }
}

byId('retry-startup').addEventListener('click', () => void initialize());
byId('question-form').addEventListener('submit', event => {
  event.preventDefault();
  const question = byId('question').value.trim();
  if (!question) return;
  const source = byId('source').value;
  void run({ source, question, skill: byId('skill').value, explain: byId('explain').checked });
});
byId('question').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    byId('question-form').requestSubmit();
  }
});
byId('approve').addEventListener('click', () => {
  if (!state.pending) return;
  if (Date.now() >= state.pending.expires) { feedback('确认已过期，请重新发起分析。'); byId('confirmation').hidden = true; state.pending = null; return; }
  const { expires, ...body } = state.pending;
  void run(body, true);
});
byId('reject').addEventListener('click', () => {
  state.pending = null;
  byId('confirmation').hidden = true;
  byId('activity-status').textContent = '已取消本次查询，未执行数据查询';
  byId('result-placeholder').textContent = '已取消，未执行数据查询。';
});
byId('stop').addEventListener('click', () => {
  if (state.requestID) void state.api.cancel(state.requestID).catch(() => {});
  state.controller?.abort();
});
byId('source').addEventListener('change', sourceChanged);
document.querySelectorAll('[data-view]').forEach(button => {
  button.addEventListener('click', () => showView(button.dataset.view));
});
document.querySelectorAll('[data-open-analysis]').forEach(button => {
  button.addEventListener('click', () => showView('analysis'));
});
document.querySelectorAll('[data-open-knowledge]').forEach(button => {
  button.addEventListener('click', () => showView('knowledge'));
});
byId('new-analysis').addEventListener('click', () => {
  if (state.busy) return;
  reset();
  clearPreset();
  byId('question').value = '';
  showView('analysis');
});
byId('question').addEventListener('input', clearPreset);
document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
  clearPreset();
  selectedPreset = button.dataset.preset;
  byId('question').value = presets[selectedPreset];
  button.setAttribute('aria-pressed', 'true');
  byId('question').focus();
}));
byId('open-preset-document').addEventListener('click', () => {
  showView('knowledge');
  void openDocument(presetDocument);
});
byId('close-document-preview').addEventListener('click', () => {
  closeDocument();
  byId('document-list').querySelector('button')?.focus();
});
document.querySelectorAll('[data-tab]').forEach(button => {
  button.addEventListener('click', () => tab(button.dataset.tab));
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('[data-tab]')];
    const index = (tabs.indexOf(button) + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    tab(tabs[index].dataset.tab); tabs[index].focus(); event.preventDefault();
  });
});
const knowledgeFile = byId('knowledge-document-file');
const uploadButton = byId('add-knowledge-document');
uploadButton.addEventListener('click', () => knowledgeFile.click());
knowledgeFile.addEventListener('change', async () => {
  const file = knowledgeFile.files[0];
  knowledgeFile.value = '';
  const api = state.api;
  const name = byId('source').value;
  if (!file || !api || uploading) return;
  const status = byId('knowledge-upload-status');
  status.classList.remove('is-error');
  if (file.size === 0 || file.size > 10 * 1024 * 1024) {
    status.classList.add('is-error');
    status.textContent = '请选择非空文件，单个文件不超过 10 MB。';
    return;
  }
  uploading = true;
  uploadButton.disabled = true;
  status.textContent = '正在上传并解析文档…';
  try {
    const query = new URLSearchParams({ source: name, filename: file.name });
    const { filename } = await api.management(`knowledge?${query}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    });
    if (state.api !== api) return;
    const source = state.sources.find(item => item.name === name);
    if (source && !source.documents.includes(filename)) source.documents.push(filename);
    if (source && byId('source').value === name) {
      renderSource(source, openDocument);
      status.textContent = `已添加「${filename}」，可在下方检索。`;
    }
  } catch (error) {
    if (state.api === api && byId('source').value === name) {
      status.classList.add('is-error');
      status.textContent = error.message;
    }
  } finally {
    uploading = false;
    uploadButton.disabled = false;
  }
});
byId('knowledge-form').addEventListener('submit', async event => {
  event.preventDefault();
  const question = byId('knowledge-question').value.trim();
  const source = byId('source').value;
  if (!question || !state.api || !source) return;

  resetKnowledge();
  const request = new AbortController();
  knowledgeRequest = request;
  const status = byId('knowledge-status');
  byId('knowledge-search').disabled = true;
  status.textContent = '正在检索…';
  try {
    const { evidence } = await state.api.context(source, question, request.signal);
    if (knowledgeRequest !== request) return;
    renderEvidence(evidence, byId('knowledge-results'));
    status.textContent = evidence.length ? `返回 ${evidence.length} 条证据` : '未找到相关证据，可换成指标名或字段名再试。';
  } catch (error) {
    if (knowledgeRequest === request) status.textContent = error.message;
  } finally {
    if (knowledgeRequest === request) {
      knowledgeRequest = undefined;
      byId('knowledge-search').disabled = false;
    }
  }
});
byId('download').addEventListener('click', () => { if (state.result) downloadCSV(state.result); });
byId('copy-sql').addEventListener('click', async () => {
  if (!state.sql) return;
  try {
    await navigator.clipboard.writeText(state.sql);
    byId('copy-sql').textContent = '已复制';
    setTimeout(() => { byId('copy-sql').textContent = '复制 SQL'; }, 2000);
  } catch { feedback('浏览器没有剪贴板权限，可以直接选中 SQL 复制。', true); }
});
try { sessionStorage.removeItem('data-agent-token'); } catch { /* Browser storage is optional. */ }
void initialize();
