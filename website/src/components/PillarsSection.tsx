import React from "react";
import { ShieldCheckIcon, LockIcon, NetworkIcon, CheckCircleIcon } from "./Icons";

export function PillarsSection() {
  const pillars = [
    {
      icon: ShieldCheckIcon,
      badge: "Core Invariant",
      badgeColor: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
      title: "Independent Deterministic Verification",
      subtitle: "The model's claim of 'fixed' proves nothing. Machine checks decide completion.",
      points: [
        "Automated baseline test execution records pre-existing errors before LLM makes changes.",
        "Post-edit verification runs real pytest / test runners inside isolated container environments.",
        "If tests fail, Harnix feeds diagnostic tracebacks back into an automated recovery loop.",
        "Tasks that cannot be verified by machine checks are explicitly stamped completed (not verified).",
      ],
      techBadge: "verification/engine.py",
    },
    {
      icon: LockIcon,
      badge: "Containment",
      badgeColor: "bg-blue-500/10 text-blue-400 border-blue-500/20",
      title: "Defense-in-Depth Policy & Sandboxing",
      subtitle: "No open shell, no credential access, and zero uncontrolled repository modifications.",
      points: [
        "Raw shell execution (/bin/sh, cmd.exe, pipes, subshells) is categorically blocked.",
        "Commands are tokenized into strict argv lists authorized by safety policies.",
        "Tasks run inside isolated Docker sandboxes configured with zero network egress by default.",
        "Only execution/apply.py can write back to your repository, guarded by human approval.",
      ],
      techBadge: "policies/policy.py + Docker",
    },
    {
      icon: NetworkIcon,
      badge: "Enterprise Context",
      badgeColor: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
      title: "Spec-to-Code Knowledge Graph & MCP",
      subtitle: "Bridges upstream business requirements and Jira stories directly to code AST symbols.",
      points: [
        "Ingests BRDs, epics, features, user stories, and technical specifications.",
        "Maintains a strict Neo4j ontology tracking provenance: BRD -> Requirement -> Feature -> Story -> Code.",
        "Standardized Model Context Protocol (MCP) tools allow agents to query requirements with Cypher guards.",
        "Assembles reproducible context bundles with quote-verification to eliminate hallucinated specs.",
      ],
      techBadge: "Neo4j + pgvector + MCP",
    },
  ];

  return (
    <section id="pillars" className="py-24 relative bg-grid-pattern">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-xs font-mono text-indigo-400 mb-4">
            <ShieldCheckIcon className="w-3.5 h-3.5" />
            <span>Architectural Pillars</span>
          </div>
          <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            Engineered for safety and ground truth.
          </h2>
          <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
            Harnix is not a chatbot wrapper. It is an industrial-grade autonomous harness designed to turn probabilistic LLM reasoning into deterministic, verified software delivery.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {pillars.map((p, idx) => {
            const Icon = p.icon;
            return (
              <div
                key={idx}
                className="glass-panel glass-panel-hover p-8 rounded-3xl flex flex-col justify-between border border-white/10"
              >
                <div>
                  <div className="flex items-center justify-between gap-4 mb-6">
                    <div className="w-12 h-12 rounded-xl bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
                      <Icon className="w-6 h-6" />
                    </div>
                    <span
                      className={`px-2.5 py-1 rounded-full text-[11px] font-mono border ${p.badgeColor}`}
                    >
                      {p.badge}
                    </span>
                  </div>

                  <h3 className="text-xl font-bold text-white mb-2 tracking-tight">
                    {p.title}
                  </h3>
                  <p className="text-slate-400 text-xs sm:text-sm mb-6 leading-relaxed">
                    {p.subtitle}
                  </p>

                  <div className="space-y-3 pt-4 border-t border-white/5">
                    {p.points.map((pt, pIdx) => (
                      <div key={pIdx} className="flex items-start gap-2.5 text-xs text-slate-300">
                        <CheckCircleIcon className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
                        <span>{pt}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-8 pt-4 border-t border-white/5 flex items-center justify-between text-[11px] font-mono text-slate-400">
                  <span>Engine module:</span>
                  <span className="text-slate-300 bg-white/5 px-2 py-0.5 rounded border border-white/5">
                    {p.techBadge}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

