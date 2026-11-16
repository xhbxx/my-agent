export const byId = id => document.getElementById(id);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function renderSource(source, model) {
  byId('source-summary').textContent = `${source.dialect.toUpperCase()} · 只读访问${source.name === 'sales_demo' ? ' · 合成数据演示' : ''}`;
  byId('connection-status').textContent = model ? `已连接 · ${model}` : '已连接';
  const tables = Object.entries(source.tables);
  byId('table-count').textContent = `${tables.length} 张表`;
  byId('schema-list').replaceChildren(...tables.map(([name, columns], index) => {
    const details = node('details');
    details.open = index === 0;
    details.append(node('summary', name));
    const list = node('dl');
    for (const [column, type] of Object.entries(columns)) {
      const pair = node('div');
      pair.append(node('dt', column), node('dd', type));
      list.append(pair);
    }
    details.append(list);
    return details;
  }));
  byId('metric-list').replaceChildren(...source.metrics.map(metric => node('li', metric)));
  byId('retrieval-label').textContent = source.retrieval;
  byId('index-status').textContent = source.retrieval === 'BM25' ? '本地关键词检索' : source.index_ready ? '索引已建立' : '首次检索时建立索引';
  byId('demo-query').hidden = source.name !== 'sales_demo';
}

export function renderEvidence(evidence) {
  const labels = { schema: '表结构', metric: '指标口径', document: '业务文档' };
  byId('evidence').replaceChildren(...evidence.map(item => {
    const section = node('section', undefined, 'evidence-item');
    section.append(node('h3', `${labels[item.kind] || '证据'} · ${item.id}`), node('p', item.content));
    return section;
  }));
}

export function renderResult(result) {
  byId('result-placeholder').hidden = true;
  byId('result-content').hidden = false;
  byId('result-summary').textContent = `${result.rows.length} 行结果 · 查询耗时 ${result.elapsed_ms} ms${result.truncated ? ' · 已达展示行数上限' : ''}`;
  const table = node('table');
  const head = node('thead');
  const headRow = node('tr');
  for (const column of result.columns) headRow.append(node('th', column));
  head.append(headRow);
  const body = node('tbody');
  for (const row of result.rows) {
    const tr = node('tr');
    for (const cell of row) tr.append(node('td', cell === null ? 'NULL' : String(cell)));
    body.append(tr);
  }
  table.append(head, body);
  byId('result-table').replaceChildren(table);
  renderChart(result.chart);
}

function renderChart(spec) {
  const container = byId('chart');
  container.replaceChildren();
  container.hidden = !spec?.data?.length;
  if (container.hidden) return;
  const rows = spec.data.slice(0, 12);
  if (!rows.every(row => typeof row[1] === 'number' && Number.isFinite(row[1]))) return;
  container.append(node('h3', `${spec.y} · 按 ${spec.x} 对比`));
  const svgNode = (name, attrs, text) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const width = 640, labelWidth = 115, plotWidth = 430;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${rows.length * 37 + 12}`, role: 'img', 'aria-label': `${spec.y}对比图` });
  const min = Math.min(0, ...rows.map(row => row[1]));
  const max = Math.max(0, ...rows.map(row => row[1]));
  const range = max - min || 1;
  const x = value => labelWidth + (value - min) / range * plotWidth;
  svg.append(svgNode('line', { x1: x(0), y1: 0, x2: x(0), y2: rows.length * 37, class: 'axis' }));
  rows.forEach(([label, value], index) => {
    const y = index * 37;
    const shortLabel = String(label ?? 'NULL').slice(0, 13);
    svg.append(svgNode('text', { x: 0, y: y + 21 }, shortLabel));
    const bar = svgNode('rect', { x: Math.min(x(0), x(value)), y: y + 4, width: Math.max(0, Math.abs(x(value) - x(0))), height: 24, rx: 3, class: 'bar' });
    bar.append(svgNode('title', {}, `${label}: ${value}`));
    svg.append(bar, svgNode('text', { x: 560, y: y + 21 }, value.toLocaleString()));
  });
  const scroll = node('div', undefined, 'chart-scroll');
  scroll.append(svg);
  container.append(scroll, node('p', '左右滑动查看图表，完整数据见上方表格。', 'chart-note'));
  if (spec.data.length > 12) container.append(node('p', '图表显示前 12 项，完整结果见表格。', 'muted'));
}

export function renderMarkdown(text) {
  const target = byId('explanation');
  target.hidden = false;
  target.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text), {
    ALLOWED_TAGS: ['p','br','strong','em','code','pre','h1','h2','h3','h4','ul','ol','li','table','thead','tbody','tr','th','td','blockquote','hr','a'],
    ALLOWED_ATTR: ['href', 'title'],
  });
  for (const link of target.querySelectorAll('a')) {
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
}

export function downloadCSV(result) {
  const cell = value => {
    let text = value === null ? '' : String(value);
    if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return `"${text.replace(/"/g, '""')}"`;
  };
  const csv = '\uFEFF' + [result.columns, ...result.rows].map(row => row.map(cell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = node('a');
  link.href = url;
  link.download = 'analysis-result.csv';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
