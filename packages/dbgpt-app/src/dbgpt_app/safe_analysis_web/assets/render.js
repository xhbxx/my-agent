export const byId = id => document.getElementById(id);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

const isNumber = value => typeof value === 'number' && Number.isFinite(value);
const formatNumber = value => value.toLocaleString(undefined, { maximumFractionDigits: 6 });

export function renderSource(source, openDocument) {
  byId('source-summary').textContent = `${source.dialect.toUpperCase()} · 只读访问${source.name === 'sales_demo' ? ' · 合成数据演示' : ''}`;
  byId('knowledge-source').textContent = `当前数据源：${source.name}`;
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
  renderList('metric-list', source.metrics, '当前数据源没有配置指标口径');
  if (!source.documents?.length) {
    renderList('document-list', [], '还没有文档，上传指标定义或业务规则后即可检索。');
  } else {
    byId('document-list').replaceChildren(...source.documents.map(filename => {
      const item = node('li');
      const link = node('button', filename, 'text-button document-link');
      link.type = 'button';
      link.addEventListener('click', () => void openDocument(filename));
      item.append(link);
      return item;
    }));
  }
}

function renderList(id, entries, emptyText) {
  const items = entries?.length ? entries : [emptyText];
  byId(id).replaceChildren(...items.map(text => node('li', text)));
}

export function renderEvidence(evidence, target = byId('evidence')) {
  const labels = { schema: '表结构', metric: '指标口径', document: '业务文档' };
  if (!evidence.length) {
    target.replaceChildren(node('p', '本次没有召回相关的表结构、指标或文档。', 'muted'));
    return;
  }
  target.replaceChildren(...evidence.map(item => {
    const section = node('section', undefined, 'evidence-item');
    section.append(node('h3', `${labels[item.kind] || '证据'} · ${item.id}`), node('p', item.content));
    return section;
  }));
}

export function renderResult(result) {
  byId('result-placeholder').hidden = true;
  byId('result-content').hidden = false;
  const limit = result.truncated ? ' · 已达展示行数上限' : '';
  byId('result-summary').textContent = `${result.rows.length} 行 × ${result.columns.length} 列 · 查询耗时 ${result.elapsed_ms} ms${limit}`;
  if (!result.rows.length) {
    byId('result-table').replaceChildren(node('p', '查询已执行，但没有返回数据。可以放宽时间范围或筛选条件后再试。', 'management-empty'));
    renderChart(undefined);
    return;
  }
  const numeric = result.columns.map((_, index) => result.rows.some(row => row[index] !== null)
    && result.rows.every(row => row[index] === null || isNumber(row[index])));
  const table = node('table');
  const head = node('thead');
  const headRow = node('tr');
  result.columns.forEach((column, index) => headRow.append(node('th', column, numeric[index] ? 'is-num' : undefined)));
  head.append(headRow);
  const body = node('tbody');
  for (const row of result.rows) {
    const tr = node('tr');
    row.forEach((cell, index) => {
      if (cell === null) tr.append(node('td', 'NULL', 'is-null'));
      else tr.append(node('td', isNumber(cell) ? formatNumber(cell) : String(cell), numeric[index] ? 'is-num' : undefined));
    });
    body.append(tr);
  }
  table.append(head, body);
  byId('result-table').replaceChildren(table);
  renderChart(result.chart);
}

const svgNode = (name, attrs, text) => {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
};

function renderChart(spec) {
  const container = byId('chart');
  container.replaceChildren();
  container.hidden = !spec?.data?.length;
  if (container.hidden) return;
  const rows = spec.data.slice(0, spec.type === 'line' ? 50 : 12);
  if (!rows.every(row => isNumber(row[1]))) { container.hidden = true; return; }
  container.append(node('h3', spec.type === 'line' ? `${spec.y} · 日期趋势` : `${spec.y} · 按 ${spec.x} 对比`));
  const scroll = node('div', undefined, 'chart-scroll');
  scroll.append(spec.type === 'line' ? lineChart(rows, spec) : barChart(rows, spec));
  container.append(scroll, node('p', '左右滑动查看图表，完整数据见上方表格。', 'chart-note'));
  if (spec.data.length > rows.length) container.append(node('p', `图表显示前 ${rows.length} 项，完整结果见表格。`, 'muted'));
}

function lineChart(rows, spec) {
  const svg = svgNode('svg', { viewBox: '0 0 640 250', role: 'img', 'aria-label': `${spec.y}趋势折线图` });
  const values = rows.map(row => row[1]);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const x = index => 76 + index * 540 / Math.max(1, rows.length - 1);
  const y = value => 208 - (value - min) * 180 / (max - min || 1);
  for (let step = 0; step <= (min === max ? 0 : 4); step++) {
    const value = min + (max - min) * step / 4;
    svg.append(
      svgNode('line', { x1: 76, x2: 616, y1: y(value), y2: y(value), class: 'axis' }),
      svgNode('text', { x: 64, y: y(value) + 4, 'text-anchor': 'end' }, value.toLocaleString(undefined, { maximumFractionDigits: 1 })),
    );
  }
  const points = rows.map((row, index) => `${x(index)},${y(row[1])}`);
  if (rows.length > 1) {
    svg.append(svgNode('polygon', { points: `${x(0)},${y(0)} ${points.join(' ')} ${x(rows.length - 1)},${y(0)}`, class: 'trend-area' }));
  }
  svg.append(svgNode('polyline', { points: points.join(' '), class: 'trend-line' }));
  const interval = Math.max(1, Math.ceil(rows.length / 5));
  rows.forEach(([label, value], index) => {
    const point = svgNode('circle', { cx: x(index), cy: y(value), r: rows.length > 30 ? 2 : 3.5, class: 'bar' });
    point.append(svgNode('title', {}, `${label}: ${formatNumber(value)}`));
    svg.append(point);
    if ((index % interval === 0 && rows.length - 1 - index >= interval / 2) || index === rows.length - 1) {
      svg.append(svgNode('text', { x: x(index), y: 232, 'text-anchor': 'middle' }, String(label).slice(5)));
    }
  });
  return svg;
}

function barChart(rows, spec) {
  const width = 640, labelWidth = 115, plotWidth = 430;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${rows.length * 37 + 12}`, role: 'img', 'aria-label': `${spec.y}对比图` });
  const min = Math.min(0, ...rows.map(row => row[1]));
  const max = Math.max(0, ...rows.map(row => row[1]));
  const range = max - min || 1;
  const x = value => labelWidth + (value - min) / range * plotWidth;
  svg.append(svgNode('line', { x1: x(0), y1: 0, x2: x(0), y2: rows.length * 37, class: 'axis' }));
  rows.forEach(([label, value], index) => {
    const y = index * 37;
    const full = String(label ?? 'NULL');
    const name = svgNode('text', { x: 0, y: y + 21 }, full.length > 13 ? `${full.slice(0, 12)}…` : full);
    name.append(svgNode('title', {}, full));
    const bar = svgNode('rect', { x: Math.min(x(0), x(value)), y: y + 4, width: Math.max(0, Math.abs(x(value) - x(0))), height: 24, rx: 3, class: 'bar' });
    bar.append(svgNode('title', {}, `${full}: ${formatNumber(value)}`));
    svg.append(name, bar, svgNode('text', { x: 560, y: y + 21, class: 'value-label' }, formatNumber(value)));
  });
  return svg;
}

export function renderMarkdown(text, target = byId('explanation')) {
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
  const csv = '﻿' + [result.columns, ...result.rows].map(row => row.map(cell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = node('a');
  link.href = url;
  link.download = 'analysis-result.csv';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
