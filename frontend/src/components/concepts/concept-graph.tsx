"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { SelectField } from "@/components/ui/field";
import { useConceptGraph } from "@/hooks/use-concepts";
import { MASTERY_BG, MASTERY_LABELS, masteryFill } from "@/lib/mastery";
import { cn, formatPercent } from "@/lib/utils";
import type { ConceptGraph as Graph } from "@/types/domain";

const COL_W = 200;
const ROW_H = 64;
const NODE_W = 160;
const NODE_H = 40;
const PAD = 16;

export interface PlacedNode {
  id: string;
  name: string;
  mastery: number;
  isTarget: boolean;
  x: number;
  y: number;
}

/**
 * Layered layout: the concept in the middle, prerequisites to the left (one column per
 * step), dependents to the right; other relationships sit next to their neighbour.
 */
export function layoutGraph(graph: Graph, targetId: string): { nodes: PlacedNode[]; width: number; height: number } {
  const layer = new Map<string, number>([[targetId, 0]]);
  for (let pass = 0; pass < graph.nodes.length + 1; pass++) {
    let changed = false;
    const place = (id: string, value: number) => {
      layer.set(id, value);
      changed = true;
    };
    for (const e of graph.edges) {
      const s = layer.get(e.source);
      const t = layer.get(e.target);
      const prereq = e.type === "prerequisite";
      if (t !== undefined && s === undefined) place(e.source, prereq ? t - 1 : t);
      else if (s !== undefined && t === undefined) place(e.target, prereq ? s + 1 : s);
    }
    if (!changed) break;
  }
  const columns = new Map<number, Graph["nodes"]>();
  for (const node of graph.nodes) {
    const l = layer.get(node.id) ?? 0;
    columns.set(l, [...(columns.get(l) ?? []), node]);
  }
  const layers = [...columns.keys()].sort((a, b) => a - b);
  const min = layers[0] ?? 0;
  const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length));
  const height = tallest * ROW_H + PAD * 2;
  const nodes: PlacedNode[] = [];
  for (const l of layers) {
    const col = [...columns.get(l)!].sort((a, b) => Number(b.is_target) - Number(a.is_target) || a.name.localeCompare(b.name));
    const offset = (height - col.length * ROW_H) / 2;
    col.forEach((node, i) => {
      nodes.push({
        id: node.id,
        name: node.name,
        mastery: node.mastery,
        isTarget: node.id === targetId,
        x: PAD + (l - min) * COL_W,
        y: offset + i * ROW_H + (ROW_H - NODE_H) / 2,
      });
    });
  }
  return { nodes, width: PAD * 2 + (layers.length - 1) * COL_W + NODE_W, height };
}

function short(name: string, max = 22): string {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

export function ConceptGraphView({ conceptId }: { conceptId: string }) {
  const [depth, setDepth] = useState(1);
  const [focus, setFocus] = useState<string | null>(null);
  const router = useRouter();
  const { data, isLoading, error } = useConceptGraph(conceptId, depth);
  const layout = useMemo(() => (data ? layoutGraph(data, conceptId) : null), [data, conceptId]);
  const byId = useMemo(() => new Map(layout?.nodes.map((n) => [n.id, n]) ?? []), [layout]);
  const open = (id: string) => {
    if (id !== conceptId) router.push(`/concepts/${id}`);
  };

  return (
    <Card data-testid="concept-graph">
      <CardHeader
        title="Concept map"
        description="Prerequisites on the left, what this unlocks on the right. Select a concept to open it."
        action={
          <div className="w-36">
            <SelectField
              label="Depth"
              value={String(depth)}
              onChange={(e) => setDepth(Number(e.target.value))}
              options={[1, 2, 3].map((d) => ({ value: String(d), label: `${d} step${d === 1 ? "" : "s"}` }))}
            />
          </div>
        }
      />
      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : error || !layout || !data ? (
        <Alert tone="error">The concept map is unavailable right now.</Alert>
      ) : data.nodes.length <= 1 ? (
        <p className="text-sm text-slate-500">No linked concepts yet.</p>
      ) : (
        <>
          <div className="overflow-auto rounded-lg bg-slate-50">
            <svg
              width={layout.width}
              height={layout.height}
              viewBox={`0 0 ${layout.width} ${layout.height}`}
              role="group"
              aria-label={`Concept map with ${data.nodes.length} concepts`}
              className="mx-auto block"
            >
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" className="fill-slate-400" />
                </marker>
              </defs>
              {data.edges.map((e) => {
                const s = byId.get(e.source);
                const t = byId.get(e.target);
                if (!s || !t) return null;
                const lit = focus !== null && (focus === e.source || focus === e.target);
                const sameColumn = s.x === t.x;
                const x1 = sameColumn ? s.x + NODE_W / 2 : s.x < t.x ? s.x + NODE_W : s.x;
                const x2 = sameColumn ? t.x + NODE_W / 2 : s.x < t.x ? t.x : t.x + NODE_W;
                const y1 = sameColumn ? (s.y < t.y ? s.y + NODE_H : s.y) : s.y + NODE_H / 2;
                const y2 = sameColumn ? (s.y < t.y ? t.y : t.y + NODE_H) : t.y + NODE_H / 2;
                const mid = (x1 + x2) / 2;
                return (
                  <path
                    key={`${e.source}-${e.target}-${e.type}`}
                    data-testid="graph-edge"
                    d={sameColumn ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
                    className={cn("fill-none", lit ? "stroke-brand-500" : "stroke-slate-300")}
                    strokeWidth={lit ? 2.5 : 1.5}
                    strokeDasharray={e.type === "prerequisite" ? undefined : "4 4"}
                    markerEnd={e.type === "prerequisite" ? "url(#arrow)" : undefined}
                  />
                );
              })}
              {layout.nodes.map((n) => (
                <g
                  key={n.id}
                  data-testid="graph-node"
                  data-mastery={n.mastery}
                  data-target={n.isTarget}
                  role={n.isTarget ? "img" : "link"}
                  tabIndex={n.isTarget ? -1 : 0}
                  aria-label={`${n.name}, mastery ${formatPercent(n.mastery)}${n.isTarget ? " (this concept)" : ""}`}
                  transform={`translate(${n.x},${n.y})`}
                  className={cn("outline-none", !n.isTarget && "cursor-pointer")}
                  onClick={() => open(n.id)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      open(n.id);
                    }
                  }}
                  onMouseEnter={() => setFocus(n.id)}
                  onMouseLeave={() => setFocus(null)}
                  onFocus={() => setFocus(n.id)}
                  onBlur={() => setFocus(null)}
                >
                  <title>{`${n.name} — mastery ${formatPercent(n.mastery)}`}</title>
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={10}
                    className={cn(masteryFill(n.mastery), n.isTarget || focus === n.id ? "stroke-brand-700" : "stroke-transparent")}
                    strokeWidth={n.isTarget ? 3 : 2}
                  />
                  <text x={NODE_W / 2} y={NODE_H / 2 - 3} textAnchor="middle" className="fill-black text-[12px] font-medium">
                    {short(n.name)}
                  </text>
                  <text x={NODE_W / 2} y={NODE_H / 2 + 12} textAnchor="middle" className="fill-black/70 text-[10px]">
                    {formatPercent(n.mastery)}
                  </text>
                </g>
              ))}
            </svg>
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            {MASTERY_LABELS.map((label) => (
              <li key={label} className="flex items-center gap-1.5 capitalize">
                <span aria-hidden className={cn("size-3 rounded", MASTERY_BG[label])} /> {label}
              </li>
            ))}
            <li className="flex items-center gap-1.5">→ prerequisite</li>
            <li className="flex items-center gap-1.5">┄ related</li>
          </ul>
        </>
      )}
    </Card>
  );
}
