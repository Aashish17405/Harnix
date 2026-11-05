import type {
  ApplyResult,
  Approval,
  Diff,
  HarnixConfig,
  Health,
  EmbeddingMapData,
  BundleSummary,
  CoverageReport,
  ExtractJob,
  FindingsReport,
  MarkdownExport,
  GraphQueryResponse,
  McpConfig,
  KnowledgeJob,
  ProjectPipeline,
  StoredBundle,
  KGraph,
  KnowledgeSearchResult,
  Neighbors,
  KnowledgeBundle,
  KnowledgeEdgeRow,
  KnowledgeIngestResult,
  KnowledgeProject,
  CapturedSource,
  KnowledgeSource,
  MemoryNote,
  SkillList,
  Task,
  TaskCreate,
  TaskEvaluation,
  TaskList,
  Verification,
} from "./types";

const TOKEN_KEY = "forge.apiToken";

export const getToken = (): string => {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
};
export const setToken = (t: string) => {
  try {
    sessionStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* storage unavailable: token lives only for this page load */
  }
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: { field: string; message: string }[],
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { ...headers, ...(init.headers as object) },
    });
  } catch {
    throw new ApiError(
      0,
      "unreachable",
      "Cannot reach the Forge backend. Start it with `uvicorn forge.api.app:app_factory --factory` (see README).",
    );
  }
  if (!res.ok) {
    let body: {
      error?: {
        code: string;
        message: string;
        details?: { field: string; message: string }[];
      };
    } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      res.status,
      body.error?.code ?? "error",
      body.error?.message ?? `Request failed (${res.status})`,
      body.error?.details,
    );
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  health: () => request<Health>("/health"),
  config: () => request<HarnixConfig>("/config"),
  listTasks: (limit = 50, offset = 0) =>
    request<TaskList>(`/tasks?limit=${limit}&offset=${offset}`),
  getTask: (id: string) => request<Task>(`/tasks/${id}`),
  createTask: (body: TaskCreate) => post<Task>("/tasks", body),
  runTask: (id: string) => post<Task>(`/tasks/${id}/run`),
  cancelTask: (id: string) => post<Task>(`/tasks/${id}/cancel`),
  rerunTask: (id: string) => post<Task>(`/tasks/${id}/rerun`),
  diff: (id: string) => request<Diff>(`/tasks/${id}/diff`),
  verifications: (id: string) =>
    request<Verification[]>(`/tasks/${id}/verifications`),
  approvals: (id: string) => request<Approval[]>(`/tasks/${id}/approvals`),
  apply: (id: string, opts: { confirm?: boolean; dry_run?: boolean }) =>
    post<ApplyResult>(`/tasks/${id}/apply`, {
      confirm: false,
      dry_run: false,
      ...opts,
    }),
  decide: (approvalId: string, decision: "approve" | "reject", note?: string) =>
    post<Approval>(`/approvals/${approvalId}/decision`, {
      decision,
      note: note || undefined,
    }),
  skills: () => request<SkillList>("/skills"),
  knowledgeProjects: () =>
    request<{ projects: KnowledgeProject[] }>("/knowledge/projects"),
  addDocument: (body: {
    project: string;
    kind: string;
    filename?: string;
    text?: string;
    data_base64?: string;
    external_id?: string;
    title?: string;
    links?: Record<string, string[]>;
  }) => post<KnowledgeIngestResult>("/knowledge/documents", body),
  indexDocuments: (project: string, path: string) =>
    post<KnowledgeIngestResult>("/knowledge/ingest", {
      project,
      source: "documents",
      path,
    }),
  /** Background versions: every step is reported on the job (the pipeline shows it live). */
  startUpload: (body: Record<string, unknown>) =>
    post<KnowledgeJob>("/knowledge/documents", { ...body, background: true }),
  startIndex: (project: string, path: string) =>
    post<KnowledgeJob>("/knowledge/ingest", {
      project,
      source: "documents",
      path,
      background: true,
    }),
  /** Index a folder of documents, a Git repository or a (mock) Jira JSON export, as a background job. */
  startIngest: (
    project: string,
    source: "documents" | "repository" | "jira_mock",
    path: string,
  ) =>
    post<KnowledgeJob>("/knowledge/ingest", {
      project,
      source,
      path,
      background: true,
    }),
  startRebuild: (project: string) =>
    post<KnowledgeJob>(
      `/knowledge/rebuild?project=${encodeURIComponent(project)}`,
      {},
    ),
  knowledgeJob: (id: string) =>
    request<KnowledgeJob>(`/knowledge/jobs/${encodeURIComponent(id)}`),
  knowledgeJobs: (project: string) =>
    request<{ jobs: KnowledgeJob[] }>(
      `/knowledge/jobs?project=${encodeURIComponent(project)}`,
    ),
  /** Wait for a job, reporting each poll; the report on success, an error with the job's reason otherwise. */
  async waitJob(
    job: KnowledgeJob,
    onUpdate?: (j: KnowledgeJob) => void,
  ): Promise<KnowledgeJob> {
    let j = job;
    onUpdate?.(j);
    while (j.status === "running") {
      await new Promise((r) => setTimeout(r, 700));
      j = await api.knowledgeJob(j.id);
      onUpdate?.(j);
    }
    if (j.status === "failed") throw new Error(j.error ?? "The job failed");
    return j;
  },
  indexRepository: (project: string, path: string) =>
    post<KnowledgeIngestResult>("/knowledge/ingest", {
      project,
      source: "repository",
      path,
    }),
  knowledgeEmbeddings: (project: string) =>
    request<EmbeddingMapData>(
      `/knowledge/embeddings?project=${encodeURIComponent(project)}`,
    ),
  chunkNeighbors: (project: string, chunkId: string) =>
    request<Neighbors>(
      `/knowledge/chunks/${encodeURIComponent(chunkId)}/neighbors?project=${encodeURIComponent(project)}`,
    ),
  knowledgeSearch: (project: string, q: string) =>
    request<KnowledgeSearchResult>(
      `/knowledge/search?project=${encodeURIComponent(project)}&q=${encodeURIComponent(q)}`,
    ),
  knowledgeGraph: (
    project: string,
    bundle?: string,
    onlyBundle = false,
    view: "documents" | "concepts" | "all" = "documents",
  ) =>
    request<KGraph>(
      `/knowledge/graph?project=${encodeURIComponent(project)}${bundle ? `&bundle=${encodeURIComponent(bundle)}` : ""}${onlyBundle ? "&only_bundle=true" : ""}${view !== "documents" ? `&view=${view}` : ""}`,
    ),
  startExtract: (project: string) =>
    post<ExtractJob>("/knowledge/extract", { project }),
  extractStatus: (id: string) =>
    request<ExtractJob>(`/knowledge/extract/${encodeURIComponent(id)}`),
  /** Have the model propose the links documents imply to other items (a background job). */
  startLink: (project: string) =>
    post<KnowledgeJob>("/knowledge/link", { project }),
  knowledgeFindings: (project: string) =>
    request<FindingsReport>(
      `/knowledge/findings?project=${encodeURIComponent(project)}`,
    ),
  knowledgeCoverage: (project: string, path?: string) =>
    request<CoverageReport>(
      `/knowledge/coverage?project=${encodeURIComponent(project)}${path ? `&path=${encodeURIComponent(path)}` : ""}`,
    ),
  exportMarkdown: (project: string, path: string) =>
    post<MarkdownExport>("/knowledge/export", { project, path }),
  knowledgeSource: (project: string, ref: string) =>
    request<KnowledgeSource>(
      `/knowledge/source?project=${encodeURIComponent(project)}&ref=${encodeURIComponent(ref)}`,
    ),
  knowledgeCaptured: (project: string, ref: string) =>
    request<CapturedSource>(
      `/knowledge/source/captured?project=${encodeURIComponent(project)}&ref=${encodeURIComponent(ref)}`,
    ),
  knowledgeBundle: (project: string, instructions: string) =>
    post<KnowledgeBundle>("/knowledge/bundles", { project, instructions }),
  knowledgeEdges: (project: string, status: string) =>
    request<{ edges: KnowledgeEdgeRow[] }>(
      `/knowledge/edges?project=${encodeURIComponent(project)}&status=${status}`,
    ),
  decideEdge: (id: string, decision: "promote" | "reject") =>
    post<{ edge_id: string; status: string }>(
      `/knowledge/edges/${id}/${decision}`,
    ),
  bundleText: (id: string) =>
    request<{ text: string }>(
      `/knowledge/bundles/${encodeURIComponent(id)}?format=text`,
    ),
  knowledgeBundles: (project: string) =>
    request<{ project: string; bundles: BundleSummary[] }>(
      `/knowledge/bundles?project=${encodeURIComponent(project)}`,
    ),
  /** One MCP JSON-RPC message to the knowledge server, scoped to `project` (the same endpoint external harnesses use). */
  mcp: <T = unknown>(project: string, method: string, params: object = {}) =>
    post<{ result?: T; error?: { code: number; message: string } }>(
      `/knowledge/mcp?project=${encodeURIComponent(project)}`,
      { jsonrpc: "2.0", id: Date.now(), method, params },
    ),
  mcpConfig: (project: string) =>
    request<McpConfig>(
      `/knowledge/mcp/config?project=${encodeURIComponent(project)}`,
    ),
  graphQuery: (project: string, q: { cypher?: string; question?: string }) =>
    post<GraphQueryResponse>("/knowledge/query", { project, ...q }),
  storedBundle: (id: string) =>
    request<StoredBundle>(`/knowledge/bundles/${encodeURIComponent(id)}`),
  knowledgePipeline: (project: string) =>
    request<ProjectPipeline>(
      `/knowledge/pipeline?project=${encodeURIComponent(project)}`,
    ),
  reviewKind: (sourceId: string, kind: string) =>
    post<{ source_id: string; external_id: string; kind: string }>(
      `/knowledge/review/${encodeURIComponent(sourceId)}`,
      { kind },
    ),
  taskEvaluation: (taskId: string) =>
    request<TaskEvaluation>(`/knowledge/tasks/${taskId}/evaluation`),
  reviewCriterion: (
    taskId: string,
    criterion: string,
    verdict: "pass" | "fail" | "unsure",
    note: string,
  ) =>
    post<TaskEvaluation>(`/knowledge/tasks/${taskId}/evaluation`, {
      criterion,
      verdict,
      note,
    }),
  memory: (repo: string) =>
    request<MemoryNote[]>(`/memory?repo_path=${encodeURIComponent(repo)}`),
  addMemory: (repo: string, content: string) =>
    post<MemoryNote>("/memory", { repo_path: repo, content }),
  deleteMemory: (repo: string, id: string) =>
    request<{ deleted: string }>(
      `/memory/${encodeURIComponent(id)}?repo_path=${encodeURIComponent(repo)}`,
      { method: "DELETE" },
    ),
};

/** An attached image as a blob URL (the token goes in a header, never in the URL). */
export async function fetchImageUrl(
  taskId: string,
  index: number,
): Promise<string> {
  const token = getToken();
  const res = await fetch(`/api/tasks/${taskId}/images/${index}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok)
    throw new ApiError(res.status, "image", `Could not load image ${index}`);
  return URL.createObjectURL(await res.blob());
}

export const reportUrl = (id: string, format: "json" | "md") => {
  const t = getToken();
  return `/api/tasks/${id}/report?format=${format}${t ? `&token=${encodeURIComponent(t)}` : ""}`;
};

export const streamUrl = (id: string, after: number) => {
  const t = getToken();
  return `/api/tasks/${id}/events/stream?after=${after}${t ? `&token=${encodeURIComponent(t)}` : ""}`;
};
