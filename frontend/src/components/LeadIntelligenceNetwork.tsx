import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Component, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ErrorInfo, ReactNode } from "react";
import type { CSSProperties } from "react";
import { BufferGeometry, IcosahedronGeometry, Line as ThreeLine, LineBasicMaterial, MathUtils, Vector3 } from "three";
import type { Mesh, MeshStandardMaterial } from "three";

import type { AgentActivity, Lead, PipelineAnalytics } from "../types";
import { fullActiveStages, journeyEdges, latestActivityLabel, stageById, stages } from "./networkData";
import type { Stage } from "./networkData";
import { NetworkDomFallback, NetworkStageInspector } from "./NetworkDomFallback";

const links: Array<[string, string]> = [
  ["website", "normalize"], ["email", "normalize"], ["linkedin", "normalize"], ["clutch", "normalize"],
  ["normalize", "context"], ["context", "icp"], ["icp", "qualification"], ["qualification", "routing"],
  ["routing", "actions"], ["actions", "salesforce"], ["actions", "hubspot"], ["routing", "slack"], ["routing", "rep"],
];

function initialSequence(lead: Lead | null, reduced: boolean, playing: boolean, edges: Array<[string, string]>) {
  return reduced || !playing || edges.length === 0 ? fullActiveStages(lead) : new Set([edges[0][0]]);
}

function useLifecycleSequence(lead: Lead | null, reduced: boolean, playing: boolean) {
  const edges = useMemo(() => journeyEdges(lead), [lead]);
  const edgeKey = edges.flat().join(":");
  const sequenceKey = `${lead?.id ?? "none"}:${edgeKey}:${reduced}:${playing}`;
  const initial = () => initialSequence(lead, reduced, playing, edges);
  const [sequence, setSequence] = useState(() => ({ key: sequenceKey, revealed: initial(), transition: null as string | null }));
  const current = sequence.key === sequenceKey ? sequence : { key: sequenceKey, revealed: initial(), transition: null };

  useLayoutEffect(() => {
    if (reduced || !playing || edges.length === 0) {
      setSequence({ key: sequenceKey, revealed: fullActiveStages(lead), transition: null });
      return;
    }
    let cursor = 0;
    let settleTimer = 0;
    const advance = () => {
      const edge = edges[cursor];
      if (!edge) return;
      setSequence((state) => state.key === sequenceKey
        ? { key: sequenceKey, revealed: new Set([...state.revealed, edge[0], edge[1]]), transition: `${edge[0]}-${edge[1]}` }
        : state);
      cursor += 1;
    };
    setSequence({ key: sequenceKey, revealed: new Set([edges[0][0]]), transition: null });
    advance();
    const timer = window.setInterval(() => {
      if (cursor >= edges.length) {
        window.clearInterval(timer);
        settleTimer = window.setTimeout(() => setSequence((state) => state.key === sequenceKey ? { ...state, transition: null } : state), 520);
        return;
      }
      advance();
    }, 520);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(settleTimer);
    };
  }, [sequenceKey]);

  return { revealed: current.revealed, transition: current.transition };
}

const NetworkScene = memo(function NetworkScene({ lead, selected, motion, particles, revealed, transition, onSelect }: {
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
  useEffect(() => () => { standardGeometry.dispose(); decisionGeometry.dispose(); }, [decisionGeometry, standardGeometry]);

  return <>
    <ambientLight intensity={.7} />
    <pointLight position={[0, 5, 7]} color="#F48067" intensity={29} distance={18} />
    <pointLight position={[-5, -2, 4]} color="#24345d" intensity={18} distance={13} />
    <CameraRig selected={selected} motion={motion} />
    {particles && <SparseParticles />}
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
    {stages.map((stage) => <Node
      key={stage.id}
      stage={stage}
      active={active.has(stage.id) && revealed.has(stage.id)}
      selected={selected.id === stage.id}
      geometry={stage.id === "qualification" || stage.id === "routing" ? decisionGeometry : standardGeometry}
      onSelect={onSelect}
      motion={motion}
    />)}
  </>;
});

function CameraRig({ selected, motion }: { selected: Stage; motion: boolean }) {
  const { camera, invalidate } = useThree();
  const target = useMemo(() => new Vector3(selected.position[0] * .035, selected.position[1] * .025, 10.5), [selected]);
  useFrame(() => {
    if (!motion) return;
    camera.position.lerp(target, .035);
    camera.lookAt(0, 0, 0);
    if (camera.position.distanceToSquared(target) > .0004) invalidate();
  });
  return null;
}

function Connection({ start, end, illuminated, emphasized }: { start: [number, number, number]; end: [number, number, number]; illuminated: boolean; emphasized: boolean }) {
  const geometry = useMemo(() => new BufferGeometry().setFromPoints([new Vector3(...start), new Vector3(...end)]), [start, end]);
  const material = useMemo(() => new LineBasicMaterial({ color: emphasized ? "#F48067" : illuminated ? "#E13511" : "#263047", transparent: true, opacity: emphasized ? .95 : illuminated ? .7 : .34 }), [emphasized, illuminated]);
  const line = useMemo(() => new ThreeLine(geometry, material), [geometry, material]);
  useEffect(() => () => { geometry.dispose(); material.dispose(); }, [geometry, material]);
  return <primitive object={line} />;
}

function Node({ stage, active, selected, geometry, onSelect, motion }: { stage: Stage; active: boolean; selected: boolean; geometry: IcosahedronGeometry; onSelect: (stage: Stage) => void; motion: boolean }) {
  const invalidate = useThree((state) => state.invalidate);
  const mesh = useRef<Mesh>(null);
  const material = useRef<MeshStandardMaterial>(null);
  const [hovered, setHovered] = useState(false);
  useFrame(() => {
    if (!mesh.current || !material.current) return;
    const scale = hovered ? 1.16 : selected ? 1.1 : 1;
    const nextScale = motion ? MathUtils.lerp(mesh.current.scale.x, scale, .18) : scale;
    mesh.current.scale.setScalar(nextScale);
    const intensity = hovered ? 1.7 : selected ? 1.45 : active ? .95 : .2;
    const nextIntensity = motion ? MathUtils.lerp(material.current.emissiveIntensity, intensity, .18) : intensity;
    material.current.emissiveIntensity = nextIntensity;
    if (Math.abs(nextScale - scale) > .002 || Math.abs(nextIntensity - intensity) > .01) invalidate();
  });
  return <mesh ref={mesh} geometry={geometry} position={stage.position}
    onClick={(event) => { event.stopPropagation(); onSelect(stage); }}
    onPointerOver={(event) => { event.stopPropagation(); setHovered(true); document.body.style.cursor = "pointer"; }}
    onPointerOut={() => { setHovered(false); document.body.style.cursor = ""; }}>
    <meshStandardMaterial ref={material} color={selected ? "#F48067" : active ? "#E13511" : "#142039"} emissive={selected || active ? "#8f200d" : "#071020"} emissiveIntensity={active ? .95 : .2} roughness={.34} metalness={.45} />
  </mesh>;
}

function Signal({ start, end }: { start: [number, number, number]; end: [number, number, number] }) {
  const invalidate = useThree((state) => state.invalidate);
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
    if (progress < 1) invalidate();
  });
  return <mesh ref={mesh} position={start}><sphereGeometry args={[.065, 8, 8]} /><meshBasicMaterial color="#F48067" /></mesh>;
}

function SparseParticles() {
  const positions = useMemo(() => {
    const values = new Float32Array(72);
    for (let index = 0; index < 24; index += 1) {
      values[index * 3] = Math.sin(index * 12.13) * 6.4;
      values[index * 3 + 1] = Math.cos(index * 7.17) * 3.2;
      values[index * 3 + 2] = -1.8 + (index % 7) * .3;
    }
    return values;
  }, []);
  return <points>
    <bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]} /></bufferGeometry>
    <pointsMaterial color="#F48067" size={.028} transparent opacity={.3} sizeAttenuation />
  </points>;
}

function hasWebGl() {
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2") || canvas.getContext("webgl");
    const supported = context !== null;
    context?.getExtension("WEBGL_lose_context")?.loseContext();
    return supported;
  } catch {
    return false;
  }
}

type NetworkProps = { pipeline: PipelineAnalytics; activity: AgentActivity; lead: Lead | null };

class NetworkFailureBoundary extends Component<{ children: ReactNode; fallback: ReactNode; onFailure: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { this.props.onFailure(); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function ContextLossMonitor({ onLost }: { onLost: () => void }) {
  const { gl } = useThree();
  useEffect(() => {
    const canvas = gl.domElement;
    const handleContextLost = (event: Event) => { event.preventDefault(); onLost(); };
    canvas.addEventListener("webglcontextlost", handleContextLost);
    return () => canvas.removeEventListener("webglcontextlost", handleContextLost);
  }, [gl, onLost]);
  return null;
}

export default function LeadIntelligenceNetwork(props: NetworkProps) {
  const [failed, setFailed] = useState(false);
  const fail = useCallback(() => setFailed(true), []);
  if (failed) return <NetworkDomFallback {...props} />;
  return <NetworkFailureBoundary fallback={<NetworkDomFallback {...props} />} onFailure={fail}>
    <LeadIntelligenceNetworkCanvas {...props} onContextLost={fail} />
  </NetworkFailureBoundary>;
}

function LeadIntelligenceNetworkCanvas({ pipeline, activity, lead, onContextLost }: NetworkProps & { onContextLost: () => void }) {
  const [selected, setSelected] = useState(stageById.get("qualification")!);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 1024px)").matches);
  const [tabVisible, setTabVisible] = useState(() => !document.hidden);
  const [inViewport, setInViewport] = useState(false);
  const layout = useRef<HTMLDivElement>(null);
  const webgl = useMemo(hasWebGl, []);
  const { revealed, transition } = useLifecycleSequence(lead, reduced, tabVisible && inViewport);
  const active = useMemo(() => fullActiveStages(lead), [lead]);
  const received = pipeline.stages.find((stage) => stage.stage === "received")?.count ?? 0;
  const sceneVisible = tabVisible && inViewport;
  const fallback = !webgl;

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const controlled = window.matchMedia("(max-width: 1024px)");
    const sync = () => { setReduced(motion.matches); setCompact(controlled.matches); };
    const visibility = () => setTabVisible(!document.hidden);
    const observer = new IntersectionObserver(([entry]) => setInViewport(entry.isIntersecting), { threshold: 0.05 });
    if (layout.current) observer.observe(layout.current);
    motion.addEventListener("change", sync);
    controlled.addEventListener("change", sync);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      motion.removeEventListener("change", sync);
      controlled.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", visibility);
      observer.disconnect();
      document.body.style.cursor = "";
    };
  }, []);

  return <section className="intelligence-network panel" aria-labelledby="network-title">
    <img className="network-twinkle" src="/asset/brand/twinkle-widecard.svg" alt="" aria-hidden="true" />
    <div className="section-heading network-heading">
      <div><span className="eyebrow">Live intelligence topology</span><h2 id="network-title">AI lead intelligence network</h2></div>
      <div className="network-live-state"><span className="flow-status"><i /> {received} signals in range</span><small>{latestActivityLabel(activity)}</small></div>
    </div>
    <div className="network-layout" ref={layout}>
      <div className="network-stage">
        {!fallback ? <>
          <div className="network-canvas-frame">
            <NetworkFailureBoundary fallback={<NetworkStageButtons active={active} selected={selected} onSelect={setSelected} />} onFailure={onContextLost}>
              <Canvas fallback={<NetworkStageButtons active={active} selected={selected} onSelect={setSelected} />} aria-label="Interactive lead intelligence network" camera={{ position: [0, 0, 10.5], fov: 49 }} dpr={compact ? 1 : [1, 1.5]} frameloop="demand" gl={{ antialias: !compact, alpha: true, powerPreference: "high-performance" }}>
                <ContextLossMonitor onLost={onContextLost} />
                <NetworkScene lead={lead} selected={selected} motion={sceneVisible && !reduced} particles={sceneVisible && !compact && !reduced} revealed={revealed} transition={transition} onSelect={setSelected} />
              </Canvas>
            </NetworkFailureBoundary>
          </div>
          <div className="network-node-labels" aria-label="Select intelligence stage">
            {stages.map((stage) => <button type="button" className={`${active.has(stage.id) && revealed.has(stage.id) ? "active" : ""} ${selected.id === stage.id ? "selected" : ""}`} style={{ "--node-x": `${stage.screen[0]}%`, "--node-y": `${stage.screen[1]}%` } as CSSProperties} onClick={() => setSelected(stage)} onFocus={() => setSelected(stage)} key={stage.id}>{stage.shortLabel}</button>)}
          </div>
        </> : <NetworkStageButtons active={active} selected={selected} onSelect={setSelected} />}
        <div className="network-lanes" aria-hidden="true"><span>Inbound</span><span>AI processing</span><span>Decision</span><span>Destinations</span></div>
      </div>
      <NetworkStageInspector selected={selected} lead={lead} active={active.has(selected.id)} />
    </div>
    {!fallback && <div className="network-accessible" aria-label="Inspect network stage">{stages.map((stage) => <button className={selected.id === stage.id ? "active" : ""} onClick={() => setSelected(stage)} onFocus={() => setSelected(stage)} key={stage.id}>{stage.label}</button>)}</div>}
  </section>;
}

function NetworkStageButtons({ active, selected, onSelect }: { active: Set<string>; selected: Stage; onSelect: (stage: Stage) => void }) {
  return <div className="network-fallback" role="group" aria-label="Lead intelligence network lightweight view">
    {stages.map((stage) => <button type="button" className={`${active.has(stage.id) ? "active" : ""} ${selected.id === stage.id ? "selected" : ""}`} onClick={() => onSelect(stage)} onFocus={() => onSelect(stage)} key={stage.id}><small>{stage.lane}</small>{stage.label}</button>)}
  </div>;
}
