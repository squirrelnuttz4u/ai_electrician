import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api, getToken, setToken } from "./api";
import type { OllamaStatus } from "./types";

export function Layout() {
  const [ollama, setOllama] = useState<OllamaStatus | null>(null);
  const [authNeeded, setAuthNeeded] = useState(false);

  useEffect(() => {
    api.config().then((c) => {
      if (c.auth_enabled && !getToken()) setAuthNeeded(true);
    }).catch(() => {});
    api.ollamaStatus().then(setOllama).catch(() => setOllama({ reachable: false, models: [] }));
  }, []);

  if (authNeeded) return <LoginGate onDone={() => setAuthNeeded(false)} />;

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand"><span className="bolt">⚡</span> AI Electrician</Link>
        <span className="spacer" />
        <OllamaPill status={ollama} />
      </header>
      <main className="content">
        <div className="container">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function OllamaPill({ status }: { status: OllamaStatus | null }) {
  if (!status) return <span className="pill">Ollama: checking…</span>;
  if (!status.reachable) return <span className="pill bad" title={status.error}>Ollama: offline</span>;
  const missing = status.configured
    ? Object.values(status.configured).filter((m) => !m.present).map((m) => m.name)
    : [];
  if (missing.length) return <span className="pill" title={`Missing models: ${missing.join(", ")}`}>Ollama: models missing</span>;
  return <span className="pill ok">Ollama: ready</span>;
}

function LoginGate({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const submit = async () => {
    try {
      const { token } = await api.login(password);
      setToken(token);
      onDone();
    } catch (e: any) {
      setError(e.message || "Login failed");
    }
  };
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>⚡ AI Electrician</h2>
        <p className="muted small">This deployment is password protected.</p>
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && submit()} />
        {error && <p style={{ color: "var(--bad)" }} className="small">{error}</p>}
        <div style={{ marginTop: 14 }}><button className="primary" onClick={submit}>Sign in</button></div>
      </div>
    </div>
  );
}
