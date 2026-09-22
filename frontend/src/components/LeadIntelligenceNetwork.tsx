import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { BufferGeometry, IcosahedronGeometry, Line as ThreeLine, LineBasicMaterial, MathUtils, Vector3 } from "three";
import type { Mesh, MeshStandardMaterial, Points } from "three";

import type { AgentActivity, Lead, PipelineAnalytics } from "../types";

type Stage = {
  id: string;
  label: string;
  shortLabel: string;
  lane: "Inbound" | "AI Processing" | "Decision" | "Destinations";
  position: [number, number, number];
  screen: [number, number];
  detail: string;
};

const stages: Stage[] = [
  { id: "website", label: "Website", shortLabel: "Website", lane: "Inbound", position: [-5.5, 2.2, -.7], screen: [6, 20], detail: "Inbound web signal" },
  { id: "email", label: "Email", shortLabel: "Email", lane: "Inbound", position: [-5.7, .7, .35], screen: [5, 38], detail: "Mailbox inquiry" },
  { id: "linkedin", label: "LinkedIn", shortLabel: "LinkedIn", lane: "Inbound", position: [-5.4, -.8, -.2], screen: [6, 57], detail: "Official lead input" },
  { id: "clutch", label: "Clutch", shortLabel: "Clutch", lane: "Inbound", position: [-5.1, -2.15, .7], screen: [8, 74], detail: "Marketplace inquiry" },
  { id: "normalize", label: "Normalize", shortLabel: "Normalize", lane: "AI Processing", position: [-3.25, 0, 0], screen: [22, 48], detail: "Canonical lead contract" },
  { id: "context", label: "Context", shortLabel: "Context", lane: "AI Processing", position: [-1.35, 1.15, .4], screen: [36, 34], detail: "Intent, evidence and uncertainty" },
  { id: "icp", label: "ICP", shortLabel: "ICP", lane: "AI Processing", position: [.35, .05, -.25], screen: [48, 47], detail: "Grounded customer fit" },
  { id: "qualification", label: "Qualification", shortLabel: "Qualify", lane: "AI Processing", position: [1.8, 1.2, .5], screen: [59, 32], detail: "Explainable 40 / 35 / 25 score" },
  { id: "routing", label: "Routing", shortLabel: "Route", lane: "Decision", position: [3.15, .05, 0], screen: [69, 47], detail: "Queue, priority and representative" },
  { id: "actions", label: "Action Plan", shortLabel: "Action", lane: "Decision", position: [4.2, 1.35, -.25], screen: [77, 29], detail: "Evidence-backed next step" },
  { id: "salesforce", label: "Salesforce", shortLabel: "Salesforce", lane: "Destinations", position: [5.65, 2.15, .5], screen: [89, 19], detail: "CRM delivery" },
  { id: "hubspot", label: "HubSpot", shortLabel: "HubSpot", lane: "Destinations", position: [5.85, .75, -.45], screen: [90, 39], detail: "CRM delivery" },
  { id: "slack", label: "Slack", shortLabel: "Slack", lane: "Destinations", position: [5.7, -.75, .55], screen: [89, 58], detail: "Operational notification" },
  { id: "rep", label: "Sales Rep", shortLabel: "Sales Rep", lane: "Destinations", position: [5.3, -2.1, -.35], screen: [87, 75], detail: "Human action" },
];

const stageById = new Map(stages.map((stage) => [stage.id, stage]));
const links: Array<[string, string]> = [
  ["website", "normalize"], ["email", "normalize"], ["linkedin", "normalize"], ["clutch", "normalize"],
  ["normalize", "context"], ["context", "icp"], ["icp", "qualification"], ["qualification", "routing"],
  ["routing", "actions"], ["actions", "salesforce"], ["actions", "hubspot"], ["routing", "slack"], ["routing", "rep"],
];

const sourceStage = (source?: string) => {
  const normalized = source?.toLowerCase() ?? "website";
  if (normalized.includes("mail")) return "email";
  if (normalized.includes("linkedin")) return "linkedin";
  if (normalized.includes("clutch") || normalized.includes("fourth")) return "clutch";
  return "website";
};

function journeyEdges(lead: Lead | null): Array<[string, string]> {
  if (!lead) return [];
  const result: Array<[string, string]> = [[sourceStage(lead.source), "normalize"]];
  if (lead.analysis) result.push(["normalize", "context"], ["context", "icp"]);
  if (lead.qualification) result.push(["icp", "qualification"]);
  if (lead.routing) result.push(["qualification", "routing"], ["routing", "actions"], ["routing", "rep"]);
  if (lead.routing?.notifications.length) result.push(["routing", "slack"]);
  if (lead.crm_sync) result.push(["actions", lead.crm_sync.provider === "hubspot" ? "hubspot" : "salesforce"]);
  return result;
}

function fullActiveStages(lead: Lead | null) {
  const ids = new Set<string>();
  for (const [from, to] of journeyEdges(lead)) {
    ids.add(from);
    ids.add(to);
  }
  return ids;
}

function useLifecycleSequence(lead: Lead | null, reduced: boolean) {
  const edges = useMemo(() => journeyEdges(lead), [lead]);
  const edgeKey = edges.flat().join(":");
  const [revealed, setRevealed] = useState(() => fullActiveStages(lead));
  const [transition, setTransition] = useState<string | null>(null);

  useEffect(() => {
    const complete = fullActiveStages(lead);
    if (reduced || edges.length === 0) {
      setRevealed(complete);
      setTransition(null);
      return;
    }

    const first = edges[0][0];
    let cursor = 0;
    let settleTimer = 0;
    setRevealed(new Set([first]));
    setTransition(null);
    const timer = window.setInterval(() => {
      const edge = edges[cursor];
      if (!edge) {
        window.clearInterval(timer);
        settleTimer = window.setTimeout(() => setTransition(null), 520);
        return;
      }
      setTransition(`${edge[0]}-${edge[1]}`);
      setRevealed((current) => new Set([...current, edge[0], edge[1]]));
      cursor += 1;
    }, 520);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(settleTimer);
    };
  }, [lead?.id, edgeKey, reduced]);

  return { revealed, transition };
}

const NetworkScene = memo(function NetworkScene({
  lead,
  selected,
  motion,
  particles,
  revealed,
  transition,
  onSelect,
}: {
  lead: Lead | null;
  selected: Stage;
  motion: boolean;
  particles: boolean;
  revealed: Set<string>;
  transition: string | null;
  onSelect: (stage: Stage) => void;
}) {
  const active = useMemo(() => fullActiveStages(lead), [lead]);
  const standardGeometry = useMemo(() => new IcosahedronGeometry(.29, 2), []);
  const decisionGeometry = useMemo(() => new IcosahedronGeometry(.43, 2), []);

  useEffect(() => () => {
    standardGeometry.dispose();
    decisionGeometry.dispose();
  }, [decisionGeometry, standardGeometry]);

  return <>
    <ambientLight intensity={.7} />
    <pointLight position={[0, 5, 7]} color="#F48067" intensity={29} distance={18} />
    <pointLight position={[-5, -2, 4]} color="#24345d" intensity={18} distance={13} />
    <CameraRig selected={selected} motion={motion} />
    {particles && <SparseParticles motion={motion} />}
    {links.map(([from, to]) => {
      const start = stageById.get(from)!;
      const end = stageById.get(to)!;
      const illuminated = active.has(from) && active.has(to) && revealed.has(from) && revealed.has(to);
      const emphasized = selected.id === from || selected.id === to;
      const key = `${from}-${to}`;
      return <group key={key}>
        <Connection start={start.position} end={end.position} illuminated={illuminated} emphasized={emphasized} />
        {motion && transition === key && <Signal key={`${key}-${lead?.id ?? "none"}`} start={start.position} end={end.position} />}
      </group>;
    })}
    {stages.map((stage) => (
      <Node
        key={stage.id}
        stage={stage}
        active={active.has(stage.id) && revealed.has(stage.id)}
        selected={selected.id === stage.id}
        geometry={stage.id === "qualification" || stage.id === "routing" ? decisionGeometry : standardGeometry}
        onSelect={onSelect}
        motion={motion}
      />
    ))}
  </>;
});

function CameraRig({ selected, motion }: { selected: Stage; motion: boolean }) {
  const { camera } = useThree();
  const target = useMemo(() => new Vector3(selected.position[0] * .035, selected.position[1] * .025, 10.5), [selected]);
  useFrame(() => {
    if (!motion) return;
    camera.position.lerp(target, .035);
    camera.lookAt(0, 0, 0);
  });
  return null;
}

function Connection({ start, end, illuminated, emphasized }: {
  start: [number, number, number];
  end: [number, number, number];
  illuminated: boolean;
  emphasized: boolean;
}) {
  const geometry = useMemo(() => new BufferGeometry().setFromPoints([new Vector3(...start), new Vector3(...end)]), [start, end]);
  const material = useMemo(() => new LineBasicMaterial({
    color: emphasized ? "#F48067" : illuminated ? "#E13511" : "#263047",
    transparent: true,
    opacity: emphasized ? .95 : illuminated ? .7 : .34,
  }), [emphasized, illuminated]);
  const line = useMemo(() => new ThreeLine(geometry, material), [geometry, material]);
  useEffect(() => () => { geometry.dispose(); material.dispose(); }, [geometry, material]);
  return <primitive object={line} />;
}

function Node({ stage, active, selected, geometry, onSelect, motion }: {
  stage: Stage;
  active: boolean;
  selected: boolean;
  geometry: IcosahedronGeometry;
  onSelect: (stage: Stage) => void;
  motion: boolean;
}) {
  const mesh = useRef<Mesh>(null);
  const material = useRef<MeshStandardMaterial>(null);
  const [hovered, setHovered] = useState(false);
  useFrame(() => {
    if (!mesh.current || !material.current) return;
    const scale = hovered ? 1.16 : selected ? 1.1 : 1;
    mesh.current.scale.setScalar(motion ? MathUtils.lerp(mesh.current.scale.x, scale, .12) : scale);
    const intensity = hovered ? 1.7 : selected ? 1.45 : active ? .95 : .2;
    material.current.emissiveIntensity = motion ? MathUtils.lerp(material.current.emissiveIntensity, intensity, .12) : intensity;
  });
  return <mesh
    ref={mesh}
    geometry={geometry}
    position={stage.position}
    onClick={(event) => { event.stopPropagation(); onSelect(stage); }}
    onPointerOver={(event) => { event.stopPropagation(); setHovered(true); document.body.style.cursor = "pointer"; }}
    onPointerOut={() => { setHovered(false); document.body.style.cursor = ""; }}
  >
    <meshStandardMaterial ref={material} color={selected ? "#F48067" : active ? "#E13511" : "#142039"} emissive={selected || active ? "#8f200d" : "#071020"} emissiveIntensity={active ? .95 : .2} roughness={.34} metalness={.45} />
  </mesh>;
}

function Signal({ start, end }: { start: [number, number, number]; end: [number, number, number] }) {
  const mesh = useRef<Mesh>(null);
  const started = useRef<number | null>(null);
  const from = useMemo(() => new Vector3(...start), [start]);
  const to = useMemo(() => new Vector3(...end), [end]);
  useFrame(({ clock }) => {
    if (!mesh.current) return;
    if (started.current === null) started.current = clock.elapsedTime;
    const progress = Math.min(1, (clock.elapsedTime - started.current) / .48);
    mesh.current.position.lerpVectors(from, to, progress);
    mesh.current.visible = progress < 1;
  });
  return <mesh ref={mesh} position={start}><sphereGeometry args={[.065, 8, 8]} /><meshBasicMaterial color="#F48067" /></mesh>;
}

function SparseParticles({ motion }: { motion: boolean }) {
  const points = useRef<Points>(null);
  const positions = useMemo(() => {
    const values = new Float32Array(72);
    for (let index = 0; index < 24; index += 1) {
      values[index * 3] = Math.sin(index * 12.13) * 6.4;
      values[index * 3 + 1] = Math.cos(index * 7.17) * 3.2;
      values[index * 3 + 2] = -1.8 + (index % 7) * .3;
    }
    return values;
  }, []);
  useFrame((_, delta) => {
    if (points.current && motion) points.current.rotation.z += delta * .006;
  });
  return <points ref={points}>
    <bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]} /></bufferGeometry>
    <pointsMaterial color="#F48067" size={.028} transparent opacity={.3} sizeAttenuation />
  </points>;
}

function stageFacts(stage: Stage, lead: Lead | null): Array<[string, string]> {
  if (!lead) return [["State", "Waiting for workspace lead data"], ["Capability", stage.detail]];
  if (stage.id === "qualification") return [
    ["ICP score", `${lead.qualification?.component_scores.icp_fit ?? 0} / 40`],
    ["Intent score", `${lead.qualification?.component_scores.intent_urgency ?? 0} / 35`],
    ["Engagement score", `${lead.qualification?.component_scores.engagement_quality ?? 0} / 25`],
    ["Total / band", `${lead.qualification?.total_score ?? 0} · ${lead.qualification?.classification ?? "Pending"}`],
    ["Rationale", lead.qualification?.rationale.slice(0, 2).join(" ") || "Awaiting qualification evidence"],
  ];
  if (stage.id === "routing" || stage.id === "actions" || stage.id === "rep") return [
    ["Queue", lead.routing?.assigned_queue ?? "Pending"],
    ["Representative", lead.routing?.assigned_rep_name ?? "Unassigned"],
    ["Priority", lead.routing?.priority ?? "Not assigned"],
    ["Action plan", lead.routing?.action_plan.immediate_action ?? "Pending"],
    ["Routing basis", lead.routing?.routing_reason ?? "Awaiting route"],
  ];
  if (["salesforce", "hubspot"].includes(stage.id)) return [
    ["Provider", lead.crm_sync?.provider ?? "Not connected"],
    ["Sync state", lead.crm_sync?.status ?? "Not synced"],
    ["Provider record", lead.crm_sync?.provider_entity_ids.contact_id ?? "Not available"],
  ];
  if (stage.id === "slack") return [
    ["Notification", lead.routing?.notification_state ?? "Pending"],
    ["Priority", lead.routing?.priority ?? "Not assigned"],
    ["Attempts", String(lead.routing?.notifications[0]?.attempts ?? 0)],
  ];
  if (stage.id === "context" || stage.id === "icp") return [
    ["Intent", lead.analysis?.intent_level ?? "Unknown"],
    ["Confidence", `${Math.round((lead.analysis?.overall_confidence ?? 0) * 100)}%`],
    ["Need", lead.analysis?.summary.need ?? lead.product_or_service],
    ["Evidence", lead.analysis?.summary.buying_signal ?? "No explicit signal"],
  ];
  if (stage.id === "normalize") return [
    ["Lead", `${lead.full_name} · ${lead.company_name}`],
    ["Contract", lead.normalization_version],
    ["Received", new Date(lead.received_at).toLocaleString()],
  ];
  return [["Lead", `${lead.full_name} · ${lead.company_name}`], ["Source", lead.source], ["Signal", lead.message]];
}

function hasWebGl() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

export default function LeadIntelligenceNetwork({ pipeline, activity, lead }: { pipeline: PipelineAnalytics | null; activity: AgentActivity | null; lead: Lead | null }) {
  const [selected, setSelected] = useState(stageById.get("qualification")!);
  const [reduced, setReduced] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [compact, setCompact] = useState(false);
  const [visible, setVisible] = useState(true);
  const webgl = useMemo(hasWebGl, []);
  const { revealed, transition } = useLifecycleSequence(lead, reduced);
  const active = useMemo(() => fullActiveStages(lead), [lead]);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const phone = window.matchMedia("(max-width: 700px)");
    const controlled = window.matchMedia("(max-width: 1024px)");
    const sync = () => { setReduced(motion.matches); setMobile(phone.matches); setCompact(controlled.matches); };
    const visibility = () => setVisible(!document.hidden);
    sync();
    motion.addEventListener("change", sync);
    phone.addEventListener("change", sync);
    controlled.addEventListener("change", sync);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      motion.removeEventListener("change", sync);
      phone.removeEventListener("change", sync);
      controlled.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", visibility);
      document.body.style.cursor = "";
    };
  }, []);

  const fallback = mobile || !webgl;
  const received = pipeline?.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  const latestEvent = activity?.activity[0];
  return <section className="intelligence-network panel" aria-labelledby="network-title">
    <img className="network-twinkle" src="/asset/brand/twinkle-widecard.svg" alt="" aria-hidden="true" />
    <div className="section-heading network-heading">
      <div><span className="eyebrow">Live intelligence topology</span><h2 id="network-title">AI lead intelligence network</h2></div>
      <div className="network-live-state"><span className="flow-status"><i /> {received} signals in range</span><small>{latestEvent ? `${latestEvent.stage.replaceAll("_", " ")} · ${latestEvent.status}` : "Awaiting audited activity"}</small></div>
    </div>
    <div className="network-layout">
      <div className="network-stage">
        {!fallback ? <>
          <Canvas aria-label="Interactive lead intelligence network" camera={{ position: [0, 0, 10.5], fov: 49 }} dpr={compact ? 1 : [1, 1.5]} frameloop={visible ? "always" : "never"} gl={{ antialias: !compact, alpha: true, powerPreference: "high-performance" }}>
            <NetworkScene lead={lead} selected={selected} motion={visible && !reduced} particles={!compact && !reduced} revealed={revealed} transition={transition} onSelect={setSelected} />
          </Canvas>
          <div className="network-node-labels" aria-label="Select intelligence stage">
            {stages.map((stage) => <button
              type="button"
              className={`${active.has(stage.id) && revealed.has(stage.id) ? "active" : ""} ${selected.id === stage.id ? "selected" : ""}`}
              style={{ "--node-x": `${stage.screen[0]}%`, "--node-y": `${stage.screen[1]}%` } as CSSProperties}
              onClick={() => setSelected(stage)}
              onFocus={() => setSelected(stage)}
              key={stage.id}
            >{stage.shortLabel}</button>)}
          </div>
        </> : <div className="network-fallback" role="group" aria-label="Lead intelligence network lightweight view">
          {stages.map((stage) => <button type="button" className={`${active.has(stage.id) ? "active" : ""} ${selected.id === stage.id ? "selected" : ""}`} onClick={() => setSelected(stage)} key={stage.id}><small>{stage.lane}</small>{stage.label}</button>)}
        </div>}
        <div className="network-lanes" aria-hidden="true"><span>Inbound</span><span>AI processing</span><span>Decision</span><span>Destinations</span></div>
      </div>
      <aside className="network-inspector" aria-live="polite">
        <div className="network-inspector-state"><i className={active.has(selected.id) ? "active" : ""} /><span>{active.has(selected.id) ? "Stage complete" : "No current event"}</span></div>
        <div className="network-inspector-content" key={`${selected.id}-${lead?.id ?? "none"}`}>
          <span className="eyebrow">{selected.lane}</span><h3>{selected.label}</h3><p>{selected.detail}</p>
          <dl>{stageFacts(selected, lead).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        </div>
      </aside>
    </div>
    <div className="network-accessible" aria-label="Inspect network stage">{stages.map((stage) => <button className={selected.id === stage.id ? "active" : ""} onClick={() => setSelected(stage)} onFocus={() => setSelected(stage)} key={stage.id}>{stage.label}</button>)}</div>
  </section>;
}
