import { useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import { getGraph, inferCareerPaths } from "../api/client";
import {
  CAREER_PATH_COLOR,
  categoryColor,
  GRAPH_EDGE,
  GRAPH_EDGE_ACTIVE,
  GRAPH_NODE_SELECTED,
  SURFACE_PAPER,
} from "../categories";
import { DEGRADED_COPY, ErrorBanner } from "./cardParts";
import ColdStartNotice from "./ColdStartNotice";
import LoadDemoButton from "./LoadDemoButton";
import NodeDetailPanel from "./NodeDetailPanel";

// View 3 — the knowledge graph (plan.md §6). A d3-force simulation rendered as
// SVG (not react-force-graph) so every mark obeys the validated category palette
// and label rules the rest of the app follows. d3 owns the physics; React owns
// the DOM — the simulation mutates node x/y in a ref and a tick counter triggers
// re-render, rather than React fighting d3 over the same nodes.
//
// Colour is not chosen here: documents take their category hue, skills the aqua
// "Skills" hue, and career paths the reserved achromatic slate — all from
// categories.js. Career Path additionally reads as a *different kind of thing*
// through composite encoding (larger node, right-side placement, an always-on
// title + match-% label), because the palette validator FAILs every 7th
// categorical hue (see categories.js). Do not add a hue here.

const HEIGHT = 560;

// Below this the surface is a phone, where a 560px-tall box is most of the
// viewport before the legend and the hint line are even reached.
const NARROW_WIDTH = 640;
const HEIGHT_NARROW = 420;

// Exported so the breakpoint is assertable without rendering a d3 simulation.
export function heightFor(width) {
  return width > 0 && width < NARROW_WIDTH ? HEIGHT_NARROW : HEIGHT;
}

// Node radii by kind. Career Path is deliberately the largest — half of its
// composite encoding (categories.js) — and skills the smallest so the document
// hubs read as the primary objects.
const RADIUS = { document: 10, skill: 7, career_path: 18 };
export function radiusOf(node) {
  return RADIUS[node.type] ?? 9;
}

export function colorOf(node) {
  if (node.type === "career_path") return CAREER_PATH_COLOR;
  if (node.type === "skill") return categoryColor("Skills");
  return categoryColor(node.category);
}

// Similarity edges (Layer B) are the "non-obvious connection" link and read as
// dashed; the entity/career chain edges are solid.
export function isDashed(relation) {
  return relation === "similar_to";
}

export function edgeId(link) {
  const s = typeof link.source === "object" ? link.source.id : link.source;
  const t = typeof link.target === "object" ? link.target.id : link.target;
  return `${s}->${t}`;
}
function endpointId(end) {
  return typeof end === "object" ? end.id : end;
}

// Precompute everything derived from the raw {nodes, edges}: the mutable node/
// link arrays d3 will own, a neighbour lookup for the highlight interaction, and
// a per-node connection list for the detail panel.
export function buildModel(data) {
  const nodes = data.nodes.map((n) => ({ ...n }));
  const links = data.edges.map((e) => ({ ...e }));
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const neighbors = new Map(nodes.map((n) => [n.id, new Set([n.id])]));
  const connections = new Map(nodes.map((n) => [n.id, []]));
  for (const e of data.edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue;
    neighbors.get(e.source).add(e.target);
    neighbors.get(e.target).add(e.source);
    connections.get(e.source).push({ node: byId.get(e.target), relation: e.relation_type });
    connections.get(e.target).push({ node: byId.get(e.source), relation: e.relation_type });
  }
  return { nodes, links, byId, neighbors, connections };
}

// A small ring spinner, reused by the initial load and the inference button.
function Spinner({ className = "" }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
    />
  );
}

// Shown while the graph is first fetched: pulsing placeholder nodes so the panel
// reads as "drawing your graph", not a frozen blank.
function GraphSkeleton() {
  const dots = [
    [90, 120, 10], [180, 210, 10], [150, 90, 7], [260, 150, 7],
    [230, 260, 10], [340, 110, 7], [360, 230, 18], [70, 260, 7],
  ];
  return (
    <div className="rounded-xl border border-sand-200 bg-paper">
      <svg width="100%" height={HEIGHT} viewBox="0 0 440 320" className="block">
        <g className="animate-pulse">
          {dots.map(([x, y], i) =>
            dots.slice(i + 1, i + 2).map(([x2, y2]) => (
              <line key={`l${i}`} x1={x} y1={y} x2={x2} y2={y2} stroke={GRAPH_EDGE} strokeWidth="1.5" />
            ))
          )}
          {dots.map(([x, y, r], i) => (
            <circle key={i} cx={x} cy={y} r={r} fill={GRAPH_EDGE} />
          ))}
        </g>
      </svg>
    </div>
  );
}

export default function KnowledgeGraph() {
  const [data, setData] = useState(null); // null = loading
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [hoverId, setHoverId] = useState(null);

  // Career-path inference (the Gemini button).
  const [inferring, setInferring] = useState(false);
  const [degraded, setDegraded] = useState(null); // {reason, retryable}

  // A callback ref, not useRef: the graph surface does not exist during the
  // loading state (this component early-returns a skeleton), so an effect that
  // measured `ref.current` on mount measured null and — with [] deps — never ran
  // again. `width` stayed at its default and the SVG was rendered wider than the
  // phone it was on, clipped by the container's overflow-hidden. State makes the
  // element's arrival re-trigger the measurement.
  const [container, setContainer] = useState(null);
  const svgRef = useRef(null);
  const simRef = useRef(null);
  const nodesRef = useRef([]);
  const linksRef = useRef([]);
  const draggingRef = useRef(null); // {id, moved}
  const [width, setWidth] = useState(720);
  const [, tick] = useReducer((n) => n + 1, 0);

  // Only the newest load may write: this runs on mount and again after career
  // inference, so two can overlap. It also clears `error` on success — a
  // transient failure used to leave the message set for the rest of the
  // session, waiting to be shown the next time the graph happened to be empty.
  const requestId = useRef(0);

  async function load() {
    const id = ++requestId.current;
    try {
      const next = await getGraph();
      if (id !== requestId.current) return;
      setError("");
      setData(next);
    } catch (e) {
      if (id !== requestId.current) return;
      setError(e.message);
      setData({ nodes: [], edges: [] });
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Measure the container so the simulation is centred in the real width.
  //
  // A ResizeObserver rather than only a window resize listener: the surface also
  // changes width without the window doing so — the sidebar, a scrollbar
  // appearing, or simply this element mounting after the fetch resolves. The
  // window listener stays as a fallback for environments without the observer.
  useLayoutEffect(() => {
    if (!container) return undefined;
    function measure() {
      // Guard 0: a hidden container reports zero, which would collapse the
      // clamp below to a single point and stack every node on top of itself.
      const next = container.clientWidth;
      if (next > 0) setWidth(next);
    }
    measure();

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(container);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [container]);

  const height = heightFor(width);

  const model = useMemo(() => (data ? buildModel(data) : null), [data]);

  // Build (and tear down) the force simulation whenever the graph data or the
  // measured width changes. Career-path nodes are pulled to the right by a
  // dedicated forceX — the placement half of their composite encoding.
  useEffect(() => {
    if (!model || model.nodes.length === 0) return;
    nodesRef.current = model.nodes;
    linksRef.current = model.links;

    // Keep every node inside the SVG viewport. A force simulation has no walls,
    // so charge repulsion pushes peripheral nodes off-screen; clamping each
    // node's centre after every tick is the standard bounding box. Career-path
    // labels are wide ("AI/ML Engineer · 87%") and centre-anchored, so they get
    // extra horizontal room so the text is not clipped at the edge.
    const PAD = 26;
    function clampToBounds() {
      for (const n of model.nodes) {
        const rx = radiusOf(n) + (n.type === "career_path" ? 72 : PAD);
        const ry = radiusOf(n) + PAD;
        const maxX = Math.max(rx, width - rx);
        const maxY = Math.max(ry, height - ry);
        n.x = Math.max(rx, Math.min(maxX, n.x ?? width / 2));
        n.y = Math.max(ry, Math.min(maxY, n.y ?? height / 2));
      }
    }

    const sim = forceSimulation(model.nodes)
      .force(
        "link",
        forceLink(model.links)
          .id((d) => d.id)
          .distance((l) => (l.relation_type === "leads_to" ? 120 : 80))
          .strength(0.5)
      )
      .force("charge", forceManyBody().strength(-300))
      .force("center", forceCenter(width / 2, height / 2))
      .force("collide", forceCollide((d) => radiusOf(d) + 14))
      .force(
        "x",
        forceX((d) => (d.type === "career_path" ? width * 0.8 : width / 2)).strength(
          (d) => (d.type === "career_path" ? 0.3 : 0.02)
        )
      )
      .force("y", forceY(height / 2).strength(0.04))
      .on("tick", () => {
        clampToBounds();
        tick();
      });

    simRef.current = sim;
    return () => sim.stop();
  }, [model, width, height]);

  // Map a pointer event to simulation coordinates (the SVG viewBox is 0 0 w h,
  // and its pixel size matches, so the ratio handles any layout scaling).
  function toSim(evt) {
    const rect = svgRef.current.getBoundingClientRect();
    return {
      x: ((evt.clientX - rect.left) / rect.width) * width,
      y: ((evt.clientY - rect.top) / rect.height) * height,
    };
  }

  function onNodePointerDown(evt, node) {
    evt.stopPropagation();
    evt.currentTarget.setPointerCapture?.(evt.pointerId);
    draggingRef.current = { id: node.id, moved: false };
    simRef.current?.alphaTarget(0.15).restart();
    const p = toSim(evt);
    node.fx = p.x;
    node.fy = p.y;
  }

  function onNodePointerMove(evt, node) {
    if (draggingRef.current?.id !== node.id) return;
    draggingRef.current.moved = true;
    const p = toSim(evt);
    node.fx = p.x;
    node.fy = p.y;
  }

  function onNodePointerUp(evt, node) {
    if (draggingRef.current?.id !== node.id) return;
    const wasDrag = draggingRef.current.moved;
    draggingRef.current = null;
    simRef.current?.alphaTarget(0);
    node.fx = null;
    node.fy = null;
    // A real drag should not also be read as a click-to-select.
    if (!wasDrag) setSelectedId((cur) => (cur === node.id ? null : node.id));
  }

  // What a screen reader hears on a node: the same three facts the tooltip
  // shows, since a sighted user gets them on hover and a keyboard user gets
  // nothing otherwise.
  function nodeLabel(node) {
    if (node.type === "career_path") {
      const pct =
        typeof node.match_score === "number"
          ? `, ${Math.round(node.match_score * 100)} percent match`
          : "";
      return `Career path: ${node.label}${pct}`;
    }
    if (node.type === "skill") return `Skill: ${node.label}`;
    const category = node.category ? `, ${node.category}` : "";
    const date =
      node.date_source === "extracted" && node.effective_date
        ? `, ${node.effective_date}`
        : "";
    return `Document: ${node.label}${category}${date}`;
  }

  function onNodeKeyDown(event, node) {
    if (event.key !== "Enter" && event.key !== " ") return;
    // Space scrolls the page by default, which would move the graph out from
    // under the node the user was about to open.
    event.preventDefault();
    event.stopPropagation();
    setSelectedId((cur) => (cur === node.id ? null : node.id));
  }

  const selectedNode = selectedId ? model?.byId.get(selectedId) : null;
  const highlightSet = selectedId ? model?.neighbors.get(selectedId) : null;

  // Labels are shown for every node on a small graph and always for career
  // paths; on a busier graph the non-career labels appear only for the hovered
  // node or the highlighted chain, so the picture stays legible.
  const showAllLabels = (model?.nodes.length ?? 0) <= 22;
  function labelVisible(node) {
    if (node.type === "career_path") return true;
    if (node.id === hoverId) return true;
    if (highlightSet) return highlightSet.has(node.id);
    return showAllLabels;
  }

  function nodeOpacity(node) {
    if (!highlightSet) return 1;
    return highlightSet.has(node.id) ? 1 : 0.2;
  }
  function edgeState(link) {
    if (!highlightSet) return "normal";
    const s = endpointId(link.source);
    const t = endpointId(link.target);
    return s === selectedId || t === selectedId ? "active" : "dim";
  }

  async function runInference(isRetry = false) {
    setInferring(true);
    if (!isRetry) setDegraded(null);
    try {
      const res = await inferCareerPaths();
      if (res.degraded_reason) {
        setDegraded({ reason: res.degraded_reason, retryable: res.retryable });
      } else {
        setDegraded(null);
      }
      // Refetch so new career-path nodes + leads_to edges enter the graph.
      await load();
      setSelectedId(null);
    } catch (e) {
      setError(e.message);
    }
    setInferring(false);
  }

  // ---- Render states ---------------------------------------------------------

  if (data === null) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm text-sand-500">
          <Spinner className="h-4 w-4 text-sand-500" />
          Drawing your knowledge graph…
        </div>
        <ColdStartNotice />
        <GraphSkeleton />
      </div>
    );
  }
  if (error && data.nodes.length === 0) {
    return (
      <ErrorBanner message={error} />
    );
  }
  if (data.nodes.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-sand-300 bg-paper px-6 py-16 text-center">
        <p className="text-sm font-medium text-sand-600">Your graph is empty</p>
        <p className="mt-1 text-xs text-sand-500">
          Head to <span className="font-medium">Upload</span> to add documents —
          the graph draws their skills, similarities, and career paths as they
          connect.
        </p>
        <div className="mt-6 flex flex-col items-center gap-2">
          <LoadDemoButton onLoaded={load} />
          <p className="text-xs text-sand-500">
            or load a sample profile to see the graph light up
          </p>
        </div>
      </div>
    );
  }

  const hasCareerPaths = data.nodes.some((n) => n.type === "career_path");
  const presentCategories = [
    ...new Set(
      data.nodes.filter((n) => n.type === "document").map((n) => n.category).filter(Boolean)
    ),
  ];
  const hoverNode = hoverId ? model.byId.get(hoverId) : null;

  return (
    <div className="space-y-4">
      {/* Controls: the Gemini career-path inference button + its degraded notice. */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => runInference(false)}
          disabled={inferring}
          className="inline-flex items-center gap-2 rounded-lg bg-espresso-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-espresso-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {inferring && <Spinner className="h-4 w-4 text-white" />}
          {inferring
            ? "Inferring…"
            : hasCareerPaths
            ? "Re-infer career paths"
            : "Infer career paths"}
        </button>
        <span className="text-xs text-sand-500">
          Reads your whole profile with AI to suggest trajectories
        </span>
      </div>

      {degraded && (
        <div
          className={`flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2 text-sm ${
            degraded.retryable
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-sand-200 bg-sand-200 text-sand-600"
          }`}
        >
          <span aria-hidden="true">{degraded.retryable ? "⚠" : "○"}</span>
          <span>
            {DEGRADED_COPY[degraded.reason] || "Career-path inference was unavailable."}{" "}
            {degraded.retryable
              ? "This usually clears — no paths were changed."
              : "No paths were changed."}
          </span>
          {degraded.retryable && (
            <button
              onClick={() => runInference(true)}
              disabled={inferring}
              className="rounded border border-amber-300 px-2 py-0.5 text-xs font-medium text-amber-800 transition hover:bg-amber-100 disabled:opacity-50"
            >
              {inferring ? "Retrying…" : "Try again"}
            </button>
          )}
        </div>
      )}

      {/* The graph surface. Relative so the detail panel and hover tooltip can
          overlay it without reflowing the simulation. */}
      <div
        ref={setContainer}
        className="relative overflow-hidden rounded-xl border border-sand-200 bg-paper"
      >
        {/* width="100%" rather than the measured pixel count: with the viewBox
            carrying the coordinate space, the surface can never be drawn wider
            than its container. Even if a measurement were missed, the graph
            scales to fit instead of being clipped — which is the failure this
            had on phones, where the whole simulation ran in a field wider than
            the screen. `toSim` reads the real rendered rect, so dragging stays
            accurate under any scaling. */}
        <svg
          ref={svgRef}
          width="100%"
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
          className="block touch-none select-none"
          role="group"
          aria-label={`Knowledge graph: ${nodesRef.current.length} nodes, ${linksRef.current.length} connections. Tab through the nodes; Enter opens one.`}
          onClick={() => setSelectedId(null)}
        >
          {/* Edges first, so nodes sit on top. */}
          <g>
            {linksRef.current.map((link) => {
              const s = link.source;
              const t = link.target;
              if (typeof s !== "object" || typeof t !== "object") return null;
              const state = edgeState(link);
              return (
                <line
                  key={edgeId(link)}
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  stroke={state === "active" ? GRAPH_EDGE_ACTIVE : GRAPH_EDGE}
                  strokeWidth={state === "active" ? 2 : 1.2}
                  strokeOpacity={state === "dim" ? 0.08 : state === "active" ? 0.9 : 0.5}
                  strokeDasharray={isDashed(link.relation_type) ? "4 3" : undefined}
                />
              );
            })}
          </g>

          {/* Nodes + labels. */}
          <g>
            {nodesRef.current.map((node) => {
              const r = radiusOf(node);
              const opacity = nodeOpacity(node);
              const isSel = node.id === selectedId;
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}
                  style={{ cursor: "pointer", opacity }}
                  /* Reachable without a pointer. The graph is the view this app
                     is judged on and it was pointer-only: no tab stop, no way to
                     open a node, nothing announced. A bare <g> is not focusable
                     and has no role, so both are stated here, and the tooltip
                     follows focus as well as hover (WCAG 1.4.13 — information on
                     hover must also be available to keyboard users). */
                  tabIndex={0}
                  role="button"
                  aria-label={nodeLabel(node)}
                  aria-pressed={isSel}
                  onKeyDown={(e) => onNodeKeyDown(e, node)}
                  onFocus={() => setHoverId(node.id)}
                  onBlur={() => setHoverId((c) => (c === node.id ? null : c))}
                  onPointerDown={(e) => onNodePointerDown(e, node)}
                  onPointerMove={(e) => onNodePointerMove(e, node)}
                  onPointerUp={(e) => onNodePointerUp(e, node)}
                  onPointerEnter={() => setHoverId(node.id)}
                  onPointerLeave={() => setHoverId((c) => (c === node.id ? null : c))}
                  onClick={(e) => e.stopPropagation()}
                >
                  <circle
                    r={r}
                    fill={colorOf(node)}
                    stroke={isSel ? GRAPH_NODE_SELECTED : SURFACE_PAPER}
                    strokeWidth={isSel ? 2.5 : 1.5}
                  />
                  {labelVisible(node) && (
                    <text
                      x={0}
                      y={r + 12}
                      textAnchor="middle"
                      className={`pointer-events-none ${
                        node.type === "career_path"
                          ? "fill-sand-900 font-semibold"
                          : "fill-sand-600"
                      }`}
                      style={{ fontSize: node.type === "career_path" ? 12 : 10 }}
                    >
                      {node.type === "career_path" && typeof node.match_score === "number"
                        ? `${node.label} · ${Math.round(node.match_score * 100)}%`
                        : node.label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        {/* Hover tooltip (plan.md §6: title, date, category). Positioned in the
            same coordinate space as the SVG since its pixel size matches. */}
        {hoverNode && hoverNode.id !== selectedId && (
          <div
            className="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-full rounded-md bg-sand-900 px-2 py-1 text-xs text-white shadow-lg"
            style={{ left: hoverNode.x, top: (hoverNode.y ?? 0) - radiusOf(hoverNode) - 6 }}
          >
            <span className="font-medium">{hoverNode.label}</span>
            {hoverNode.type === "document" && hoverNode.category && (
              <span className="ml-1 text-sand-400">· {hoverNode.category}</span>
            )}
            {hoverNode.type === "document" &&
              hoverNode.date_source === "extracted" &&
              hoverNode.effective_date && (
                <span className="ml-1 text-sand-400">· {hoverNode.effective_date}</span>
              )}
            {hoverNode.type === "career_path" &&
              typeof hoverNode.match_score === "number" && (
                <span className="ml-1 text-sand-400">
                  · {Math.round(hoverNode.match_score * 100)}% match
                </span>
              )}
            {hoverNode.type === "skill" && <span className="ml-1 text-sand-400">· skill</span>}
          </div>
        )}

        {/* While the Gemini inference runs, the existing graph stays visible
            (nothing blanks) with a pulsing banner so the wait reads as work. */}
        {inferring && (
          <div className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2">
            <div className="flex animate-pulse items-center gap-2 rounded-full border border-espresso-200 bg-espresso-50/90 px-3 py-1.5 text-xs font-medium text-espresso-700 shadow-sm">
              <Spinner className="h-3.5 w-3.5 text-espresso-500" />
              Analyzing your profile for career paths…
            </div>
          </div>
        )}

        {selectedNode && (
          <NodeDetailPanel
            node={selectedNode}
            connections={model.connections.get(selectedNode.id)}
            onSelect={(id) => setSelectedId(id)}
            onClose={() => setSelectedId(null)}
          />
        )}
      </div>

      {/* Legend — always present, so identity is never colour-alone (categories.js
          / dataviz a11y rule). Only node kinds actually on screen are shown. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-sand-500">
        {presentCategories.map((cat) => (
          <span key={cat} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: categoryColor(cat) }}
            />
            {cat}
          </span>
        ))}
        {data.nodes.some((n) => n.type === "skill") && (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: categoryColor("Skills") }}
            />
            Skill
          </span>
        )}
        {hasCareerPaths && (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-3.5 w-3.5 rounded-full"
              style={{ backgroundColor: CAREER_PATH_COLOR }}
            />
            Career path
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-3 text-sand-500">
          <span className="inline-flex items-center gap-1.5">
            <svg width="20" height="6">
              <line x1="0" y1="3" x2="20" y2="3" stroke={GRAPH_EDGE} strokeWidth="1.5" />
            </svg>
            linked
          </span>
          <span className="inline-flex items-center gap-1.5">
            <svg width="20" height="6">
              <line
                x1="0"
                y1="3"
                x2="20"
                y2="3"
                stroke={GRAPH_EDGE}
                strokeWidth="1.5"
                strokeDasharray="4 3"
              />
            </svg>
            similar
          </span>
        </span>
      </div>

      <p className="text-center text-xs text-sand-500">
        Click a node to trace its connections · drag to reposition · click empty
        space to reset
      </p>
    </div>
  );
}
