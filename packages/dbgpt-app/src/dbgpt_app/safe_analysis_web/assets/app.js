import { createAPI } from './api.js';
import { byId, renderSource, renderResult, renderEvidence, renderMarkdown, downloadCSV } from './render.js';

const state = { api: null, sources: [], model: '', busy: false, requestID: null, controller: null, pending: null, result: null, sql: '', failed: false };
const stageNames = { retrieval: '正在检索表结构与业务知识…', generation: '正在生成查询 SQL…', validation: '正在检查访问权限与 SQL…', cost_check: '正在评估查询成本…', execution: '正在执行只读查询…', explanation: '查询完成，正在解释结果…' };

function toggleNavigation(open) {
  byId('navigation').classList.toggle('is-open', open);
  byId('toggle-navigation').setAttribute('aria-expanded', String(open));
  byId('toggle-navigation').textContent = open ? '关闭菜单' : '菜单';
}

byId('toggle-navigation').addEventListener('click', () => {
  toggleNavigation(byId('toggle-navigation').getAttribute('aria-expanded') !== 'true');
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') toggleNavigation(false);
});

function feedback(message, notice = false) {
  byId('feedback').hidden = !message;
  byId('feedback').textContent = message;
  byId('feedback').classList.toggle('notice', notice);
}

function setBusy(busy) {
  state.busy = busy;
  for (const id of ['submit', 'source', 'skill', 'question', 'demo-query', 'new-analysis', 'check-retrieval', 'explain']) byId(id).disabled = busy;
  byId('stop').hidden = !busy;
  byId('submit').textContent = busy ? '分析中…' : '开始分析';
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
  state.pending = null;
  state.result = null;
  state.sql = '';
  state.failed = false;
  for (const id of ['confirmation', 'activity', 'output', 'feedback', 'result-content', 'explanation', 'chart']) byId(id).hidden = true;
  byId('empty-state').hidden = false;
  byId('result-placeholder').hidden = false;
  byId('explanation').replaceChildren();
  byId('sql').textContent = '尚未生成 SQL。';
  byId('copy-sql').textContent = '复制 SQL';
  byId('evidence').replaceChildren();
  document.querySelectorAll('[data-stage]').forEach(item => item.classList.remove('current', 'complete'));
  tab('result');
}

function sourceChanged() {
  if (state.busy) return;
  reset();
  const source = state.sources.find(item => item.name === byId('source').value);
  if (source) renderSource(source, state.model);
}

async function connect(token) {
  byId('connect-button').disabled = true;
  byId('connect-button').textContent = '正在连接…';
  byId('connect-error').textContent = '';
  try {
    const api = createAPI(token);
    const data = await api.sources();
    if (!data.sources.length) throw new Error('当前令牌没有可用数据源，请在服务端配置授权。');
    state.api = api;
    state.sources = data.details;
    state.model = data.model;
    sessionStorage.setItem('data-agent-token', token);
    byId('access-token').value = '';
    byId('source').replaceChildren(...data.sources.map(name => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      return option;
    }));
    byId('connect-panel').hidden = true;
    byId('analysis-layout').hidden = false;
    byId('disconnect').hidden = false;
    sourceChanged();
  } catch (error) {
    byId('connect-error').textContent = error.message;
    sessionStorage.removeItem('data-agent-token');
  } finally {
    byId('connect-button').disabled = false;
    byId('connect-button').textContent = '连接工作台';
  }
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
    case 'context': renderEvidence(data); byId('index-status').textContent = '本次业务知识检索成功'; break;
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
      break;
    }
    case 'done':
      document.querySelectorAll('[data-stage].current').forEach(item => {
        item.classList.remove('current');
        if (!state.failed) item.classList.add('complete');
      });
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
  byId('empty-state').hidden = true;
  feedback('');
  state.controller = new AbortController();
  setBusy(true);
  try {
    await state.api.query(body, state.controller.signal, item => processEvent(item, body));
  } catch (error) {
    state.failed = true;
    const cancelled = error.name === 'AbortError';
    byId('activity-status').textContent = cancelled ? '已请求停止' : '分析未完成';
    feedback(cancelled ? '已停止接收结果，并请求终止查询。' : error.message, cancelled);
  } finally {
    state.controller = null;
    state.requestID = null;
    setBusy(false);
  }
}

byId('connect-form').addEventListener('submit', event => {
  event.preventDefault();
  void connect(byId('access-token').value.trim());
});
byId('question-form').addEventListener('submit', event => {
  event.preventDefault();
  const question = byId('question').value.trim();
  if (question) void run({ source: byId('source').value, question, skill: byId('skill').value, explain: byId('explain').checked });
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
});
byId('stop').addEventListener('click', () => {
  if (state.requestID) void state.api.cancel(state.requestID).catch(() => {});
  state.controller?.abort();
});
byId('source').addEventListener('change', sourceChanged);
byId('new-analysis').addEventListener('click', () => { if (!state.busy) { reset(); byId('question').value = ''; toggleNavigation(false); byId('question').focus(); } });
byId('demo-query').addEventListener('click', () => {
  byId('question').value = '查看一条示例销售记录';
  void run({ source: 'sales_demo', sql: 'SELECT id, region, amount FROM sales_public WHERE id=1', explain: false });
});
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  byId('question').value = button.dataset.question;
  byId('question').focus();
}));
document.querySelectorAll('[data-tab]').forEach(button => {
  button.addEventListener('click', () => tab(button.dataset.tab));
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('[data-tab]')];
    const index = (tabs.indexOf(button) + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    tab(tabs[index].dataset.tab); tabs[index].focus(); event.preventDefault();
  });
});
byId('check-retrieval').addEventListener('click', async () => {
  if (!state.api || state.busy) return;
  const button = byId('check-retrieval');
  setBusy(true); byId('stop').hidden = true;
  button.textContent = '正在检查…'; feedback('');
  try {
    const data = await state.api.context(byId('source').value, byId('question').value.trim() || '有效销售额的指标口径与表结构');
    renderEvidence(data.evidence);
    byId('output').hidden = false; byId('empty-state').hidden = true;
    byId('index-status').textContent = '知识检索成功 · 已返回实际证据';
    tab('evidence');
  } catch (error) {
    feedback(error.message); byId('index-status').textContent = '检索未成功，请检查模型与向量库配置';
  } finally { setBusy(false); button.textContent = '检查知识检索'; }
});
byId('download').addEventListener('click', () => { if (state.result) downloadCSV(state.result); });
byId('copy-sql').addEventListener('click', async () => {
  if (!state.sql) return;
  try { await navigator.clipboard.writeText(state.sql); byId('copy-sql').textContent = '已复制'; }
  catch { feedback('浏览器没有剪贴板权限，可以直接选中 SQL 复制。', true); }
});
byId('disconnect').addEventListener('click', () => {
  toggleNavigation(false);
  state.controller?.abort(); state.api = null; sessionStorage.removeItem('data-agent-token');
  byId('analysis-layout').hidden = true; byId('connect-panel').hidden = false;
  byId('connection-status').textContent = '尚未连接'; byId('disconnect').hidden = true;
});

const savedToken = sessionStorage.getItem('data-agent-token');
if (savedToken) void connect(savedToken);
