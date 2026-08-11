import type {
  ChatMessage,
  ChatSession,
  Component,
  KBEntry,
  Machine,
  OllamaStatus,
  Page,
  Print,
  SearchHit,
  Wire,
} from "./types";

const TOKEN_KEY = "ai_electrician_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  // system
  health: () => req<{ status: string; database: boolean }>("/api/health"),
  ollamaStatus: () => req<OllamaStatus>("/api/ollama/status"),
  config: () => req<{ auth_enabled: boolean }>("/api/config"),
  login: (password: string) => req<{ token: string }>("/api/login", { method: "POST", body: JSON.stringify({ password }) }),

  // machines
  listMachines: () => req<Machine[]>("/api/machines"),
  getMachine: (id: number) => req<Machine>(`/api/machines/${id}`),
  createMachine: (data: Partial<Machine>) => req<Machine>("/api/machines", { method: "POST", body: JSON.stringify(data) }),
  updateMachine: (id: number, data: Partial<Machine>) => req<Machine>(`/api/machines/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteMachine: (id: number) => req<void>(`/api/machines/${id}`, { method: "DELETE" }),

  // prints
  listPrints: (machineId: number) => req<Print[]>(`/api/machines/${machineId}/prints`),
  getPrint: (id: number) => req<Print>(`/api/prints/${id}`),
  deletePrint: (id: number) => req<void>(`/api/prints/${id}`, { method: "DELETE" }),
  listPages: (printId: number) => req<Page[]>(`/api/prints/${printId}/pages`),
  listComponents: (printId: number) => req<Component[]>(`/api/prints/${printId}/components`),
  listWires: (printId: number) => req<Wire[]>(`/api/prints/${printId}/wires`),
  pageImageUrl: (printId: number, page: number) => `/api/prints/${printId}/pages/${page}/image`,
  pdfUrl: (printId: number) => `/api/prints/${printId}/file`,
  uploadPrint: async (machineId: number, title: string, file: File) => {
    const form = new FormData();
    form.append("title", title);
    form.append("file", file);
    const res = await fetch(`/api/machines/${machineId}/prints`, { method: "POST", headers: { ...authHeaders() }, body: form });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json() as Promise<Print>;
  },

  // search
  search: (q: string, machineId?: number) =>
    req<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}${machineId ? `&machine_id=${machineId}` : ""}`),

  // review / corrections
  updateComponent: (id: number, data: Partial<Component> & { note?: string }) =>
    req<Component>(`/api/components/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  updateWire: (id: number, data: Partial<Wire> & { note?: string }) =>
    req<Wire>(`/api/wires/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteComponent: (id: number) => req<void>(`/api/components/${id}`, { method: "DELETE" }),
  deleteWire: (id: number) => req<void>(`/api/wires/${id}`, { method: "DELETE" }),

  // knowledge base
  listKB: (machineId: number) => req<KBEntry[]>(`/api/machines/${machineId}/kb`),
  createKB: (data: { machine_id: number; symptom: string; guidance: string; wire_refs?: string[] }) =>
    req<KBEntry>("/api/kb", { method: "POST", body: JSON.stringify(data) }),
  deleteKB: (id: number) => req<void>(`/api/kb/${id}`, { method: "DELETE" }),

  // chat
  listSessions: (machineId: number) => req<ChatSession[]>(`/api/chat/sessions?machine_id=${machineId}`),
  listChatMessages: (sessionId: number) => req<ChatMessage[]>(`/api/chat/sessions/${sessionId}/messages`),
  feedback: (messageId: number, feedback: string, corrected_guidance?: string) =>
    req<KBEntry | null>(`/api/chat/messages/${messageId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback, corrected_guidance }),
    }),
};

/**
 * Ask a troubleshooting question, streaming the answer.
 * Calls onMeta once, onDelta for each text chunk, onDone at the end.
 */
export async function askStream(
  machineId: number,
  message: string,
  sessionId: number | null,
  handlers: {
    onMeta?: (m: { session_id: number; citations: any[] }) => void;
    onDelta?: (text: string) => void;
    onDone?: (messageId: number) => void;
  }
) {
  const res = await fetch("/api/chat/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ machine_id: machineId, message, session_id: sessionId }),
  });
  if (!res.ok || !res.body) throw new Error("Failed to start chat");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const obj = JSON.parse(line);
      if (obj.type === "meta") handlers.onMeta?.(obj);
      else if (obj.type === "delta") handlers.onDelta?.(obj.text);
      else if (obj.type === "done") handlers.onDone?.(obj.message_id);
    }
  }
}
