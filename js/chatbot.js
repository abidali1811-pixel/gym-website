(function () {
  const launcher = document.querySelector(".chat-launcher");
  const panel = document.getElementById("chat-panel");
  const closeButton = document.querySelector(".chat-close");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const messagesEl = document.getElementById("chat-messages");
  const sendButton = form && form.querySelector("button[type='submit']");
  if (!launcher || !panel || !form || !input || !messagesEl || !sendButton) return;

  let history = [];

  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) input.focus();
  }

  function addMessage(text, kind) {
    const bubble = document.createElement("p");
    bubble.className = `chat-bubble ${kind}`;
    bubble.textContent = text;
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  async function sendMessage(text) {
    const cleanText = text.trim();
    if (!cleanText || sendButton.disabled) return;
    addMessage(cleanText, "user");
    const nextHistory = [...history, { role: "user", content: cleanText }].slice(-12);
    history = nextHistory;
    input.value = "";
    input.disabled = true;
    sendButton.disabled = true;
    const typing = addMessage("Thinking…", "assistant typing");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      const result = await response.json();
      typing.remove();
      if (!response.ok) throw new Error(result.error || "The assistant is unavailable right now.");
      const reply = typeof result.reply === "string" ? result.reply : "I couldn't create a reply. Please try again.";
      history = [...history, { role: "assistant", content: reply }].slice(-12);
      addMessage(reply, "assistant");
    } catch (error) {
      typing.remove();
      addMessage(error.message || "Chat is temporarily unavailable. Please try again.", "assistant chat-error");
      history = history.slice(0, -1);
    } finally {
      input.disabled = false;
      sendButton.disabled = false;
      input.focus();
    }
  }

  launcher.addEventListener("click", () => setOpen(panel.hidden));
  closeButton?.addEventListener("click", () => {
    setOpen(false);
    launcher.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(input.value);
  });
  document.querySelectorAll(".chat-suggestions button").forEach((button) => {
    button.addEventListener("click", () => sendMessage(button.textContent));
  });
})();
