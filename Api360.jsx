import React, { useState, useEffect } from "react";
import { SectionHeader } from "./AppShell.jsx";
import ProjectSwitcher from "./ProjectSwitcher.jsx";
import ProjectBadge from "./ProjectBadge.jsx";
import PiiBadge from "./PiiBadge.jsx";
import { api } from "./api.js";

const SUBVIEWS = ["Sources", "API Dependency", "Business Flow"];
const METHOD_COLOR = { GET: "#159943", POST: "#0091bf", PUT: "#e67e22", DELETE: "#c1113a" };

export default function Api360({ t }) {
  const [project, setProject] = useState("all");
  const [view, setView] = useState("Sources");
  const [sources, setSources] = useState([]);
  const [stats, setStats] = useState(null);
  const [deps, setDeps] = useState([]);
  const [flows, setFlows] = useState([]);
  const [activeFlow, setActiveFlow] = useState(null);

  const pid = project === "all" || project === "sei" || project === "non-sei" ? null : project;
  useEffect(() => {
    api.apiSources(project).then((r) => setSources(r.sources || []));
    api.apiStats().then(setStats).catch(() => {});
    api.apiDependencies(pid).then((r) => setDeps(r.dependencies || []));
    api.apiFlows(pid).then((r) => { setFlows(r.flows || []); setActiveFlow((r.flows || [])[0] || null); });
  }, [project]);

  const visibleSources = sources;

  return (
    <div>
      <SectionHeader t={t}>API 360</SectionHeader>
      <ProjectSwitcher t={t} value={project} onChange={setProject}
        stats={stats?.project_counts} />

      <div style={{ display: "flex", gap: 2, margin: "20px 0",
        borderBottom: `1px solid ${t.disabled}` }}>
        {SUBVIEWS.map((v) => (
          <button key={v} onClick={() => setView(v)} style={{ background: "none", border: "none",
            fontFamily: t.font, fontSize: 13, fontWeight: 500, padding: "10px 18px", cursor: "pointer",
            color: view === v ? t.accent : t.sub,
            borderBottom: `2px solid ${view === v ? t.accent : "transparent"}`, marginBottom: -1 }}>
            {v}</button>
        ))}
      </div>

      {view === "Sources" && (
        <table style={{ width: "100%", borderCollapse: "collapse", background: t.panel,
          border: `1px solid ${t.disabled}`, borderRadius: t.radius.md, overflow: "hidden" }}>
          <thead><tr>{["Source", "Project", "Feature Group", "Release", "Kind", "Endpoints"].map((h) => (
            <th key={h} style={th(t)}>{h}</th>))}</tr></thead>
          <tbody>{visibleSources.map((s) => (
            <tr key={s.source_id}>
              <td style={td(t)}><b>{s.source_id}</b></td>
              <td style={td(t)}><ProjectBadge projectId={s.project_id} t={t} /></td>
              <td style={td(t)}>{s.feature_group}</td><td style={td(t)}>{s.release_version}</td>
              <td style={td(t)}>{s.kind}</td><td style={td(t)}>{s.endpoint_count}</td>
            </tr>))}</tbody>
        </table>
      )}

      {view === "API Dependency" && (
        <div style={{ background: t.panel, border: `1px solid ${t.disabled}`,
          borderRadius: t.radius.md, padding: 20 }}>
          <div style={{ fontSize: 12, color: t.sub, marginBottom: 15 }}>
            Endpoint dependency graph — method color on the border, arrow shows call direction.
          </div>
          <DependencyGraph t={t} deps={deps} />
        </div>
      )}

      {view === "Business Flow" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
            {flows.map((f) => (
              <button key={f.flow_key} onClick={() => setActiveFlow(f)} style={{
                height: t.height.btnSm, padding: "0 14px", border: `1px solid ${t.border}`,
                borderRadius: t.radius.pill, cursor: "pointer", fontFamily: t.font, fontSize: 13,
                background: activeFlow?.flow_key === f.flow_key ? t.modApi : t.panel,
                color: activeFlow?.flow_key === f.flow_key ? "#fff" : t.text }}>
                {f.flow_name}</button>))}
          </div>
          {activeFlow && <FlowGraph t={t} flow={activeFlow} />}
        </div>
      )}
    </div>
  );
}

function DependencyGraph({ t, deps }) {
  // gather unique endpoints, lay out source column + target column
  const froms = [...new Set(deps.map((d) => d.from_endpoint))];
  const tos = [...new Set(deps.map((d) => d.to_endpoint))];
  const NW = 230, NH = 40, GAP_Y = 18, X1 = 20, X2 = 340;
  const yOf = (arr, i) => 20 + i * (NH + GAP_Y);
  const fromPos = Object.fromEntries(froms.map((e, i) => [e, { x: X1, y: yOf(froms, i) }]));
  const toPos = Object.fromEntries(tos.map((e, i) => [e, { x: X2, y: yOf(tos, i) }]));
  const h = Math.max(yOf(froms, froms.length), yOf(tos, tos.length)) + 20;

  const node = (e, pos, method) => (
    <div key={e} style={{ position: "absolute", left: pos.x, top: pos.y, width: NW, height: NH,
      background: t.panel, border: `1px solid ${t.border}`, borderLeft: `3px solid ${METHOD_COLOR[method] || t.muted}`,
      borderRadius: t.radius.md, display: "flex", alignItems: "center", padding: "0 10px",
      fontSize: 12, boxShadow: t.shadow.reg }}>
      <span style={{ fontSize: 9, fontWeight: 700, color: METHOD_COLOR[method] || t.muted,
        marginRight: 6 }}>{method}</span>
      <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {e.replace(/^(GET|POST|PUT|DELETE)\s/, "")}</span>
    </div>
  );

  return (
    <div style={{ position: "relative", height: h, minWidth: X2 + NW + 20 }}>
      <svg width={X2 + NW + 20} height={h} style={{ position: "absolute", top: 0, left: 0 }}>
        <defs><marker id="apidep" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 Z" fill={t.muted} /></marker></defs>
        {deps.map((d, i) => {
          const a = fromPos[d.from_endpoint], b = toPos[d.to_endpoint];
          const ax = a.x + NW, ay = a.y + NH / 2, bx = b.x, by = b.y + NH / 2, dx = (bx - ax) * 0.5;
          return <path key={i} d={`M${ax} ${ay} C${ax + dx} ${ay} ${bx - dx} ${by} ${bx} ${by}`}
            fill="none" stroke={t.muted} strokeWidth="1.5"
            strokeDasharray={d.dep_type === "calls" ? "none" : "4 3"} markerEnd="url(#apidep)" />;
        })}
      </svg>
      {froms.map((e) => node(e, fromPos[e], deps.find((d) => d.from_endpoint === e)?.method_from))}
      {tos.map((e) => node(e, toPos[e], deps.find((d) => d.to_endpoint === e)?.method_to))}
    </div>
  );
}

function FlowGraph({ t, flow }) {
  return (
    <div style={{ background: t.panel, border: `1px solid ${t.disabled}`,
      borderRadius: t.radius.md, padding: 25 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 0, flexWrap: "wrap" }}>
        {flow.steps.map((s, i) => (
          <React.Fragment key={s.step_order}>
            <div style={{ background: "#f3effd", border: `1px solid ${t.projPivotal}`,
              borderRadius: t.radius.md, padding: "12px 16px", minWidth: 150 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: t.projPivotal,
                textTransform: "uppercase" }}>Step {s.step_order}</div>
              <div style={{ fontSize: 13, fontWeight: 600, margin: "3px 0" }}>{s.label}</div>
              <div style={{ fontSize: 11, color: t.sub, fontFamily: "monospace" }}>{s.endpoint}</div>
              {s.variable_passed && s.variable_passed !== "-" && (
                <div style={{ fontSize: 10, color: t.modData, marginTop: 4 }}>
                  → {s.variable_passed}</div>)}
            </div>
            {i < flow.steps.length - 1 && (
              <div style={{ color: t.muted, fontSize: 18, padding: "0 8px" }}>▶</div>)}
          </React.Fragment>
        ))}
      </div>
      <div style={{ fontSize: 12, color: t.textMuted, marginTop: 15 }}>
        Variables passed between steps are shown under each call — the chain shows how one API's
        output feeds the next.
      </div>
    </div>
  );
}

const th = (t) => ({ background: "#f0f4f5", textAlign: "left", padding: "10px 14px", fontSize: 11,
  fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, color: t.accent,
  borderBottom: `1px solid ${t.disabled}` });
const td = (t) => ({ padding: "10px 14px", fontSize: 13, borderBottom: `1px solid ${t.bg}` });
