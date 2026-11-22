class ChatControls {
  constructor(input, button) {
    this.input = input;
    this.button = button;
    this.controller = null;
    this.conversation = null;
  }

  begin(clearInput = true) {
    this.controller = new AbortController();
    this.conversation = null;
    if (clearInput) {
      this.input.value = '';
      this.input.dispatchEvent(new Event('input'));
    }
    this.button.disabled = false;
    this.button.textContent = '停止生成';
    this.button.title = '停止接收回答，并取消后续生成步骤';
  }

  get signal() { return this.controller.signal; }

  setConversation(conversation) {
    this.conversation = conversation;
    this.signal.throwIfAborted();
  }

  stop() {
    if (!this.controller) return;
    this.controller.abort();
    this.button.disabled = true;
    if (this.conversation !== null) {
      fetch(`/api/conversations/${this.conversation}/stop`, {method: 'POST'}).catch(() => {});
    }
  }

  showStopped(shell) {
    if (!shell) return;
    shell.querySelectorAll('.cursor-blink').forEach(cursor => cursor.remove());
    const status = shell.querySelector('.status-text');
    if (status) status.parentElement.style.display = 'none';
    const note = document.createElement('p');
    note.textContent = '已停止生成';
    note.style.color = 'var(--sub)';
    shell.querySelector('.content').append(note);
  }

  finish() {
    this.controller = null;
    this.conversation = null;
    this.button.disabled = false;
    this.button.textContent = '发送';
    this.button.title = '';
  }
}
