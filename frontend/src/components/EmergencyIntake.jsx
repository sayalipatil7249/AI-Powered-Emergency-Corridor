import { useRef, useState } from "react";


// Move the cursor to the manual form (when the AI could not help).
function focusManualForm() {
  document.getElementById("trip-start")?.focus();
}

// Chrome's (and Edge's) built-in speech-to-text: it sends the voice to
// Google and types the words back as they are spoken. Not in Firefox or
// Safari; needs https (or localhost) and the microphone allowed.
const Recognition =
  typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

const LANGUAGES = [
  ["en-IN", "English"],
  ["hi-IN", "Hindi"],
  ["mr-IN", "Marathi"],
];

// Why the microphone stopped, in plain words.
const MIC_ERRORS = {
  "not-allowed": "The microphone is blocked. Allow it in the browser's address bar.",
  "service-not-allowed": "The microphone is blocked. Allow it in the browser's address bar.",
  "no-speech": "No speech heard. Try again, a little closer to the microphone.",
  "audio-capture": "No microphone found.",
  network: "Voice typing needs an internet connection.",
};

// "Describe the emergency": the operator types the call, or records it
// (English, Hindi or Marathi: speech-to-text in the browser); the AI
// translates it to English and fills in the trip (place, condition,
// hospital) and says why. The operator checks it and presses Start; the
// AI never controls signals (POST /assistant/intake).
function EmergencyIntake({ apiUrl, disabled, onFill }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [language, setLanguage] = useState("hi-IN");
  const [listening, setListening] = useState(false);
  const [micError, setMicError] = useState("");
  const recognition = useRef(null);
  const spoken = useRef("");

  const read = async (words) => {
    if (!words.trim() || busy) return;
    try {
      setBusy(true);
      setError("");
      const response = await fetch(`${apiUrl}/assistant/intake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: words }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "The AI could not read this request.");
      setResult(data);
      onFill(data);
    } catch (problem) {
      setResult(null);
      setError(problem.message === "Failed to fetch" ? "Can't reach the server." : problem.message);
      focusManualForm();
    } finally {
      setBusy(false);
    }
  };

  const submit = (event) => {
    event.preventDefault();
    read(text);
  };

  const startRecording = () => {
    const listener = new Recognition();
    listener.lang = language;
    listener.continuous = true;
    listener.interimResults = true;
    spoken.current = "";
    setMicError("");
    setResult(null);
    setError("");
    setText("");

    listener.onresult = (event) => {
      let words = "";
      for (const part of event.results) words += part[0].transcript;
      spoken.current = words;
      setText(words);
    };
    listener.onerror = (event) => {
      setMicError(MIC_ERRORS[event.error] || `Voice typing stopped (${event.error}).`);
    };
    // Stopped (by the button, or silence): the words go to the AI.
    listener.onend = () => {
      setListening(false);
      recognition.current = null;
      if (spoken.current.trim()) read(spoken.current);
    };

    recognition.current = listener;
    listener.start();
    setListening(true);
  };

  const stopRecording = () => recognition.current?.stop();

  return (
    <section className="panel-section intake">
      <div className="section-heading">
        <h2>Describe the emergency</h2>
        <span className="muted">AI fills in the trip</span>
      </div>

      {Recognition ? (
        <div className="voice-call">
          <select
            aria-label="Language of the call"
            value={language}
            disabled={disabled || busy || listening}
            onChange={(event) => setLanguage(event.target.value)}
          >
            {LANGUAGES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
          {listening ? (
            <button type="button" className="button voice-stop" onClick={stopRecording}>
              <i className="voice-dot" aria-hidden="true" /> Stop and read
            </button>
          ) : (
            <button
              type="button"
              className="button button-secondary"
              disabled={disabled || busy}
              onClick={startRecording}
              title="Speak the call in English, Hindi or Marathi; the words appear as you speak"
            >
              Record call
            </button>
          )}
        </div>
      ) : (
        <p className="field-hint">Voice calls work in Google Chrome. Here, type the call below.</p>
      )}
      {listening && <p className="field-hint" role="status">Listening… speak the call, then press Stop.</p>}
      {micError && <p className="field-error">{micError}</p>}

      <form onSubmit={submit} className="intake-form">
        <textarea
          rows={3}
          value={text}
          disabled={disabled || busy || listening}
          maxLength={600}
          placeholder="Type or record the call, e.g. Man collapsed holding his chest near Kasba Peth, family wants a government hospital"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) submit(event);
          }}
        />
        <button
          type="submit"
          className="button button-primary"
          disabled={disabled || busy || listening || !text.trim()}
        >
          {busy ? "Reading…" : "Fill in with AI"}
        </button>
      </form>
      {error && (
        <div className="intake-check failed" role="alert">
          <strong>The AI couldn&apos;t understand this call.</strong>
          <span>{error}</span>
          <span>Please fill in the trip by hand below.</span>
        </div>
      )}
      {result && !error && (
        <div className="intake-check" role="status">
          <strong>AI understood this. Please check it before sending.</strong>
          {result.english && result.english.trim() !== text.trim() && (
            <span className="intake-english">In English: &ldquo;{result.english}&rdquo;</span>
          )}
          <dl>
            <dt>Pickup</dt>
            <dd>{result.start.name}</dd>
            <dt>Patient</dt>
            <dd>{result.condition_label} · {result.level_name}</dd>
            <dt>Hospital</dt>
            <dd>{result.hospital.name}{result.government_only ? " · government only" : ""}</dd>
            <dt>Ambulance</dt>
            <dd>{result.from_base ? "Send the nearest 108 ambulance" : "Patient already on board"}</dd>
          </dl>
          {result.note && <span className="intake-guess">AI guessed: {result.note}</span>}
          <span className="muted">{result.reason}</span>
          <span className="muted">Wrong? Change it in Plan a trip below. Then press Start.</span>
        </div>
      )}
    </section>
  );
}

export default EmergencyIntake;
