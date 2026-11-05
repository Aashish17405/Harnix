import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KGEdge, KGNode } from "../types";

/** Node colours: the same palette as docs/neo4j-style.grass, so the dashboard and the Neo4j Browser look alike. */
export const KIND_COLOR: Record<string, string> = {
  brd: "#D7263D", requirement: "#8C564B", epic: "#7B2CBF", feature: "#F77F00", story: "#1F77B4", ticket: "#17BECF",
  design: "#BCBD22", code: "#3D3D3D", test: "#2CA02C", doc: "#7F7F7F",
  concept: "#57CC99", actor: "#FF5DA2", field: "#80DEEA", event: "#FFB000", system: "#4361EE", domain: "#9B5DE5", rule: "#FFE14D", constraint: "#F4A261",
}; // prettier-ignore
const DARK_TEXT = new Set(["concept", "field", "event", "rule", "constraint"]); // light fills get dark captions
const RADIUS: Record<string, number> = {
  brd: 34,
  epic: 30,
  feature: 26,
  story: 24,
  requirement: 22,
};
const radius = (k: string) => RADIUS[k] ?? 19;
const isContent = (n: KGNode) =>
  !!n.subtype || n.kind === "concept" || n.kind === "rule";
// inside the circle: a namespaced id without its backlog (BRD-02/US-001 -> US-001); the full id is in the tooltip
const caption = (n: KGNode) =>
  isContent(n)
    ? n.title
    : n.external_id.includes("/")
      ? n.external_id.split("/").slice(1).join("/")
      : n.external_id;
const kindOf = (n: KGNode) => n.subtype ?? n.kind;

interface P {
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

/**
 * The knowledge graph the way a graph database browser shows it: coloured circles per kind, captions inside, arrows
 * labelled with the relationship type, laid out by a small force simulation (nodes repel, links pull, a weak pull to
 * the centre). Drag a node to move it (it stays where you put it), drag the background to pan, wheel to zoom.
 */
export function NetworkView({
  nodes,
  edges,
  selected,
  onSelect,
  emphasizeBundle = false,
  height = 640,
}: {
  nodes: KGNode[];
  edges: KGEdge[];
  selected: string | null;
  onSelect: (n: KGNode | null) => void;
  emphasizeBundle?: boolean;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pos = useRef(new Map<string, P>());
  const alpha = useRef(1);
  // a copy of the positions for rendering (the simulation works on the ref; render reads only this snapshot)
  const [at, setAt] = useState<Map<string, { x: number; y: number }>>(
    new Map(),
  );
  const publish = useCallback(
    () =>
      setAt(new Map([...pos.current].map(([k, p]) => [k, { x: p.x, y: p.y }]))),
    [],
  );
  const [width, setWidth] = useState(900);
  const [view, setView] = useState({ k: 0.6, x: 450, y: 320 }); // the layout grows around (0, 0): start centred
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{
    id: string | null;
    sx: number;
    sy: number;
    vx: number;
    vy: number;
    moved: boolean;
  } | null>(null);
  const fitted = useRef(false);
  const running = useRef(false);
  const userMoved = useRef(false); // once you pan, zoom or drag, the view is yours: no more automatic fitting
  const frames = useRef(0);
  const [kick, setKick] = useState(0); // restarts the simulation after it settled (drag, re-layout)

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const links = useMemo(
    () =>
      edges.filter(
        (e) => byId.has(e.from) && byId.has(e.to) && e.from !== e.to,
      ),
    [edges, byId],
  );

  // keep positions of nodes that stay; place new ones on a spiral (deterministic, so a layout is repeatable)
  useEffect(() => {
    const m = pos.current;
    for (const id of [...m.keys()]) if (!byId.has(id)) m.delete(id);
    nodes.forEach((n, i) => {
      if (!m.has(n.id)) {
        const a = i * 2.39996; // golden angle
        const r = 40 * Math.sqrt(i + 1);
        m.set(n.id, {
          x: r * Math.cos(a),
          y: r * Math.sin(a),
          vx: 0,
          vy: 0,
          pinned: false,
        });
      }
    });
    alpha.current = 1;
    fitted.current = false;
    userMoved.current = false;
    frames.current = 0;
    publish(); // draw the starting positions at once, before the first simulation frame
  }, [nodes, byId, publish]);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || 900));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    const ps = [...pos.current.values()];
    if (!ps.length) return;
    const xs = ps.map((p) => p.x);
    const ys = ps.map((p) => p.y);
    const [x0, x1, y0, y1] = [
      Math.min(...xs) - 50,
      Math.max(...xs) + 50,
      Math.min(...ys) - 50,
      Math.max(...ys) + 50,
    ];
    const k = Math.min(
      2,
      Math.max(0.15, Math.min(width / (x1 - x0), height / (y1 - y0))),
    );
    setView({
      k,
      x: width / 2 - k * ((x0 + x1) / 2),
      y: height / 2 - k * ((y0 + y1) / 2),
    });
  }, [width, height]);

  // the simulation: one step per animation frame while it is still moving
  useEffect(() => {
    let raf = 0;
    const step = () => {
      const m = pos.current;
      const ps = nodes
        .map((n) => [n, m.get(n.id)!] as const)
        .filter(([, p]) => p);
      const a = alpha.current;
      if (a < 0.02) {
        running.current = false;
        if (!fitted.current && !userMoved.current) fit();
        fitted.current = true;
        return;
      }
      if (!userMoved.current && !fitted.current && frames.current++ % 20 === 0)
        fit(); // follow the layout as it spreads
      // repulsion between every pair (fine for a few hundred nodes)
      for (let i = 0; i < ps.length; i++) {
        const pi = ps[i][1];
        for (let j = i + 1; j < ps.length; j++) {
          const pj = ps[j][1];
          let dx = pj.x - pi.x;
          let dy = pj.y - pi.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) {
            dx = Math.random() - 0.5;
            dy = Math.random() - 0.5;
            d2 = 0.5;
          }
          const d = Math.sqrt(d2);
          const f = (6000 / d2) * a;
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          pi.vx -= fx;
          pi.vy -= fy;
          pj.vx += fx;
          pj.vy += fy;
        }
      }
      // links pull their ends towards a comfortable length
      for (const e of links) {
        const s = m.get(e.from)!;
        const t = m.get(e.to)!;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = ((d - 140) / d) * 0.05 * a;
        s.vx += dx * f;
        s.vy += dy * f;
        t.vx -= dx * f;
        t.vy -= dy * f;
      }
      for (const [, p] of ps) {
        p.vx -= p.x * 0.003 * a; // a weak pull to the centre keeps separate groups on screen
        p.vy -= p.y * 0.003 * a;
        if (!p.pinned) {
          p.x += p.vx;
          p.y += p.vy;
        }
        p.vx *= 0.55;
        p.vy *= 0.55;
      }
      // circles never overlap: push overlapping pairs apart directly, however cool the simulation is
      for (let pass = 0; pass < 2; pass++) {
        for (let i = 0; i < ps.length; i++) {
          const [ni, pi] = ps[i];
          for (let j = i + 1; j < ps.length; j++) {
            const [nj, pj] = ps[j];
            const dx = pj.x - pi.x;
            const dy = pj.y - pi.y;
            const min = radius(kindOf(ni)) + radius(kindOf(nj)) + 10;
            const d2 = dx * dx + dy * dy;
            if (d2 >= min * min) continue;
            const d = Math.sqrt(d2) || 0.01;
            const push = (min - d) / 2 / d;
            if (!pi.pinned) {
              pi.x -= dx * push;
              pi.y -= dy * push;
            }
            if (!pj.pinned) {
              pj.x += dx * push;
              pj.y += dy * push;
            }
          }
        }
      }
      alpha.current *= 0.988;
      publish();
      raf = requestAnimationFrame(step);
    };
    running.current = true;
    raf = requestAnimationFrame(step);
    return () => {
      running.current = false;
      cancelAnimationFrame(raf);
    };
  }, [nodes, links, fit, publish, kick]);

  const reheat = () => {
    if (alpha.current < 0.3) {
      alpha.current = 0.3;
      fitted.current = true; // keep the user's view while things settle again
      if (!running.current) setKick((k) => k + 1);
    }
  };

  // what is related to the selection (or the hovered node) stays bright, the rest fades
  const focus = selected ?? hover;
  const near = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    for (const e of links) {
      if (e.from === focus) s.add(e.to);
      if (e.to === focus) s.add(e.from);
    }
    return s;
  }, [focus, links]);

  const toWorld = (cx: number, cy: number) => {
    const r = box.current!.getBoundingClientRect();
    return {
      x: (cx - r.left - view.x) / view.k,
      y: (cy - r.top - view.y) / view.k,
    };
  };
  const onDown = (e: React.PointerEvent, id: string | null) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    drag.current = {
      id,
      sx: e.clientX,
      sy: e.clientY,
      vx: view.x,
      vy: view.y,
      moved: false,
    };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) > 3)
      d.moved = true;
    if (!d.moved) return;
    userMoved.current = true;
    if (d.id) {
      const p = pos.current.get(d.id);
      if (p) {
        const w = toWorld(e.clientX, e.clientY);
        Object.assign(p, { x: w.x, y: w.y, vx: 0, vy: 0, pinned: true });
        reheat();
        publish();
      }
    } else {
      setView((v) => ({
        ...v,
        x: d.vx + e.clientX - d.sx,
        y: d.vy + e.clientY - d.sy,
      }));
    }
  };
  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) onSelect(d.id ? (byId.get(d.id) ?? null) : null);
  };
  const zoom = (factor: number, cx = width / 2, cy = height / 2) =>
    setView((v) => {
      const k = Math.min(4, Math.max(0.1, v.k * factor));
      return {
        k,
        x: cx - ((cx - v.x) * k) / v.k,
        y: cy - ((cy - v.y) * k) / v.k,
      };
    });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      userMoved.current = true;
      zoom(
        e.deltaY < 0 ? 1.15 : 1 / 1.15,
        e.clientX - r.left,
        e.clientY - r.top,
      );
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  });

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of nodes) c[kindOf(n)] = (c[kindOf(n)] ?? 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [nodes]);
  const rels = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of links) c[e.type] = (c[e.type] ?? 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [links]);

  if (!nodes.length)
    return <p className="empty">Nothing to show yet: add documents above.</p>;
  const showLabel = (e: KGEdge) =>
    view.k >= 1.1 || e.from === focus || e.to === focus;

  return (
    <div className="net">
      <div className="net-bar">
        <div className="net-chips" aria-label="Node labels">
          <span className="net-chip all">*({nodes.length})</span>
          {counts.map(([k, n]) => (
            <span
              key={k}
              className="net-chip"
              style={{
                background: KIND_COLOR[k] ?? "#A5ABB6",
                color: DARK_TEXT.has(k) ? "#1d1d1d" : "#fff",
              }}
            >
              {k} ({n})
            </span>
          ))}
        </div>
        <div className="net-chips" aria-label="Relationship types">
          <span className="net-chip rel all">*({links.length})</span>
          {rels.map(([t, n]) => (
            <span key={t} className="net-chip rel">
              {t.toUpperCase()} ({n})
            </span>
          ))}
        </div>
      </div>
      <div
        ref={box}
        className="net-canvas"
        style={{ height }}
        onPointerDown={(e) => onDown(e, null)}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => (drag.current = null)}
      >
        <div className="net-tools">
          <button type="button" onClick={() => zoom(1.25)} aria-label="Zoom in">
            +
          </button>
          <button
            type="button"
            onClick={() => zoom(1 / 1.25)}
            aria-label="Zoom out"
          >
            −
          </button>
          <button type="button" onClick={fit} aria-label="Fit the graph">
            Fit
          </button>
          <button
            type="button"
            onClick={() => {
              for (const p of pos.current.values()) p.pinned = false;
              alpha.current = 1;
              fitted.current = false;
              setKick((k) => k + 1);
            }}
            aria-label="Lay out again"
          >
            Re-layout
          </button>
        </div>
        <svg
          width="100%"
          height={height}
          role="group"
          aria-label="Knowledge graph (network)"
        >
          <defs>
            <marker
              id="net-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" className="net-arrowhead" />
            </marker>
          </defs>
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {links.map((e) => {
              const s = at.get(e.from);
              const t = at.get(e.to);
              if (!s || !t) return null;
              const dx = t.x - s.x;
              const dy = t.y - s.y;
              const d = Math.sqrt(dx * dx + dy * dy) || 1;
              const rs = radius(kindOf(byId.get(e.from)!));
              const rt = radius(kindOf(byId.get(e.to)!)) + 3;
              const [x1, y1, x2, y2] = [
                s.x + (dx / d) * rs,
                s.y + (dy / d) * rs,
                t.x - (dx / d) * rt,
                t.y - (dy / d) * rt,
              ];
              const faded =
                (near && !(near.has(e.from) && near.has(e.to))) ||
                (emphasizeBundle && !e.in_bundle);
              let ang = (Math.atan2(dy, dx) * 180) / Math.PI;
              if (ang > 90 || ang < -90) ang += 180; // keep the label upright
              return (
                <g
                  key={e.id}
                  className={`net-edge st-${e.status}${e.stale ? " stale" : ""}${e.type === "contradicts" ? " contra" : ""}${faded ? " faded" : ""}`}
                >
                  <line
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    markerEnd="url(#net-arrow)"
                  >
                    <title>{`${byId.get(e.from)!.external_id} ${e.type} ${byId.get(e.to)!.external_id} (${e.status}${e.stale ? ", changed since" : ""})`}</title>
                  </line>
                  {showLabel(e) && d > 70 && (
                    <text
                      x={(x1 + x2) / 2}
                      y={(y1 + y2) / 2 - 3}
                      transform={`rotate(${ang} ${(x1 + x2) / 2} ${(y1 + y2) / 2})`}
                      textAnchor="middle"
                    >
                      {e.type.toUpperCase()}
                    </text>
                  )}
                </g>
              );
            })}
            {nodes.map((n) => {
              const p = at.get(n.id);
              if (!p) return null;
              const k = kindOf(n);
              const r = radius(k);
              const text = caption(n);
              const max = Math.max(4, Math.floor(r / 3.1));
              const faded =
                (near && !near.has(n.id)) || (emphasizeBundle && !n.in_bundle);
              return (
                <g
                  key={n.id}
                  className={`net-node${n.id === selected ? " sel" : ""}${faded ? " faded" : ""}${emphasizeBundle && n.in_bundle ? " inbundle" : ""}`}
                  transform={`translate(${p.x},${p.y})`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${isContent(n) ? n.title : n.external_id}: ${isContent(n) ? k : n.title}`}
                  onPointerDown={(e) => onDown(e, n.id)}
                  onPointerEnter={() => setHover(n.id)}
                  onPointerLeave={() => setHover(null)}
                  onKeyDown={(e) =>
                    (e.key === "Enter" || e.key === " ") &&
                    (e.preventDefault(), onSelect(n))
                  }
                >
                  <circle r={r + 6} className="net-halo" />
                  <circle r={r} fill={KIND_COLOR[k] ?? "#A5ABB6"} />
                  <text
                    dy="0.35em"
                    textAnchor="middle"
                    fill={DARK_TEXT.has(k) ? "#1d1d1d" : "#fff"}
                    fontSize={r >= 26 ? 9 : 8}
                  >
                    {text.length > max ? `${text.slice(0, max - 1)}…` : text}
                  </text>
                  <title>{`${n.external_id}: ${n.title} (${k}, v${n.version})`}</title>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <p className="help">
        Drag a node to move it (it stays where you drop it), drag the background
        to pan, scroll to zoom. Click a node for its properties. Relationship
        names appear when you zoom in or point at a node.
      </p>
    </div>
  );
}
