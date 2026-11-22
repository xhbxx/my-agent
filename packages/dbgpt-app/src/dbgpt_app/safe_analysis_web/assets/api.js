import { createSSEParser } from './stream.js';

const PREFIX = '/api/v1/safe-analysis';

async function checkResponse(response) {
  if (response.ok) return;
  let data = {};
  try { data = await response.json(); } catch { /* An intermediary may return HTML. */ }
  const message = data.detail || data.err_msg;
  throw new Error(typeof message === 'string' ? message : `请求失败（${response.status}），请检查服务是否启动。`);
}

export function createAPI() {
  const headers = { 'X-Data-Agent-Local': '1', 'Content-Type': 'application/json' };
  return {
    async management(path, init = {}) {
      const response = await fetch(`${PREFIX}/manage/${path}`, {
        ...init, headers: { ...headers, ...init.headers },
      });
      await checkResponse(response);
      return response.json();
    },
    async sources() {
      const response = await fetch(`${PREFIX}/sources`, { headers });
      await checkResponse(response);
      return response.json();
    },
    async document(source, filename, signal) {
      const query = new URLSearchParams({ source, filename });
      const response = await fetch(`${PREFIX}/documents?${query}`, { headers, signal });
      await checkResponse(response);
      return response.json();
    },
    async context(source, question, signal) {
      const response = await fetch(`${PREFIX}/context`, {
        method: 'POST', headers, body: JSON.stringify({ source, question }), signal,
      });
      await checkResponse(response);
      return response.json();
    },
    async query(body, signal, onEvent) {
      const response = await fetch(`${PREFIX}/query`, {
        method: 'POST', headers, body: JSON.stringify({ ...body, stream: true }), signal,
      });
      await checkResponse(response);
      if (!response.body) throw new Error('浏览器无法读取流式响应，请升级浏览器。');
      let done = false;
      const parser = createSSEParser(item => {
        if (item.event === 'done') done = true;
        onEvent(item);
      });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          parser.push(decoder.decode(chunk.value, { stream: true }));
        }
        parser.push(decoder.decode());
        parser.finish();
        if (!done) throw new Error('分析连接提前中断，请查看已有结果后重试。');
      } finally {
        reader.releaseLock();
      }
    },
    async cancel(requestID) {
      const response = await fetch(`${PREFIX}/requests/${encodeURIComponent(requestID)}/cancel`, {
        method: 'POST', headers,
      });
      await checkResponse(response);
    },
  };
}
