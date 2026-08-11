import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, askStream } from "../api";
import type { Citation } from "../types";

interface UIMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  feedback?: string | null;
}

export function Chat({ machineId }: { machineId: number }) {
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const nav = useNavigate();

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [messages]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "" }]);

    try {
      await askStream(machineId, q, sessionId, {
        onMeta: (m) => {
          setSessionId(m.session_id);
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], citations: m.citations };
            return copy;
          });
        },
        onDelta: (text) => {
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + text };
            return copy;
          });
        },
        onDone: (messageId) => {
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], id: messageId };
            return copy;
          });
        },
      });
    } catch (e: any) {
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = { ...copy[copy.length - 1], content: `Error: ${e.message}` };
        return copy;
      });
    } finally {
      setBusy(false);
    }
  };

  const rate = async (idx: number, good: boolean) => {
    const msg = messages[idx];
    if (!msg.id) return;
    let corrected: string | undefined;
    if (!good) {
      corrected = window.prompt("What is the correct guidance? (saved to this machine's knowledge base)") || undefined;
    }
    await api.feedback(msg.id, good ? "good" : "bad", corrected);
    setMessages((prev) => prev.map((m, i) => (i === idx ? { ...m, feedback: good ? "good" : "bad" } : m)));
  };

  return (
    <div className="chat">
      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && (
          <div className="empty">
            <p>Describe the electrical problem in plain English.</p>
            <p className="small">e.g. <em>"cutter motor fails to start"</em> or <em>"no control voltage at the main panel"</em></p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content || (busy && i === messages.length - 1 ? "…" : "")}
            {m.role === "assistant" && m.citations && m.citations.length > 0 && (
              <div className="citations">
                {dedupeCitations(m.citations).map((c, j) => (
                  <span key={j} className="chip" title={c.snippet || ""}
                        onClick={() => nav(`/prints/${c.print_id}${c.page_number ? `?page=${c.page_number}` : ""}`)}>
                    📄 {c.print_title}{c.page_number ? ` p${c.page_number}` : ""}
                  </span>
                ))}
              </div>
            )}
            {m.role === "assistant" && m.id && !busy && (
              <div className="feedback">
                {m.feedback ? (
                  <span className="small muted">{m.feedback === "good" ? "✓ marked helpful" : "✎ correction saved"}</span>
                ) : (
                  <>
                    <button onClick={() => rate(i, true)}>👍 Correct</button>
                    <button onClick={() => rate(i, false)}>👎 Fix this</button>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="chat-input">
        <textarea value={input} placeholder="Describe the problem…" onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="primary" onClick={send} disabled={busy || !input.trim()}>Ask</button>
      </div>
    </div>
  );
}

function dedupeCitations(cites: Citation[]): Citation[] {
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const c of cites) {
    const key = `${c.print_id}:${c.page_number}`;
    if (!seen.has(key)) { seen.add(key); out.push(c); }
  }
  return out.slice(0, 8);
}
