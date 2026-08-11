import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Chat } from "../components/Chat";
import type { KBEntry, Machine, Print } from "../types";

type Tab = "prints" | "troubleshoot" | "kb";

export function MachinePage() {
  const { machineId } = useParams();
  const id = Number(machineId);
  const [machine, setMachine] = useState<Machine | null>(null);
  const [tab, setTab] = useState<Tab>("prints");
  const nav = useNavigate();

  useEffect(() => {
    api.getMachine(id).then(setMachine).catch(() => nav("/"));
  }, [id]);

  if (!machine) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="small muted"><Link to="/">← Machines</Link></div>
      <div className="spread" style={{ marginTop: 6 }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>{machine.name}</h1>
          <div className="muted small">{machine.location} {machine.description ? `· ${machine.description}` : ""}</div>
        </div>
        <DeleteMachineButton machine={machine} onDeleted={() => nav("/")} />
      </div>

      <div className="tabs">
        <div className={`tab ${tab === "prints" ? "active" : ""}`} onClick={() => setTab("prints")}>Prints</div>
        <div className={`tab ${tab === "troubleshoot" ? "active" : ""}`} onClick={() => setTab("troubleshoot")}>Troubleshoot</div>
        <div className={`tab ${tab === "kb" ? "active" : ""}`} onClick={() => setTab("kb")}>Knowledge Base</div>
      </div>

      {tab === "prints" && <PrintsTab machineId={id} />}
      {tab === "troubleshoot" && <Chat machineId={id} />}
      {tab === "kb" && <KBTab machineId={id} />}
    </>
  );
}

function DeleteMachineButton({ machine, onDeleted }: { machine: Machine; onDeleted: () => void }) {
  return (
    <button className="danger" onClick={async () => {
      if (window.confirm(`Delete machine "${machine.name}" and all its prints? This cannot be undone.`)) {
        await api.deleteMachine(machine.id);
        onDeleted();
      }
    }}>Delete machine</button>
  );
}

function PrintsTab({ machineId }: { machineId: number }) {
  const [prints, setPrints] = useState<Print[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const nav = useNavigate();

  const load = () => api.listPrints(machineId).then(setPrints);
  useEffect(() => {
    load();
    // poll while anything is still processing
    const t = setInterval(async () => {
      const p = await api.listPrints(machineId);
      setPrints(p);
      if (!p.some((x) => x.status === "processing")) clearInterval(t);
    }, 4000);
    return () => clearInterval(t);
  }, [machineId]);

  return (
    <>
      <div className="spread">
        <h2>Schematics</h2>
        <button className="primary" onClick={() => setShowUpload(true)}>+ Upload print</button>
      </div>
      {prints.length === 0 ? (
        <div className="empty"><p>No prints uploaded for this machine yet.</p></div>
      ) : (
        <table>
          <thead><tr><th>Title</th><th>Pages</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {prints.map((p) => (
              <tr key={p.id}>
                <td><Link to={`/prints/${p.id}`}>{p.title}</Link><div className="muted small">{p.filename}</div></td>
                <td>{p.page_count || "—"}</td>
                <td>
                  <span className={`status ${p.status}`}>{p.status}</span>
                  {p.status_detail && <div className="muted small">{p.status_detail}</div>}
                </td>
                <td className="row" style={{ justifyContent: "flex-end" }}>
                  <button onClick={() => nav(`/prints/${p.id}`)}>View</button>
                  <button className="danger" onClick={async () => {
                    if (window.confirm(`Delete print "${p.title}"?`)) { await api.deletePrint(p.id); load(); }
                  }}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {showUpload && <UploadModal machineId={machineId} onClose={() => setShowUpload(false)} onDone={() => { setShowUpload(false); load(); }} />}
    </>
  );
}

function UploadModal({ machineId, onClose, onDone }: { machineId: number; onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const submit = async () => {
    if (!file) return setError("Choose a PDF file");
    setBusy(true); setError("");
    try {
      await api.uploadPrint(machineId, title.trim() || file.name.replace(/\.pdf$/i, ""), file);
      onDone();
    } catch (e: any) {
      setError(e.message); setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Upload print</h2>
        <p className="muted small">PDF schematics. Vector CAD exports and scanned prints are both supported (OCR runs automatically for scans).</p>
        <label>Title</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Main Control Panel — Sheet 1" />
        <label>PDF file *</label>
        <input ref={fileRef} type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        {error && <p style={{ color: "var(--bad)" }} className="small">{error}</p>}
        <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button className="primary" onClick={submit} disabled={busy}>{busy ? "Uploading…" : "Upload & process"}</button>
        </div>
      </div>
    </div>
  );
}

function KBTab({ machineId }: { machineId: number }) {
  const [entries, setEntries] = useState<KBEntry[]>([]);
  const [symptom, setSymptom] = useState("");
  const [guidance, setGuidance] = useState("");

  const load = () => api.listKB(machineId).then(setEntries);
  useEffect(() => { load(); }, [machineId]);

  const add = async () => {
    if (!symptom.trim() || !guidance.trim()) return;
    await api.createKB({ machine_id: machineId, symptom: symptom.trim(), guidance: guidance.trim() });
    setSymptom(""); setGuidance(""); load();
  };

  return (
    <>
      <h2>Knowledge Base</h2>
      <p className="muted small">Verified symptom → guidance entries. The assistant treats these as authoritative and retrieves them on matching questions. Confirmed answers and corrections land here automatically.</p>
      <div className="card" style={{ margin: "12px 0" }}>
        <label>Symptom</label>
        <input value={symptom} onChange={(e) => setSymptom(e.target.value)} placeholder="e.g. cutter motor fails to start" />
        <label>Verified guidance</label>
        <textarea value={guidance} onChange={(e) => setGuidance(e.target.value)} rows={3}
                  placeholder="e.g. Check for 120V between wire A1 and ground; confirm contactor M3 is pulled in; inspect overload OL3." />
        <div style={{ marginTop: 10 }}><button className="primary" onClick={add}>Add entry</button></div>
      </div>
      {entries.length === 0 ? (
        <div className="empty"><p>No knowledge base entries yet.</p></div>
      ) : (
        entries.map((e) => (
          <div key={e.id} className="card" style={{ marginBottom: 10 }}>
            <div className="spread">
              <strong>{e.symptom}</strong>
              <button className="danger" onClick={async () => { await api.deleteKB(e.id); load(); }}>Delete</button>
            </div>
            <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{e.guidance}</div>
            <div className="muted small" style={{ marginTop: 6 }}>source: {e.source}</div>
          </div>
        ))
      )}
    </>
  );
}
