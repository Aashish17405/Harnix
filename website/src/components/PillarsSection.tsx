import React from "react";

export function PillarsSection() {
  const specs = [
    {
      subsystem: "verification/engine.py",
      title: "Deterministic Test Oracles",
      tag: "CORE INVARIANT",
      tagColor: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
      summary: "Model self-evaluation is fundamentally unreliable. Harnix uses independent machine checks as the sole gate of completion.",
      details: [
        "Establishes pre-mutation invariant: Test suite must execute and fail on the target symptom before editing begins.",
        "Post-edit verification runs pytest/unittest in an isolated environment without agent intervention.",
        "Failing verification outputs structured diagnostic traces back into a bounded recovery loop.",
        "Tasks that cannot be verified by machine checks are explicitly recorded as UNVERIFIED.",
      ],
    },
    {
      subsystem: "policies/commands.py",
      title: "Argv Tokenized Command Policy",
      tag: "DEFENSE IN DEPTH",
      tagColor: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
      summary: "Raw shell access (/bin/sh, bash, cmd.exe) is blocked to eliminate prompt injection and unintended script execution.",
      details: [
        "Commands are tokenized into explicit argv vectors using POSIX-compliant lexical parsers.",
        "Shell chaining and redirect operators (|, &, ;, >, <, `) are strictly prohibited.",
        "Command allowlists restrict execution strictly to safe compilers, linters, and test runners.",
        "Directory traversal (../) beyond the isolated repository root triggers an immediate policy abort.",
      ],
    },
    {
      subsystem: "execution/docker.py",
      title: "Ephemeral Container Isolation",
      tag: "SANDBOX BOUNDARY",
      tagColor: "bg-blue-500/10 text-blue-400 border-blue-500/20",
      summary: "LLMs never touch your actual working tree. Changes occur inside disposable Linux containers.",
      details: [
        "Clones clean repository state against baseline git commit hash inside a Docker sandbox.",
        "Runs with --network none to prevent secret exfiltration or unauthorized dependency fetching.",
        "Runs as an unprivileged non-root user with read-only root mounts and disposable tmpfs workspaces.",
        "Only execution/apply.py can stage changes back to the host, requiring explicit developer approval.",
      ],
    },
    {
      subsystem: "knowledge/graph.py",
      title: "Spec-to-Code Graph via MCP",
      tag: "KNOWLEDGE TRACE",
      tagColor: "bg-purple-500/10 text-purple-400 border-purple-500/20",
      summary: "Grounds agent reasoning in upstream business requirements instead of guessed database schemas.",
      details: [
        "Ingests BRDs, technical specifications, and Jira user stories into Neo4j graph relationships.",
        "Maintains provenance: BRD -> Feature -> User Story -> Acceptance Criteria -> Code Symbol.",
        "Provides Model Context Protocol (MCP) endpoints for agents to query exact requirements before writing code.",
        "Eliminates hallucinated product rules by validating citations against stored document chunks.",
      ],
    },
  ];

  return (
    <section id="architecture" className="py-20 bg-[#0B0D12] border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Section Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>01 // System Architecture</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-white">
              Engineered with deterministic bounds.
            </h2>
          </div>
          <p className="text-neutral-400 font-mono text-xs max-w-md">
            Four architectural subsystems ensure model hallucinations are contained before they reach host files or production repos.
          </p>
        </div>

        {/* 4-Column Technical Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {specs.map((item, idx) => (
            <div
              key={idx}
              className="sys-panel p-6 space-y-4 border border-white/10 flex flex-col justify-between"
            >
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2 border-b border-white/5 pb-3">
                  <span className="font-mono text-xs text-emerald-400 font-semibold">
                    {item.subsystem}
                  </span>
                  <span className={`sys-badge ${item.tagColor}`}>
                    {item.tag}
                  </span>
                </div>

                <h3 className="text-lg font-bold text-white font-mono">
                  {item.title}
                </h3>
                
                <p className="text-neutral-300 text-xs sm:text-sm leading-relaxed">
                  {item.summary}
                </p>

                <div className="space-y-2 pt-2">
                  {item.details.map((d, dIdx) => (
                    <div key={dIdx} className="flex items-start gap-2 text-xs text-neutral-400 font-mono">
                      <span className="text-emerald-400 select-none">&bull;</span>
                      <span>{d}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-3 border-t border-white/5 flex items-center justify-between font-mono text-[11px] text-neutral-400">
                <span>COMPONENT {idx + 1} OF 4</span>
                <span className="text-neutral-300">ACTIVE IN HARNIX v0.5.0</span>
              </div>
            </div>
          ))}
        </div>

      </div>
    </section>
  );
}
