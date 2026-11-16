/** Parse complete SSE records, independently of UTF-8/network chunk boundaries. */
export function createSSEParser(onEvent) {
  let buffer = '';
  function record(frame) {
    let event = 'message';
    const data = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    }
    if (data.length) onEvent({ event, data: JSON.parse(data.join('\n')) });
  }
  return {
    push(text) {
      buffer += text;
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        record(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
    },
    finish() {
      if (buffer.trim()) record(buffer);
      buffer = '';
    },
  };
}
