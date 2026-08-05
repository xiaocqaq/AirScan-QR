(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.AirScan = root.AirScan || {};
  root.AirScan.messages = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  async function copyText(text, allowFallback = true) {
    try {
      if (root.navigator && root.navigator.clipboard) {
        await root.navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {
      if (!allowFallback) return false;
    }
    if (!allowFallback || !root.document) return false;
    const input = root.document.createElement('textarea');
    input.value = text;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    root.document.body.appendChild(input);
    input.select();
    let copied = false;
    try {
      copied = root.document.execCommand('copy');
    } catch (_) {
      copied = false;
    }
    input.remove();
    return copied;
  }

  function buildMessage(document, message, onCopy) {
    const item = document.createElement('article');
    item.className = 'message-item';
    const body = document.createElement('p');
    body.className = 'message-body';
    body.textContent = message.text;
    body.title = message.text;
    const button = document.createElement('button');
    button.className = 'text-button message-copy';
    button.type = 'button';
    button.textContent = '复制';
    button.setAttribute('aria-label', '复制消息');
    button.addEventListener('click', () => onCopy(message.text));
    item.append(body, button);
    return item;
  }

  function createMessageFeed(options) {
    const messages = [];
    const { list, empty, count, onCopy } = options;

    async function copyMessage(text) {
      const copied = await copyText(text);
      onCopy(copied);
    }

    async function addText(text, receivedAt = new Date(), settings = {}) {
      const message = { text: String(text), receivedAt: new Date(receivedAt).toISOString() };
      messages.unshift(message);
      list.prepend(buildMessage(root.document, message, copyMessage));
      empty.hidden = true;
      count.textContent = `${messages.length} 条`;
      if (settings.copyOnReceive) await copyText(message.text, false);
      return message;
    }

    return { addText, all: () => messages.slice() };
  }

  return { copyText, createMessageFeed };
}));
