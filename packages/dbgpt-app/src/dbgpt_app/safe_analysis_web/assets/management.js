import { byId } from './render.js';

const labels = {
  host: '主机', port: '端口', user: '用户名', username: '用户名',
  password: '密码', database: '数据库名称', path: '文件路径', schema: 'Schema',
};
const databaseLabels = {
  mysql: 'MySQL', postgresql: 'PostgreSQL', sqlite: 'SQLite', duckdb: 'DuckDB',
  mssql: 'SQL Server', clickhouse: 'ClickHouse', oracle: 'Oracle', oceanbase: 'OceanBase',
  doris: 'Apache Doris', starrocks: 'StarRocks', gaussdb: 'GaussDB', opengauss: 'openGauss',
  vertica: 'Vertica', spark: 'Spark', hive: 'Hive', maxcompute: 'MaxCompute',
  tugraph: 'TuGraph', neo4j: 'Neo4j',
};
const databaseLabel = type => databaseLabels[type.name.toLowerCase()] || type.label || type.name;
const modelKinds = {
  chat: { title: '对话模型', purpose: '理解问题、生成 SQL、解释结果。', endpoint: 'chat/completions', defaultLabel: '使用启动配置' },
  embedding: { title: '向量模型', purpose: '将问题和知识文档转换为向量，在 Qdrant 中按语义检索。', endpoint: 'embeddings', defaultLabel: '使用启动配置中的向量模型' },
  rerank: { title: '重排模型', purpose: '对召回的内容重新排序，优先使用与问题最相关的证据。', endpoint: 'rerank', defaultLabel: '未启用重排' },
};

function databaseIcon(kind) {
  const icon = element('img', undefined, 'database-icon');
  icon.src = '/safe-analysis/database-icons/' + encodeURIComponent(kind.toLowerCase());
  icon.alt = '';
  icon.width = 24;
  icon.height = 24;
  return icon;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function option(value, text = value) {
  const node = element('option', text);
  node.value = value;
  return node;
}

function status(id, message = '', error = false) {
  const node = byId(id);
  node.textContent = message;
  node.classList.toggle('is-error', error);
}

function action(text, run) {
  const node = element('button', text, 'button secondary row-action');
  node.type = 'button';
  node.addEventListener('click', run);
  return node;
}

function renderField(field, previous, path, editing) {
  const name = field.param_name;
  const id = `db-field-${path}-${name}`;
  const wrapper = element('div', undefined, 'management-field');
  const label = element('label', labels[name] || field.label || name);
  label.htmlFor = id;
  const tags = field.ext_metadata?.tags || [];
  const privateField = tags.includes('privacy') || /password|passwd|pwd|secret|token|api_key/i.test(name);
  const value = previous ?? field.default_value;
  let input;
  let read;
  if (field.nested_fields) {
    input = element('select');
    const group = element('div', undefined, 'nested-fields');
    let nestedReads = [];
    input.replaceChildren(option('', '请选择'), ...Object.keys(field.nested_fields).map(key => option(key)));
    input.value = value?.type || '';
    const update = () => {
      const kind = input.value;
      const nested = (field.nested_fields[kind] || []).map(child =>
        renderField(child, value?.type === kind ? value[child.param_name] : undefined, id, editing && value?.type === kind),
      );
      nestedReads = nested.map(item => item.read);
      group.replaceChildren(...nested.map(item => item.wrapper));
    };
    input.addEventListener('change', update);
    update();
    read = () => input.value ? { type: input.value, ...readParams(nestedReads) } : undefined;
    wrapper.append(label, input, group);
  } else if (field.valid_values?.length && !privateField) {
    input = element('select');
    const values = field.valid_values.map(item => typeof item === 'object' ? item : { key: item, label: item });
    input.replaceChildren(option('', '请选择'), ...values.map(item => option(item.key, item.label ?? item.key)));
    input.value = value ?? '';
    read = () => values.find(item => String(item.key) === input.value)?.key;
  } else {
    input = element('input');
    const type = field.param_type?.toLowerCase();
    if (field.is_array && !privateField) {
      input.placeholder = '以 JSON 数组填写，例如 ["a", "b"]';
      input.value = value == null ? '' : JSON.stringify(value);
      read = () => {
        if (!input.value.trim()) return undefined;
        try {
          const parsed = JSON.parse(input.value);
          if (Array.isArray(parsed)) return parsed;
        } catch { /* Show one field-specific validation message below. */ }
        throw new Error(`${label.textContent}需要填写 JSON 数组`);
      };
    } else if (privateField) {
      input.type = 'password';
      input.autocomplete = 'new-password';
      if (editing) input.placeholder = '留空保留现有凭据';
      read = () => input.value || undefined;
    } else if (['bool', 'boolean'].includes(type)) {
      input.type = 'checkbox';
      input.checked = value === true;
      read = () => input.checked;
    } else if (['int', 'integer', 'float', 'number'].includes(type)) {
      input.type = 'number';
      input.step = ['int', 'integer'].includes(type) ? '1' : 'any';
      input.value = value ?? '';
      read = () => input.value === '' ? undefined : input.valueAsNumber;
    } else {
      input.type = 'text';
      input.value = value ?? '';
      read = () => input.value.trim() || undefined;
    }
  }
  input.id = id;
  input.required = Boolean(field.required && !(editing && privateField) && input.type !== 'checkbox');
  input.disabled = tags.includes('fixed');
  if (!field.nested_fields) wrapper.append(label, input);
  if (field.description && field.description !== label.textContent) {
    const hint = element('small', field.description);
    hint.id = `${id}-hint`;
    input.setAttribute('aria-describedby', hint.id);
    wrapper.append(hint);
  }
  return { wrapper, read: () => [name, read()] };
}

function readParams(reads) {
  return Object.fromEntries(reads.map(read => read()).filter(([, value]) => value !== undefined));
}

export function createManagement(getAPI) {
  const form = byId('connection-form');
  const picker = byId('database-picker');
  const search = byId('database-search');
  const overview = byId('connections-overview');
  const addButton = byId('add-connection');
  const filter = byId('database-filter');
  const modelForm = byId('model-form');
  const modelKey = byId('model-key');
  const keyless = byId('model-keyless');
  const keyToggle = byId('toggle-model-key');
  const modelButtons = [...document.querySelectorAll('button[data-model-kind]')];
  let types = [], connections = [], fieldReads = [], editing = null, selectedType = null;
  let modelBusy = false, connectionBusy = false, listRequest = 0;
  let modelKind = 'chat', modelRequest = 0;

  function clearConnectionForm() {
    form.reset();
    byId('connection-fields').replaceChildren();
    editing = null;
    selectedType = null;
    fieldReads = [];
  }

  function showConnectionStep(step) {
    overview.hidden = step !== 'overview';
    addButton.hidden = overview.hidden;
    picker.hidden = step !== 'picker';
    form.hidden = step !== 'form';
  }

  function closeConnectionFlow(restoreFocus = true) {
    clearConnectionForm();
    showConnectionStep('overview');
    if (restoreFocus) addButton.focus();
  }

  function renderDatabaseChoices() {
    const query = search.value.trim().toLowerCase();
    const matches = types.filter(type => [type.name, databaseLabel(type), type.label]
      .some(value => value?.toLowerCase().includes(query)));
    status('database-choice-count', matches.length ? `${matches.length} 种数据库` : '没有匹配的数据库，请试试其他名称。');
    byId('database-choices').replaceChildren(...matches.map(type => {
      const item = element('li');
      const button = action(databaseLabel(type), () => openConnection(type));
      button.className = 'database-choice';
      button.prepend(databaseIcon(type.name));
      item.append(button);
      return item;
    }));
  }

  function openDatabasePicker(resetSearch = true) {
    if (connectionBusy || !types.length) return;
    clearConnectionForm();
    if (resetSearch) search.value = '';
    renderDatabaseChoices();
    showConnectionStep('picker');
    byId('database-picker-title').focus();
  }

  function openConnection(type, item = null) {
    if (connectionBusy || !type) return;
    clearConnectionForm();
    editing = item;
    selectedType = type;
    const previous = item?.params || {};
    const rendered = type.parameters.map(field =>
      renderField(field, previous[field.param_name], 'connection', Boolean(item)),
    );
    byId('connection-fields').replaceChildren(...rendered.map(item => item.wrapper));
    fieldReads = rendered.map(item => item.read);
    byId('connection-description').value = item?.description || '';
    byId('connection-form-title').textContent = item ? '编辑数据源' : '填写连接信息';
    byId('connection-type-label').replaceChildren(databaseIcon(type.name), document.createTextNode(databaseLabel(type)));
    byId('change-connection-type').hidden = Boolean(item);
    status('connection-form-status');
    showConnectionStep('form');
    byId('connection-form-title').focus();
  }

  async function connectionAction(button, path, method, message) {
    const api = getAPI();
    if (!api || connectionBusy) return;
    button.disabled = true;
    try {
      await api.management(path, { method });
      if (getAPI() === api) await loadConnections(message);
    } catch (error) {
      if (getAPI() === api) status('connections-status', error.message, true);
    } finally { button.disabled = false; }
  }

  function renderConnections() {
    const visible = connections.filter(item => !filter.value || item.type === filter.value);
    const list = byId('connection-list');
    if (!visible.length) {
      list.replaceChildren(element('p', filter.value ? '这个类型还没有连接。可点击「添加数据源」创建。' : '还没有数据库连接。可先添加数据源，再测试连通性。', 'management-empty'));
      return;
    }
    list.replaceChildren(...visible.map(item => {
      const row = element('article', undefined, 'connection-row');
      const summary = element('div', undefined, 'connection-summary');
      const type = types.find(entry => entry.name === item.type);
      const name = item.name || item.type;
      const title = element('h3', name, 'connection-name');
      title.prepend(databaseIcon(item.type));
      summary.append(title);
      const target = item.params.host
        ? [item.params.host, item.params.port].filter(Boolean).join(':')
        : item.params.path?.split(/[/\\]/).pop();
      summary.append(element('p', [type ? databaseLabel(type) : item.type, target, item.description].filter(Boolean).join(' · '), 'muted'));
      const controls = element('div', undefined, 'management-actions');
      const refresh = action('刷新表结构', () => void connectionAction(refresh, `datasources/${item.id}/refresh`, 'POST', '已提交表结构刷新。'));
      const remove = action('删除', () => {
        if (window.confirm(`删除连接「${name}」？不会删除数据库中的数据。`)) {
          void connectionAction(remove, `datasources/${item.id}`, 'DELETE', '连接已删除。');
        }
      });
      remove.classList.add('danger-action');
      const edit = action('编辑', () => openConnection(type, item));
      edit.disabled = !type;
      controls.append(refresh, edit, remove);
      row.append(summary, controls);
      return row;
    }));
  }

  async function loadConnections(message = '') {
    const api = getAPI();
    if (!api) return;
    const sequence = ++listRequest;
    status('connections-status', message || '正在读取数据源…');
    try {
      const data = await api.management('datasources');
      if (getAPI() !== api || sequence !== listRequest) return;
      types = data.types.sort((first, second) => databaseLabel(first).localeCompare(databaseLabel(second)));
      connections = data.connections;
      const selected = filter.value;
      filter.replaceChildren(option('', '所有类型'), ...types.map(type => option(type.name, databaseLabel(type))));
      filter.value = types.some(type => type.name === selected) ? selected : '';
      byId('connections-toolbar').hidden = false;
      addButton.disabled = !types.length;
      status('connections-status', message);
      renderConnections();
      if (!picker.hidden) renderDatabaseChoices();
    } catch (error) {
      if (getAPI() !== api || sequence !== listRequest) return;
      byId('connections-toolbar').hidden = true;
      byId('add-connection').disabled = true;
      byId('connection-list').replaceChildren();
      status('connections-status', error.message, true);
    }
  }

  addButton.addEventListener('click', () => openDatabasePicker());
  byId('cancel-database-picker').addEventListener('click', () => closeConnectionFlow());
  byId('change-connection-type').addEventListener('click', () => openDatabasePicker(false));
  byId('cancel-connection').addEventListener('click', () => closeConnectionFlow());
  search.addEventListener('input', renderDatabaseChoices);
  filter.addEventListener('change', renderConnections);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const api = getAPI();
    if (!api || connectionBusy || !selectedType) return;
    const testOnly = event.submitter?.value === 'test';
    connectionBusy = true;
    const triggers = [...form.querySelectorAll('button')];
    triggers.forEach(button => { button.disabled = true; });
    status('connection-form-status', testOnly ? '正在测试连接…' : '正在测试并保存连接…');
    try {
      const body = { type: selectedType.name, params: readParams(fieldReads), description: byId('connection-description').value.trim() };
      if (editing) body.id = editing.id;
      await api.management(`datasources?test_only=${testOnly}`, { method: 'POST', body: JSON.stringify(body) });
      if (getAPI() !== api) return;
      if (testOnly) status('connection-form-status', '连接测试通过，尚未保存。');
      else {
        if (filter.value && filter.value !== body.type) filter.value = '';
        closeConnectionFlow();
        await loadConnections('连接已保存。');
      }
    } catch (error) {
      if (getAPI() === api) status('connection-form-status', error.message, true);
    } finally {
      connectionBusy = false;
      triggers.forEach(button => { button.disabled = false; });
    }
  });

  function syncKey() {
    if (keyless.checked) modelKey.value = '';
    modelKey.disabled = keyless.checked;
    keyToggle.disabled = keyless.checked;
    modelKey.type = 'password';
    keyToggle.textContent = '显示';
    keyToggle.setAttribute('aria-pressed', 'false');
  }

  function renderModel(data) {
    const current = modelKinds[modelKind];
    byId('model-current').textContent = data.read_failed
      ? '已保存的设置无法读取，可重新配置。'
      : data.active ? `已启用 · ${data.model}` : (data.model ? `${current.defaultLabel} · ${data.model}` : current.defaultLabel);
    byId('model-current').classList.toggle('is-active', data.active);
    byId('model-url').value = data.base_url;
    byId('model-name').value = data.model || '';
    keyless.checked = data.keyless;
    byId('model-advanced').open = data.keyless;
    modelKey.value = '';
    modelKey.placeholder = data.has_key ? '已保存 · 同地址留空可沿用' : '粘贴供应商 API Key';
    byId('reset-model').hidden = !(data.active || data.read_failed);
    byId('reset-model').textContent = modelKind === 'rerank' ? '关闭重排' : '恢复默认';
    syncKey();
    modelForm.hidden = false;
  }

  async function loadModel(kind = modelKind) {
    const api = getAPI();
    if (!api || modelBusy || (kind === modelKind && !modelForm.hidden)) return;
    modelKind = kind;
    const current = modelKinds[kind];
    const sequence = ++modelRequest;
    modelForm.hidden = true;
    modelKey.value = '';
    modelButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.modelKind === kind)));
    byId('model-section-title').textContent = current.title;
    byId('model-purpose').textContent = current.purpose;
    byId('model-url-help').textContent = `可填 API 前缀，也可填带 /${current.endpoint} 的完整地址。`;
    byId('model-url').placeholder = kind === 'rerank' ? '例如：https://api.cohere.com/v2' : '例如：https://api.openai.com/v1';
    byId('model-current').classList.remove('is-active');
    byId('model-current').textContent = '正在读取模型设置…';
    status('model-feedback');
    try {
      const data = await api.management(`model?kind=${kind}`);
      if (getAPI() === api && sequence === modelRequest) renderModel(data);
    } catch (error) {
      if (getAPI() === api && sequence === modelRequest) byId('model-current').textContent = error.message;
    }
  }

  modelButtons.forEach(button => button.addEventListener('click', () => void loadModel(button.dataset.modelKind)));
  keyless.addEventListener('change', syncKey);
  keyToggle.addEventListener('click', () => {
    const reveal = modelKey.type === 'password';
    modelKey.type = reveal ? 'text' : 'password';
    keyToggle.textContent = reveal ? '隐藏' : '显示';
    keyToggle.setAttribute('aria-pressed', String(reveal));
  });

  async function updateModel(reset, testOnly = false) {
    const api = getAPI();
    if (!api || modelBusy) return;
    modelBusy = true;
    const controls = [...modelForm.elements, ...modelButtons];
    controls.forEach(control => { control.disabled = true; });
    status('model-feedback', reset ? '正在恢复默认模型…' : '正在测试模型连接…');
    try {
      const data = await api.management(`model?kind=${modelKind}&test_only=${testOnly}`, reset ? { method: 'DELETE' } : {
        method: 'POST', body: JSON.stringify({
          base_url: byId('model-url').value.trim(), model: byId('model-name').value.trim(),
          api_key: modelKey.value.trim() || null, keyless: keyless.checked,
        }),
      });
      if (getAPI() !== api) return;
      if (!testOnly) {
        renderModel(data);
      }
      const saved = {
        chat: '已保存。下一次分析将使用这个模型。',
        embedding: '已保存。下一次向量检索会使用新模型并构建索引。',
        rerank: '已保存。下一次检索会使用此模型重新排序。',
      };
      status('model-feedback', reset ? (modelKind === 'rerank' ? '已关闭重排。' : '已恢复启动配置中的模型。') : testOnly ? '模型连接正常，尚未保存。' : saved[modelKind]);
    } catch (error) {
      if (getAPI() === api) status('model-feedback', error.message, true);
    } finally {
      modelBusy = false;
      controls.forEach(control => { control.disabled = false; });
      syncKey();
    }
  }

  modelForm.addEventListener('submit', event => {
    event.preventDefault();
    void updateModel(false, event.submitter?.value === 'test');
  });
  byId('reset-model').addEventListener('click', () => {
    const verb = modelKind === 'rerank' ? '关闭重排' : '恢复默认模型';
    if (window.confirm(`${verb}，并移除此类别已保存的 API Key？`)) void updateModel(true);
  });

  return { loadConnections, loadModel };
}
