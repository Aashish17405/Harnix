import { useMemo } from "react";
import { NODE_H, NODE_W, edgePath, layoutKinds } from "../lib";
import type { KGEdge, KGNode } from "../types";

const KIND_LABEL: Record<string, string> = {
  brd: "BRD",
  doc: "Documents",
  requirement: "Requirements",
  epic: "Epics",
  feature: "Features",
  story: "Stories",
  ticket: "Tickets",
  design: "Design",
  code: "Code",
  test: "Tests",
  actor: "Actors",
  concept: "Concepts",
  field: "Data fields",
  event: "Events",
  system: "Systems",
  domain: "Domain knowledge",
  rule: "Rules",
  constraint: "Limits & conditions",
};

const isContent = (n: KGNode) =>
  n.kind === "concept" ||
  n.kind === "rule" ||
  (n.subtype != null && n.subtype !== "");
/** Extracted concepts are shown by name; documents by their id. */
const label = (n: KGNode) => (isContent(n) ? n.title : n.external_id);
const sub = (n: KGNode) =>
  isContent(n) ? n.kind : n.title.replace(/^[^:]*:\s*/, "");

/** Sources as boxes in one column per AIDLC stage, relationships as curves. Plain SVG: no graph library needed. */
export function GraphView({
  nodes,
  edges,
  selected,
  onSelect,
  emphasizeBundle = false,
  fit = true,
}: {
  nodes: KGNode[];
  edges: KGEdge[];
  selected: string | null;
  onSelect: (n: KGNode | null) => void;
  emphasizeBundle?: boolean; // dim what is not part of the task's context
  fit?: boolean; // scale the whole picture to the width available (readable up to a few dozen sources); false = actual size, scroll
}) {
  const { placed, width, height, kinds } = useMemo(
    () => layoutKinds(nodes),
    [nodes],
  );
  const at = useMemo(
    () => new Map(placed.map((p) => [p.node.id, p])),
    [placed],
  );
  const linked = useMemo(() => {
    const s = new Set<string>();
    if (selected) {
      s.add(selected);
      for (const e of edges) {
        if (e.from === selected) s.add(e.to);
        if (e.to === selected) s.add(e.from);
      }
    }
    return s;
  }, [selected, edges]);

  if (nodes.length === 0) {
    return (
      <p className="muted">
        Nothing here yet. Add a BRD or a user story to start the graph.
      </p>
    );
  }
  const dim = (id: string, inBundle: boolean) =>
    selected ? !linked.has(id) : emphasizeBundle && !inBundle;
  return (
    <div className="graph-scroll">
      <svg
        className="graph"
        width={fit ? "100%" : width}
        height={fit ? undefined : height}
        style={
          fit
            ? {
                aspectRatio: `${width} / ${height}`,
                maxWidth: width * 1.4,
                minWidth: Math.min(width, 1150),
              }
            : undefined
        } // never smaller than still readable: scroll instead
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Knowledge graph: ${nodes.length} sources, ${edges.length} relationships`}
        onClick={() => onSelect(null)}
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="arrow" />
          </marker>
        </defs>
        {kinds.map((k, i) => (
          <text key={k} x={16 + i * (NODE_W + 96)} y={22} className="graph-col">
            {KIND_LABEL[k] ?? k}
          </text>
        ))}
        {edges.map((e) => {
          const a = at.get(e.from);
          const b = at.get(e.to);
          if (!a || !b) return null;
          const faded = selected
            ? !(e.from === selected || e.to === selected)
            : emphasizeBundle && !e.in_bundle;
          return (
            <path
              key={e.id}
              d={edgePath(a, b)}
              className={`gedge st-${e.status}${e.stale ? " stale" : ""}${e.type === "contradicts" ? " contra" : ""}${faded ? " faded" : ""}`}
              markerEnd="url(#arrow)"
            >
              <title>{`${a.node.external_id} ${e.type} ${b.node.external_id} (${e.status}${e.stale ? ", stale" : ""}${e.evidence ? `: ${e.evidence}` : ""})`}</title>
            </path>
          );
        })}
        {placed.map(({ node, x, y }) => (
          <g
            key={node.id}
            transform={`translate(${x},${y})`}
            className={`gnode k-${node.kind}${selected === node.id ? " sel" : ""}${node.in_bundle ? " inbundle" : ""}${dim(node.id, node.in_bundle) ? " faded" : ""}${node.state !== "ok" ? " partial" : ""}`}
            tabIndex={0}
            role="button"
            aria-label={`${label(node)}: ${sub(node)}`}
            aria-pressed={selected === node.id}
            onClick={(ev) => {
              ev.stopPropagation();
              onSelect(node);
            }}
            onKeyDown={(ev) => {
              if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                onSelect(node);
              }
            }}
          >
            <rect width={NODE_W} height={NODE_H} rx={6} />
            <text x={8} y={15} className="gid">
              {label(node).length > 26
                ? `${label(node).slice(0, 25)}…`
                : label(node)}
            </text>
            <text x={8} y={29} className="gtitle">
              {(node.subtype ?? sub(node)).slice(0, 34)}
            </text>
            <title>{`${node.external_id} v${node.version} (${node.kind}, ${node.origin})\n${node.title}`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function GraphLegend() {
  return (
    <div className="graph-legend row" aria-label="Legend">
      <span>
        <i className="lg line" /> verified
      </span>
      <span>
        <i className="lg line dashed" /> proposed (not verified)
      </span>
      <span>
        <i className="lg line stale" /> changed since recorded
      </span>
      <span>
        <i className="lg line contra" /> contradicts
      </span>
      <span>
        <i className="lg box" /> in the task's context
      </span>
    </div>
  );
}
