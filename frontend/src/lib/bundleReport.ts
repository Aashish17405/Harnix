import type { StoredBundle } from "../types";

const cell = (s: unknown) =>
  String(s ?? "")
    .replace(/\s+/g, " ")
    .replace(/\|/g, "\\|")
    .trim();
const table = (head: string[], rows: unknown[][]) =>
  rows.length
    ? [
        `| ${head.join(" | ")} |`,
        `|${head.map(() => " --- ").join("|")}|`,
        ...rows.map((r) => `| ${r.map(cell).join(" | ")} |`),
      ].join("\n")
    : "_none recorded_";

/** Everything the context builder recorded for one task's context, as Markdown. Only recorded facts: nothing is inferred here. */
export function bundleReport(b: StoredBundle): string {
  const p = b.pipeline;
  const out: string[] = [`# Context report: bundle ${b.bundle_id}`, ""];
  out.push(
    `- status: **${b.status}**`,
    `- embedding model: ${b.embedding_model}`,
    `- prompt: ${cell(b.query)}`,
    "",
  );
  if (p?.prompt?.embedding) {
    const e = p.prompt.embedding;
    out.push(
      "## 1. Prompt embedding",
      `Model ${e.model}, ${e.dims} dimensions. Only the first components are stored: [${e.head.join(", ")}]`,
      "",
    );
  }
  if (p?.search) {
    const s = p.search;
    out.push(
      "## 2. Search over stored chunks",
      `${s.chunks_searched} chunks searched, mode ${s.mode}, RRF k=${s.rrf_k}, weights keyword ${s.weights.keyword} / vector ${s.weights.vector}.`,
      "",
      "### Fused ranking",
      table(
        ["#", "document", "section", "rrf", "keyword rank", "vector rank"],
        s.fused.map((r, i) => [
          i + 1,
          r.external_id,
          r.heading,
          r.rrf,
          r.keyword_rank,
          r.vector_rank,
        ]),
      ),
      "",
      "### Keyword ranking (BM25)",
      table(
        ["#", "document", "section", "score"],
        s.keyword.map((r) => [r.rank, r.external_id, r.heading, r.score]),
      ),
      "",
      "### Vector ranking (cosine)",
      table(
        ["#", "document", "section", "score"],
        s.vector.map((r) => [r.rank, r.external_id, r.heading, r.score]),
      ),
      "",
    );
  }
  out.push(
    "## 3. Documents considered (reranked)",
    table(
      [
        "document",
        "kind",
        "found by",
        "similarity",
        "score",
        "decision",
        "reason",
        "pulled by",
      ],
      (p?.candidates ?? []).map((c) => [
        c.external_id,
        c.kind,
        c.found_by.join("+"),
        c.similarity.toFixed(4),
        c.score?.toFixed(4),
        c.decision,
        c.reason,
        c.pulled_by,
      ]),
    ),
    "",
  );
  for (const c of p?.candidates ?? []) {
    if (!c.chunks?.length) continue;
    out.push(
      `### Best chunks of ${c.external_id}`,
      table(
        ["chunk", "section", "similarity", "text"],
        c.chunks.map((k) => [
          k.ordinal,
          k.heading,
          k.similarity.toFixed(4),
          k.text,
        ]),
      ),
      "",
    );
  }
  out.push("## 4. Graph nodes and relationships pulled");
  for (const x of p?.expansion ?? []) {
    out.push(
      "",
      `### From ${x.seed}: ${x.link_types.join(", ")}, depth ${x.depth}, via ${x.via ?? "?"}${x.truncated ? " (truncated)" : ""}`,
      "Nodes",
      table(
        ["node", "kind", "hop", "title"],
        x.nodes.map((n) => [n.external_id, n.kind, n.hop, n.title]),
      ),
      "",
      "Relationships",
      table(
        ["from", "type", "to", "status", "origin", "stale", "evidence"],
        x.edges.map((e) => [
          e.from,
          e.type,
          e.to,
          e.status,
          e.origin,
          e.stale,
          e.evidence,
        ]),
      ),
    );
  }
  out.push(
    "",
    "## 5. Filter and budget",
    p
      ? `Relevance: best ${p.relevance?.best ?? "-"} vs floor ${p.relevance?.floor ?? p.weights.floor}. Max items ${p.budgets.max_items}.`
      : "_not recorded_",
    "",
    "## 6. What the agent received",
  );
  for (const [slot, list] of Object.entries(b.slots)) {
    out.push(
      "",
      `### ${slot}`,
      table(
        ["document", "kind", "version", "title", "sections given"],
        list.map((i) => [
          i.external_id,
          i.kind,
          i.version,
          i.title,
          i.sections?.map((s) => s.ordinal).join(", ") ?? "whole",
        ]),
      ),
    );
  }
  out.push(
    "",
    "### Relationships among them",
    table(
      ["from", "type", "to", "status", "evidence"],
      (b.relationships ?? []).map((r) => [
        r.from,
        r.type,
        r.to,
        r.status,
        r.evidence,
      ]),
    ),
    "",
  );
  return out.join("\n");
}

function save(name: string, text: string, type: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function downloadReport(b: StoredBundle, as: "md" | "json") {
  const id = b.bundle_id.slice(0, 12);
  if (as === "md")
    save(`context-report-${id}.md`, bundleReport(b), "text/markdown");
  else
    save(
      `context-report-${id}.json`,
      JSON.stringify(b, null, 2),
      "application/json",
    );
}
