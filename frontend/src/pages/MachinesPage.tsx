import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Machine } from "../types";

export function MachinesPage() {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const nav = useNavigate();

  const load = () => {
    setLoading(true);
    api.listMachines().then(setMachines).finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <>
      <div className="spread">
        <h1>Machines</h1>
        <button className="primary" onClick={() => setShowCreate(true)}>+ New machine</button>
      </div>
      <p className="muted">Prints are organized by machine. Pick a machine to view its schematics or troubleshoot.</p>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : machines.length === 0 ? (
        <div className="empty">
          <p>No machines yet.</p>
          <button className="primary" onClick={() => setShowCreate(true)}>Create your first machine</button>
        </div>
      ) : (
        <div className="grid cards" style={{ marginTop: 16 }}>
          {machines.map((m) => (
            <div key={m.id} className="card clickable" onClick={() => nav(`/machines/${m.id}`)}>
              <h3>{m.name}</h3>
              {m.location && <div className="meta">📍 {m.location}</div>}
              {m.description && <div className="meta" style={{ marginTop: 4 }}>{m.description}</div>}
              <div className="meta" style={{ marginTop: 10 }}>{m.print_count} print{m.print_count === 1 ? "" : "s"}</div>
            </div>
          ))}
        </div>
      )}

      {showCreate && <CreateMachineModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); load(); }} />}
    </>
  );
}

function CreateMachineModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");

  const submit = async () => {
    if (!name.trim()) return setError("Name is required");
    try {
      await api.createMachine({ name: name.trim(), location: location.trim() || null, description: description.trim() || null });
      onCreated();
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New machine</h2>
        <label>Name *</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Line 3 Cutter" autoFocus />
        <label>Location</label>
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Building B, Bay 12" />
        <label>Description</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
        {error && <p style={{ color: "var(--bad)" }} className="small">{error}</p>}
        <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={submit}>Create</button>
        </div>
      </div>
    </div>
  );
}
