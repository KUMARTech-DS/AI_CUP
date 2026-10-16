/* Shared Suffai page-aware assistant. Context is assembled by each page at send time. */
(() => {
  const cfg = window.SUFFAI_CHAT_CONFIG;
  if (!cfg || !cfg.getContext) return;

  const history = [];
  let busy = false;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[c]));

  const style = document.createElement('style');
  style.textContent = `
    #suffai-chat-launcher{position:fixed;right:24px;bottom:22px;z-index:9998;border:0;border-radius:999px;background:#292722;color:#fff;padding:12px 18px;font:600 13px Inter,Arial,sans-serif;box-shadow:0 8px 28px #0002;cursor:pointer}
    #suffai-chat-panel{position:fixed;right:24px;bottom:78px;z-index:9999;width:min(410px,calc(100vw - 28px));height:min(560px,calc(100vh - 115px));display:none;flex-direction:column;background:#fff;border:1px solid #e4ded3;border-radius:16px;box-shadow:0 20px 60px #0003;overflow:hidden;color:#292722;font:13px Inter,Arial,sans-serif}
    #suffai-chat-panel.open{display:flex}
    #suffai-chat-panel *{box-sizing:border-box}
    .sc-head{display:flex;align-items:center;gap:10px;padding:15px 16px;background:#292722;color:#fff}
    .sc-orb{width:32px;height:32px;border-radius:10px;background:#89765c;display:grid;place-items:center;font-weight:800}
    .sc-title{font-weight:700;font-size:14px}.sc-sub{font-size:11px;color:#d6d0c6;margin-top:3px}
    .sc-close{margin-left:auto;background:transparent;color:#fff;border:0;font-size:22px;cursor:pointer}
    .sc-messages{flex:1;overflow:auto;padding:14px;background:#faf8f4;display:flex;flex-direction:column;gap:10px}
    .sc-msg{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5;padding:10px 12px;border-radius:12px;max-width:92%}
    .sc-msg.assistant{background:#fff;border:1px solid #e4ded3;align-self:flex-start}
    .sc-msg.user{background:#292722;color:#fff;align-self:flex-end}
    .sc-msg.error{background:#fff2f0;border:1px solid #e6b8b2;color:#8b2f26;align-self:flex-start}
    .sc-empty{margin:auto;text-align:center;color:#777066;line-height:1.6;padding:16px}.sc-empty strong{display:block;color:#292722;font-size:15px;margin-bottom:6px}
    .sc-suggestions{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 10px;background:#faf8f4}
    .sc-suggestion{border:1px solid #d9d1c4;background:#fff;border-radius:999px;padding:6px 9px;font-size:11px;color:#514737;cursor:pointer}
    .sc-compose{padding:11px;border-top:1px solid #e4ded3;display:flex;gap:8px;background:#fff}
    .sc-input{min-width:0;flex:1;resize:none;max-height:100px;border:1px solid #d9d1c4;border-radius:10px;padding:10px;font:13px Inter,Arial,sans-serif;outline:none}
    .sc-input:focus{border-color:#89765c;box-shadow:0 0 0 2px #89765c20}
    .sc-send{border:0;border-radius:10px;padding:0 13px;background:#89765c;color:#fff;font-weight:700;cursor:pointer}
    .sc-send:disabled,.sc-input:disabled{opacity:.55;cursor:wait}
    @media(max-width:520px){#suffai-chat-launcher{right:14px;bottom:14px}#suffai-chat-panel{right:10px;bottom:68px;width:calc(100vw - 20px);height:min(70vh,560px)}}
  `;
  document.head.appendChild(style);

  const launcher = document.createElement('button');
  launcher.id = 'suffai-chat-launcher';
  launcher.type = 'button';
  launcher.textContent = '✦ Ask Suffai';
  launcher.setAttribute('aria-label', 'Open Suffai AI assistant');

  const panel = document.createElement('section');
  panel.id = 'suffai-chat-panel';
  panel.setAttribute('aria-label', 'Suffai AI assistant');
  panel.innerHTML = `
    <header class="sc-head"><div class="sc-orb">S</div><div><div class="sc-title">Suffai AI Analyst</div><div class="sc-sub">${cfg.page === 'geo' ? 'Geo Intelligence · Current filters' : 'Ads Lab · Creative fit analysis'}</div></div><button class="sc-close" type="button" aria-label="Close chat">×</button></header>
    <div class="sc-messages" role="log" aria-live="polite"><div class="sc-empty"><strong>What would you like to know?</strong>I'll use the current page filters and available metrics to answer.</div></div>
    <div class="sc-suggestions">${(cfg.page === 'geo' ? ['Summarize this audience','What are the top garment preferences?','Compare the selected areas'] : ['Which creative fits best?','Explain the segment-fit scores','What attributes were extracted?']).map(q=>`<button type="button" class="sc-suggestion">${esc(q)}</button>`).join('')}</div>
    <form class="sc-compose"><textarea class="sc-input" rows="1" maxlength="2000" placeholder="Ask about the current page…" aria-label="Your question"></textarea><button class="sc-send" type="submit">Send</button></form>
  `;
  document.body.append(launcher, panel);

  const messages = panel.querySelector('.sc-messages');
  const form = panel.querySelector('.sc-compose');
  const input = panel.querySelector('.sc-input');
  const send = panel.querySelector('.sc-send');

  function addMessage(role, text, kind = '') {
    const empty = messages.querySelector('.sc-empty');
    if (empty) empty.remove();
    const node = document.createElement('div');
    node.className = `sc-msg ${role}${kind ? ' ' + kind : ''}`;
    node.textContent = text;
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  function toggle(open) {
    panel.classList.toggle('open', open);
    launcher.style.display = open ? 'none' : 'block';
    if (open) input.focus();
  }
  launcher.addEventListener('click', () => toggle(true));
  panel.querySelector('.sc-close').addEventListener('click', () => toggle(false));
  panel.querySelectorAll('.sc-suggestion').forEach(btn => btn.addEventListener('click', () => {
    input.value = btn.textContent;
    form.requestSubmit();
  }));
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || busy) return;
    busy = true;
    input.value = '';
    input.disabled = send.disabled = true;
    addMessage('user', question);
    const pending = addMessage('assistant', 'Checking the current page context…');
    try {
      const context = await cfg.getContext();
      const apiBase = (window.DASHBOARD_API || '').replace(/\/$/, '');
      const response = await fetch(`${apiBase}/v1/chat/assistant`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_context: cfg.page, question, context, history: history.slice(-8) })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.message || `Assistant request failed (HTTP ${response.status})`);
      const answer = data.answer || data.data?.answer;
      if (!answer) throw new Error('The assistant returned an empty answer.');
      pending.textContent = answer;
      history.push({ role: 'user', content: question }, { role: 'assistant', content: answer });
    } catch (error) {
      pending.className = 'sc-msg assistant error';
      pending.textContent = error.message || 'Could not reach the assistant. Please try again.';
    } finally {
      busy = false;
      input.disabled = send.disabled = false;
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  });
})();
