import { useEffect, useRef, useState } from "react";

const SUGGESTIONS = [
  "Why is Ambulance 2 stopped?",
  "Which ambulance arrives first?",
  "Where are the police right now?",
];

// The "Ask" chat: a floating button that opens a small panel for
// questions about the live simulation. Claude answers from the current
// state only (POST /assistant/ask).
function AskChat({ apiUrl }) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const listRef = useRef(null);

  useEffect(() => {
    listRef.current?.scrollTo(0, listRef.current.scrollHeight);
  }, [turns, busy]);

  const ask = async (text) => {
    const asked = text.trim();
    if (!asked || busy) {
      return;
    }
    setQuestion("");
    setError(null);
    setBusy(true);
    const history = turns;
    setTurns([...history, { role: "user", content: asked }]);
    try {
      const response = await fetch(`${apiUrl}/assistant/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: asked, history }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "No answer.");
      }
      setTurns((list) => [...list, { role: "assistant", content: data.answer }]);
    } catch (problem) {
      // Drop the unanswered question so the history stays in pairs.
      setTurns(history);
      setQuestion(asked);
      setError(problem.message === "Failed to fetch" ? "Can't reach the server." : problem.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="ask-toggle" onClick={() => setOpen(true)}>
        Ask AI
      </button>
    );
  }

  return (
    <section className="ask-panel" aria-label="Ask about the simulation">
      <div className="ask-header">
        <strong>Ask a question</strong>
        <div>
          {turns.length > 0 && (
            <button type="button" className="ask-link" onClick={() => setTurns([])}>
              Clear
            </button>
          )}
          <button type="button" className="ask-link" onClick={() => setOpen(false)}
            aria-label="Close">
            ✕
          </button>
        </div>
      </div>

      <div className="ask-list" ref={listRef}>
        {turns.length === 0 && (
          <div className="ask-suggestions">
            {SUGGESTIONS.map((text) => (
              <button key={text} type="button" className="ask-suggestion"
                onClick={() => ask(text)} disabled={busy}>
                {text}
              </button>
            ))}
          </div>
        )}
        {turns.map((turn, index) => (
          <p key={index} className={`ask-turn ask-${turn.role}`}>{turn.content}</p>
        ))}
        {busy && <p className="ask-turn ask-assistant muted">Thinking…</p>}
      </div>

      {error && <p className="field-error ask-error">{error}</p>}

      <form
        className="ask-form"
        onSubmit={(event) => {
          event.preventDefault();
          ask(question);
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="e.g. Why is Ambulance 1 slow?"
          maxLength={500}
          autoFocus
        />
        <button type="submit" className="button" disabled={busy || !question.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}

export default AskChat;
