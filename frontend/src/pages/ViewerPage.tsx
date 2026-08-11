import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Component, Page, Print, SearchHit, Wire } from "../types";

export function ViewerPage() {
  const { printId } = useParams();
  const id = Number(printId);
  const [params, setParams] = useSearchParams();
  const [print, setPrint] = useState<Print | null>(null);
  const [pages, setPages] = useState<Page[]>([]);
  const [components, setComponents] = useState<Component[]>([]);
  const [wires, setWires] = useState<Wire[]>([]);
  const [current, setCurrent] = useState<number>(Number(params.get("page")) || 1);
  const [zoom, setZoom] = useState(1);
  const [showBoxes, setShowBoxes] = useState(true);
  const [sidebar, setSidebar] = useState<"search" | "review">("review");

  const reload = () => {
    api.listComponents(id).then(setComponents);
    api.listWires(id).then(setWires);
  };
  useEffect(() => {
    api.getPrint(id).then(setPrint);
    api.listPages(id).then(setPages);
    reload();
  }, [id]);

  useEffect(() => { setParams({ page: String(current) }, { replace: true }); }, [current]);

  const page = pages.find((p) => p.page_number === current);
  const pageComponents = components.filter((c) => c.page_number === current);
  const pageWires = wires.filter((w) => w.page_number === current);

  if (!print) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="small muted">
        <Link to={`/machines/${print.machine_id}`}>← Back to machine</Link>
      </div>
      <div className="spread" style={{ marginTop: 6 }}>
        <h1 style={{ marginBottom: 0 }}>{print.title}</h1>
        <a className="btn" href={api.pdfUrl(id)} target="_blank" rel="noreferrer">⬇ Download PDF</a>
      </div>

      <div className="toolbar" style={{ marginTop: 10 }}>
        <button disabled={current <= 1} onClick={() => setCurrent((c) => c - 1)}>‹ Prev</button>
        <span className="small">Page {current} / {print.page_count || pages.length}</span>
        <button disabled={current >= (print.page_count || pages.length)} onClick={() => setCurrent((c) => c + 1)}>Next ›</button>
        <span style={{ width: 12 }} />
        <button onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}>−</button>
        <span className="small">{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((z) => Math.min(4, z + 0.25))}>+</button>
        <span style={{ width: 12 }} />
        <label className="row small" style={{ margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> Overlay boxes
        </label>
      </div>

      <div className="viewer">
        <div className="sidebar">
          <div className="tabs" style={{ margin: "0 0 10px" }}>
            <div className={`tab ${sidebar === "review" ? "active" : ""}`} onClick={() => setSidebar("review")}>Review</div>
            <div className={`tab ${sidebar === "search" ? "active" : ""}`} onClick={() => setSidebar("search")}>Search</div>
          </div>
          {sidebar === "search"
            ? <SearchPanel machineId={print.machine_id} onJump={(pid, pg) => { if (pid === id && pg) setCurrent(pg); }} />
            : <ReviewPanel components={pageComponents} wires={pageWires} onChanged={reload} />}
        </div>

        <div className="page-wrap">
          {page ? (
            <div style={{ position: "relative", display: "inline-block", transform: `scale(${zoom})`, transformOrigin: "top left" }}>
              <img className="page-img" src={api.pageImageUrl(id, current)} alt={`Page ${current}`} />
              {showBoxes && pageComponents.map((c) => c.bbox && (
                <div key={`c${c.id}`} className="bbox" title={c.designator}
                     style={boxStyle(c.bbox, page)} />
              ))}
              {showBoxes && pageWires.map((w) => w.bbox && (
                <div key={`w${w.id}`} className="bbox wire" title={w.wire_number}
                     style={boxStyle(w.bbox, page)} />
              ))}
            </div>
          ) : <div className="empty">Page image not available yet.</div>}
        </div>
      </div>
    </>
  );
}

function boxStyle(bbox: number[], page: Page): React.CSSProperties {
  const [x0, y0, x1, y1] = bbox;
  return { left: x0, top: y0, width: Math.max(2, x1 - x0), height: Math.max(2, y1 - y0) };
}

function SearchPanel({ machineId, onJump }: { machineId: number; onJump: (printId: number, page: number | null) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const run = async () => { if (q.trim()) setHits(await api.search(q.trim(), machineId)); };
  return (
    <div>
      <div className="row">
        <input value={q} placeholder="wire #, component, text…" onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && run()} />
        <button onClick={run}>Go</button>
      </div>
      <p className="muted small" style={{ marginTop: 8 }}>Search this machine's prints to confirm what the AI says.</p>
      {hits.map((h, i) => (
        <div key={i} className="card" style={{ padding: 10, marginTop: 8, cursor: "pointer" }}
             onClick={() => onJump(h.print_id, h.page_number)}>
          <div className="small"><span className={`status ${h.kind === "wire" ? "processing" : "review"}`}>{h.kind}</span> {h.print_title}{h.page_number ? ` · p${h.page_number}` : ""}</div>
          <div className="small" style={{ marginTop: 4 }}>{h.snippet}</div>
        </div>
      ))}
    </div>
  );
}

function ReviewPanel({ components, wires, onChanged }: { components: Component[]; wires: Wire[]; onChanged: () => void }) {
  return (
    <div>
      <p className="muted small">Correct anything the AI got wrong. Edits are saved and immediately improve the assistant's answers.</p>
      <h3 style={{ margin: "10px 0 4px" }}>Components ({components.length})</h3>
      {components.length === 0 && <div className="muted small">None on this page.</div>}
      {components.map((c) => <ComponentRow key={c.id} c={c} onChanged={onChanged} />)}
      <h3 style={{ margin: "16px 0 4px" }}>Wires ({wires.length})</h3>
      {wires.length === 0 && <div className="muted small">None on this page.</div>}
      {wires.map((w) => <WireRow key={w.id} w={w} onChanged={onChanged} />)}
    </div>
  );
}

function ComponentRow({ c, onChanged }: { c: Component; onChanged: () => void }) {
  const [edit, setEdit] = useState(false);
  const [designator, setDesignator] = useState(c.designator);
  const [type, setType] = useState(c.type || "");
  const [voltage, setVoltage] = useState(c.voltage || "");
  const low = c.confidence < 0.55 && !c.verified;

  const save = async () => {
    await api.updateComponent(c.id, { designator, type, voltage });
    setEdit(false); onChanged();
  };

  return (
    <div className="card" style={{ padding: 10, marginBottom: 8 }}>
      {edit ? (
        <>
          <input value={designator} onChange={(e) => setDesignator(e.target.value)} placeholder="designator" />
          <div className="row" style={{ marginTop: 6 }}>
            <input value={type} onChange={(e) => setType(e.target.value)} placeholder="type" />
            <input value={voltage} onChange={(e) => setVoltage(e.target.value)} placeholder="voltage" />
          </div>
          <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }}>
            <button onClick={() => setEdit(false)}>Cancel</button>
            <button className="primary" onClick={save}>Save</button>
          </div>
        </>
      ) : (
        <div className="spread">
          <div>
            <strong>{c.designator}</strong> <span className="muted small">{c.type}{c.voltage ? ` · ${c.voltage}` : ""}</span>
            {c.verified ? <span className="small" style={{ color: "var(--good)" }}> ✓</span> : low ? <span className="badge-low"> ⚠ low confidence</span> : null}
          </div>
          <div className="row">
            <button onClick={() => setEdit(true)}>Edit</button>
            {!c.verified && <button onClick={async () => { await api.updateComponent(c.id, { verified: true }); onChanged(); }}>✓ Verify</button>}
            <button className="danger" onClick={async () => { await api.deleteComponent(c.id); onChanged(); }}>✕</button>
          </div>
        </div>
      )}
    </div>
  );
}

function WireRow({ w, onChanged }: { w: Wire; onChanged: () => void }) {
  const [edit, setEdit] = useState(false);
  const [wireNumber, setWireNumber] = useState(w.wire_number);
  const [voltage, setVoltage] = useState(w.voltage || "");
  const [fromRef, setFromRef] = useState(w.from_ref || "");
  const [toRef, setToRef] = useState(w.to_ref || "");
  const low = w.confidence < 0.55 && !w.verified;

  const save = async () => {
    await api.updateWire(w.id, { wire_number: wireNumber, voltage, from_ref: fromRef, to_ref: toRef });
    setEdit(false); onChanged();
  };

  return (
    <div className="card" style={{ padding: 10, marginBottom: 8 }}>
      {edit ? (
        <>
          <div className="row">
            <input value={wireNumber} onChange={(e) => setWireNumber(e.target.value)} placeholder="wire #" />
            <input value={voltage} onChange={(e) => setVoltage(e.target.value)} placeholder="voltage" />
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <input value={fromRef} onChange={(e) => setFromRef(e.target.value)} placeholder="from" />
            <input value={toRef} onChange={(e) => setToRef(e.target.value)} placeholder="to" />
          </div>
          <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }}>
            <button onClick={() => setEdit(false)}>Cancel</button>
            <button className="primary" onClick={save}>Save</button>
          </div>
        </>
      ) : (
        <div className="spread">
          <div>
            <strong>{w.wire_number}</strong> <span className="muted small">{w.voltage}{(w.from_ref || w.to_ref) ? ` · ${w.from_ref || "?"}→${w.to_ref || "?"}` : ""}</span>
            {w.verified ? <span className="small" style={{ color: "var(--good)" }}> ✓</span> : low ? <span className="badge-low"> ⚠</span> : null}
          </div>
          <div className="row">
            <button onClick={() => setEdit(true)}>Edit</button>
            {!w.verified && <button onClick={async () => { await api.updateWire(w.id, { verified: true }); onChanged(); }}>✓</button>}
            <button className="danger" onClick={async () => { await api.deleteWire(w.id); onChanged(); }}>✕</button>
          </div>
        </div>
      )}
    </div>
  );
}
