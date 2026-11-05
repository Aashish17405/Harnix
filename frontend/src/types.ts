export type TaskStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled";

export const ACTIVE: TaskStatus[] = [
  "running",
  "awaiting_approval",
  "verifying",
];
export const isActive = (s: string) => (ACTIVE as string[]).includes(s);
export const isTerminal = (s: string) =>
  ["completed", "failed", "cancelled"].includes(s);

export interface Task {
  id: string;
  title: string;
  repo_path: string;
  instructions: string;
  acceptance_criteria: string[];
  verification_commands: string[];
  thinking?: ThinkingLevel | null;
  skills?: string[];
  status: TaskStatus;
  outcome: "verified" | "unverified" | "verification_failed" | null;
  error: string | null;
  parent_task_id: string | null;
  provider: string | null;
  model: string | null;
  max_iterations: number;
  max_duration_seconds: number;
  iterations: number;
  model_calls: number;
  tool_calls: number;
  tool_failures: number;
  policy_denials: number;
  recovery_attempts: number;
  input_tokens: number;
  output_tokens: number;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  estimated_cost_usd: number | null;
  report?: Report | null;
  images?: ImageInfo[];
  session_id?: string | null; // the conversation this message belongs to
  turn?: number; // 1 = the first message of it
  discarded?: boolean; // rolled back
  knowledge_project?: string | null; // the knowledge project whose context bundle this task got
}

export interface TaskList {
  items: Task[];
  total: number;
  limit: number;
  offset: number;
}

export interface HarnixEvent {
  id: number;
  task_id: string;
  ts: string;
  type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface Verification {
  id: number;
  phase: "baseline" | "final";
  attempt: number;
  name: string;
  kind: string;
  command: string | null;
  status: "passed" | "failed" | "skipped" | "unavailable";
  exit_code: number | null;
  duration_seconds: number;
  output: string;
  note: string | null;
}

export interface Approval {
  id: string;
  task_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  reason: string;
  status: "pending" | "approved" | "rejected" | "expired";
  note: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface ChangedFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface ApplyResult {
  applied: boolean;
  dry_run: boolean;
  files: { path: string; status: string }[];
  verified: boolean;
}

export interface Diff {
  diff: string;
  files: ChangedFile[];
  live: boolean;
}

export interface Report {
  status: string;
  outcome: string | null;
  error: string | null;
  false_success_claim: boolean;
  agent: {
    finish_reason: string;
    claimed_done: boolean;
    final_message: string;
  };
  independent_verification: {
    outcome: string;
    blocking: string[];
    reasons: string[];
    limitations: string[];
  } | null;
  known_limitations: string[];
  execution_mode: string;
  provider: { name: string; model: string | null };
}

export interface Health {
  status: string;
  version: string;
  execution_mode: "docker" | "local";
  sandbox: { available: boolean; detail: string };
  provider: {
    name: string;
    model: string;
    configured: boolean;
    problem: string | null;
  };
  mock_provider: boolean;
  auth_required: boolean;
  spend: { spent_usd: number } | null;
}

export interface HarnixConfig {
  provider: string;
  model: string;
  execution_mode: string;
  approval_policy: string;
  allowed_repo_roots: string[];
  limits: Record<string, number>;
}

export interface TaskCreate {
  repo_path: string;
  instructions: string;
  title?: string;
  acceptance_criteria: string[];
  verification_commands: string[];
  thinking?: ThinkingLevel;
  skills?: string[];
  images?: { name: string; media_type: string; data: string }[];
  session_id?: string;
  knowledge_project?: string;
  max_iterations?: number;
  max_duration_seconds?: number;
}

export interface ImageInfo {
  index: number;
  name: string;
  media_type: string;
  size: number;
  width?: number | null;
  height?: number | null;
}

export type ThinkingLevel = "low" | "medium" | "high" | "xhigh";

export interface Skill {
  id: string;
  name: string;
  description: string;
  source: string;
  listed: boolean;
}

export interface SkillList {
  items: Skill[];
  problems: string[];
  repo_skills: boolean;
}

export interface MemoryNote {
  id: string;
  content: string;
  source: "user" | "agent";
  task_id: string | null;
  created_at: string;
}

// ---- knowledge base -----------------------------------------------------------------------------------------
export type EdgeStatus = "proposed" | "verified" | "rejected";

export interface KnowledgeProject {
  project: string;
  sources: number;
  by_kind: Record<string, number>;
}

export interface KGNode {
  id: string;
  kind: string;
  external_id: string;
  title: string;
  version: number;
  origin: string;
  state: string;
  in_bundle: boolean;
  subtype?: string | null; // for extracted concepts: actor, concept, field, rule, constraint, event, system
}

export interface KGEdge {
  id: string;
  from: string;
  to: string;
  type: string;
  status: EdgeStatus;
  origin: string;
  evidence: string;
  stale: boolean;
  in_bundle: boolean;
}

export interface KGraph {
  project: string;
  bundle: string | null;
  nodes: KGNode[];
  edges: KGEdge[];
}

export interface KnowledgeIngestResult {
  project: string;
  embedding_model: string;
  created: number;
  updated: number;
  unchanged: number;
  partial: string[];
  failed: string[];
  skipped: string[];
  unresolved: string[];
  ingested: string[];
  suggested: number;
  edges_created: number;
}

export interface BundleItem {
  source_id: string;
  kind: string;
  external_id: string;
  title: string;
  version: number;
  content: string;
  excerpt_of?: string;
  matched: boolean;
  found_by?: string[]; // id | search | graph | graph:neo4j
  content_hash?: string;
  origin?: string;
  slot?: string;
  content_truncated?: boolean;
  ingestion_state?: string;
  open_questions?: string[];
  target?: boolean; // what the task is about: given whole
  sections?: {
    chunk_id: string;
    ordinal: number;
    heading: string;
    similarity: number;
  }[]; // the parts given, when cut
}

export interface KnowledgeBundle {
  bundle_id: string;
  project: string;
  status: "sufficient" | "insufficient";
  sufficiency: {
    status: string;
    missing: string[];
    contradictions: boolean;
    notes: string[];
  };
  slots: Record<string, BundleItem[]>;
  unresolved_questions: string[];
  embedding_model: string;
}

export interface KnowledgeSource {
  source_id: string;
  kind: string;
  external_id: string;
  title: string;
  version: number;
  content: string;
  metadata: Record<string, unknown>;
  origin: string;
  uri: string | null;
}

export interface ReviewState {
  verdict: "pass" | "fail" | "unsure";
  note: string;
  by: string;
  at: string;
}

export interface EvalCriterion {
  id: string;
  source: string;
  source_version?: number; // the version of the story the agent was given (criteria are read from it)
  text: string;
  review: ReviewState | null;
  // the changed test that most resembles this criterion: a pointer for the reviewer, not a verdict
  test?: { file: string; name: string; similarity: number; likely: boolean };
}

export interface TaskEvaluation {
  task_id: string;
  project: string;
  bundle_id: string | null;
  changed_files: string[];
  checks: {
    name: string;
    status: "pass" | "fail" | "warn" | "info";
    detail: string;
  }[];
  traceability: {
    requirement: string;
    kind: string;
    title: string;
    code_changed: boolean;
    test_linked: boolean;
    links: {
      external_id: string;
      kind: string;
      type: string;
      status: string;
      changed: boolean;
    }[];
  }[];
  criteria: EvalCriterion[];
  criteria_summary: {
    pass: number;
    fail: number;
    unsure: number;
    unreviewed: number;
  };
  verdict: "pass" | "fail" | "needs review";
  note: string;
}

export interface KnowledgeEdgeRow {
  edge_id: string;
  from: string;
  to: string;
  type: string;
  status: EdgeStatus;
  origin: string;
  evidence: string;
  task_id: string | null;
  decided_by: string | null;
}

export interface EmbeddingPoint {
  id: string;
  source_id: string;
  external_id: string;
  kind: string;
  heading: string;
  text: string;
  x: number;
  y: number;
}

export interface EmbeddingMapData {
  project: string;
  model: string | null;
  dimensions: number;
  chunks: number;
  embedded: number;
  by_model: Record<string, number>;
  truncated: boolean;
  points: EmbeddingPoint[];
}

export interface Neighbors {
  chunk: {
    id: string;
    external_id: string;
    kind: string;
    heading: string;
    text: string;
    model: string;
    dimensions: number;
    vector_head: number[];
  };
  neighbors: {
    id: string;
    external_id: string;
    kind: string;
    heading: string;
    text: string;
    similarity: number;
  }[];
}

export interface KnowledgeSearchResult {
  project: string;
  query: string;
  note: string;
  embedding_model: string;
  hits: {
    source_id: string;
    kind: string;
    external_id: string;
    title: string;
    version: number;
    score: number;
    matched_by: string[];
    snippet: string;
    chunk_ids: string[];
  }[];
}

export interface ExtractJob {
  id: string;
  project: string;
  status: "running" | "done" | "failed";
  done: number;
  total: number;
  error: string | null;
  report: {
    sources: number;
    skipped_unchanged: number;
    failed: string[];
    entities: number;
    relations: number;
    mentions: number;
    rejected_entities: number;
    rejected_relations: number;
  } | null;
}

/** What happened to one candidate while the context bundle was built (GET /knowledge/bundles/{id} -> pipeline). */
export interface BundleCandidate {
  external_id: string;
  kind: string;
  found_by: string[];
  decision: "chosen" | "over_budget" | "dropped";
  reason: string | null;
  similarity: number;
  bonus: {
    named_id: number;
    search: number;
    best_seed_link: number;
    kind: number;
  };
  score: number | null;
  seed: boolean;
  /** recorded with the drill-down; older bundles do not have these */
  title?: string;
  source_id?: string;
  pulled_by?: string | null; // the seed whose graph traversal brought it in
  chunks?: {
    chunk_id: string;
    ordinal: number;
    heading: string;
    similarity: number;
    text: string;
  }[]; // its best chunks for the prompt
}

/** One chunk in a ranking the search was fused from. */
export interface SearchRow {
  chunk_id: string;
  source_id: string;
  external_id: string;
  kind: string;
  heading: string;
  ordinal?: number; // the chunk's place in its document
  snippet: string;
  rank?: number;
  score?: number; // BM25 or cosine
  rrf?: number; // fused score
  keyword_rank?: number | null;
  vector_rank?: number | null;
}

/** One graph traversal made while building a context: where from, how, and everything it reached. */
export interface Expansion {
  seed: string;
  link_types: string[];
  depth: number;
  via: string | null;
  truncated: boolean;
  nodes: {
    external_id: string;
    kind: string;
    title: string;
    hop: number | null;
  }[];
  edges: {
    from: string;
    to: string;
    type: string;
    status: string;
    origin: string;
    evidence: string;
    stale: boolean;
  }[];
}

export interface BundlePipeline {
  named_ids: string[];
  relevance?: { best: number; floor: number }; // best requirement similarity vs the relevance floor
  seeds: string[];
  candidates: BundleCandidate[];
  timings_ms: Record<string, number>;
  weights: {
    named_id: number;
    search: number;
    best_seed_link: number;
    kind: Record<string, number>;
    floor: number;
  };
  budgets: {
    max_items: number;
    per_slot: Record<string, number>;
    per_kind: Record<string, number>;
    default_per_kind: number;
  };
  /** the layers of the drill-down (bundles built before it do not have them) */
  prompt?: {
    text: string;
    named: { id: string; source_id: string }[];
    embedding: {
      model: string;
      dims: number;
      head: number[];
      nonzero?: number;
      top?: [number, number][];
    } | null;
  };
  search?: {
    mode: string;
    chunks_searched: number;
    rrf_k: number;
    weights: { keyword: number; vector: number };
    keyword: SearchRow[];
    vector: SearchRow[];
    fused: SearchRow[];
    embedding: {
      model: string;
      dims: number;
      head: number[];
      nonzero?: number;
      top?: [number, number][];
    };
  } | null;
  expansion?: Expansion[];
}

export interface StoredBundle extends KnowledgeBundle {
  query?: string;
  retrieval_trace?: {
    tool: string;
    args: Record<string, unknown>;
    results: number;
  }[];
  relationships?: {
    from: string;
    to: string;
    type: string;
    status: string;
    evidence?: string;
    stale?: boolean;
  }[];
  pipeline?: BundlePipeline;
}

interface AuditRef {
  at: string;
  action: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  detail: Record<string, any>;
}

/** GET /knowledge/pipeline: every stage of a project's knowledge pipeline and what it holds now. */
export interface ProjectPipeline {
  project: string;
  ingest: {
    documents: Record<string, number>;
    states: Record<string, number>;
    last: AuditRef | null;
    /** documents stored as `doc` because their declared kind is unknown: a person accepts or reclassifies them */
    review?: {
      source_id: string;
      external_id: string;
      title: string;
      uri: string | null;
      requested_kind: string | null;
    }[];
  };
  chunks: {
    total: number;
    embedded: number;
    by_model: Record<string, number>;
    vector_index: string;
    embedder: string;
    current_format?: string; // model|chunk format the search uses now
    current?: number; // chunks embedded in that format
  };
  extraction: { concepts: number; rules: number; last: AuditRef | null };
  /** links the model inferred from the text of documents (proposals until a person decides) */
  linking?: { last: AuditRef | null; by_status: Record<string, number> };
  ontology: {
    types: number;
    relations: number;
    synonyms: Record<string, string>;
    links_checked: number;
    violations_total: number;
    violations: {
      from: string;
      from_type: string;
      type: string;
      to: string;
      to_type: string;
    }[];
    /** links a document declared that the ontology refused at intake (never stored as edges) */
    refused_total?: number;
    refused?: {
      from: string;
      type: string;
      to: string;
      from_type: string;
      to_type: string;
      rule: string;
      at: string;
    }[];
  };
  graph: {
    edges: number;
    by_status: Record<string, number>;
    by_origin: Record<string, number>;
    by_type: Record<string, number>;
  };
  neo4j: {
    enabled: boolean;
    available?: boolean;
    current?: boolean;
    nodes?: number;
    edges?: number;
    synced_at?: string;
    detail?: string;
  };
  evaluation: { retrieval: AuditRef | null; bundles_built: number };
}

/** GET /knowledge/coverage: did everything reach the knowledge base? */
export interface CoverageReport {
  project: string;
  chunks: {
    lines: number;
    uncovered: number;
    examples: { source: string; text: string }[];
  };
  orphans: {
    count: number;
    by_kind: Record<string, number>;
    examples: string[];
  };
  /** only when an input folder was named */
  inputs?: {
    datapoints: number;
    mapped: number;
    score: number | null;
    missing: { file: string; where: string; text: string }[];
    missing_total: number;
    files: Record<
      string,
      { datapoints: number; mapped: number; error?: string }
    >;
  };
}

/** GET /knowledge/findings: problems in the documents themselves, from the last ingestion of each folder. */
export interface FindingsReport {
  project: string;
  count: number;
  by_kind: Record<string, number>;
  findings: {
    kind: string;
    where: string;
    message: string;
    root: string;
    at: string;
  }[];
}

/** POST /knowledge/export: the Markdown copy of the project. */
export interface MarkdownExport {
  project: string;
  folder: string;
  files: number;
  written: number;
  unchanged: number;
  removed: number;
}

/** A background knowledge job (folder indexing, upload, extraction) and every step it reported. */
export interface KnowledgeJob {
  id: string;
  kind: "ingest" | "upload" | "extract" | "link" | "rebuild";
  project: string;
  title: string;
  status: "running" | "done" | "failed";
  stage: string;
  started_at: number;
  finished_at: number | null;
  done: number;
  total: number;
  events: {
    at: number;
    stage: string;
    message: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    data: Record<string, any>;
  }[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  report: any;
  error: string | null;
}

/** GET /knowledge/bundles: one row per stored context graph. */
export interface BundleSummary {
  bundle_id: string;
  created_at: string;
  status: string;
  query: string;
  items: Record<string, number>;
  relationships: number;
  tasks: string[];
  previews: number;
  has_pipeline: boolean;
}

/** POST /knowledge/query: a read-only, project-scoped query on the graph in Neo4j. */
export interface GraphQueryResponse {
  project: string;
  cypher: string;
  explanation: string | null;
  generated: boolean; // written by the model from a question
  result?: {
    columns: string[];
    rows: unknown[][];
    nodes: KGNode[];
    edges: KGEdge[];
    records: number;
    truncated: boolean;
    hidden: number;
    ms: number;
  };
}

/** MCP (Model Context Protocol) knowledge tools: what `tools/list` returns and the ready-made client settings. */
export interface McpTool {
  name: string;
  description: string;
  inputSchema: {
    type: "object";
    properties: Record<string, McpProp>;
    required?: string[];
  };
}
export interface McpProp {
  type: "string" | "integer" | "number" | "boolean" | "array";
  description?: string;
  enum?: string[];
  items?: { type: string; enum?: string[] };
  minimum?: number;
  maximum?: number;
}
export interface McpCallResult {
  content: { type: "text"; text: string }[];
  isError: boolean;
}
export interface McpConfig {
  project: string;
  url: string;
  auth_required: boolean;
  tools: string[];
  clients: {
    client: string;
    file: string;
    config: unknown;
    command?: string;
  }[];
  note: string;
}

/** What ingesting one document produced (GET /knowledge/source/captured): text, chunks, links, Neo4j node. */
export interface CapturedChunk {
  id: string;
  ordinal: number;
  heading: string;
  text: string;
  chars: number;
  start: number | null; // place in the document text; null when it could not be located
  end: number | null;
  embedded_text: string;
  embedded: boolean;
  model: string | null;
  dimensions: number;
  vector_head: number[];
  norm: number | null;
}
export interface CapturedLink {
  id: string;
  type: string;
  direction: "in" | "out";
  status: "verified" | "proposed" | "rejected";
  origin: string;
  evidence: string;
  other: {
    external_id: string;
    kind: string;
    title: string;
    node_type: string;
  } | null;
}
export interface CapturedSource {
  project: string;
  source: {
    id: string;
    kind: string;
    external_id: string;
    title: string;
    uri: string | null;
    origin: string;
    version: number;
    state: string;
    error: string | null;
    node_type: string;
    content_hash: string;
  };
  metadata: Record<string, unknown>;
  content: string;
  length: number;
  truncated: boolean;
  chunks: CapturedChunk[];
  covered_chars: number;
  links: CapturedLink[];
  neo4j: {
    enabled: boolean;
    error?: string;
    node?: {
      labels: string[];
      properties: Record<string, unknown>;
      relationships: {
        type: string;
        outgoing: boolean;
        status: string;
        origin: string;
        other: string;
        kind: string;
        title: string;
      }[];
    } | null;
  };
}
