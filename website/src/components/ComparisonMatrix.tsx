import React from "react";
import { CheckIcon, AlertTriangleIcon, ShieldCheckIcon } from "./Icons";

export function ComparisonMatrix() {
  const comparisons = [
    {
      feature: "Task Completion Model",
      naive: "Trusts LLM when it says 'I am finished' or stops calling tools.",
      harnix: "Model claim is untrusted. Completion requires deterministic machine verification to pass.",
      highlight: true,
    },
    {
      feature: "Terminal Execution Security",
      naive: "Raw shell access (/bin/sh, bash) with unrestricted piping and subshells.",
      harnix: "Strict tokenized argv allowlist (policies/commands.py). Arbitrary shells are denied.",
      highlight: true,
    },
    {
      feature: "Workspace Isolation",
      naive: "Edits host repository in-place; uncommitted work can be corrupted or deleted.",
      harnix: "Ephemeral copy + baseline git hash inside Docker sandbox (--network none). Atomic apply-back.",
      highlight: false,
    },
    {
      feature: "Upstream Spec Context",
      naive: "Limited to raw grep / prompt stuffing with no concept of product requirements.",
      harnix: "Neo4j Knowledge Graph + pgvector hybrid search connecting BRDs & User Stories to AST symbols via MCP.",
      highlight: true,
    },
    {
      feature: "Human-in-the-Loop Approval",
      naive: "Either all-or-nothing autonomy or constant annoying alerts for trivial reads.",
      harnix: "Granular policy engine: read operations are free; risky writes and commands require exact SHA256 approval.",
      highlight: false,
    },
    {
      feature: "Auditability & Event Sourcing",
      naive: "Transient terminal stdout or in-memory chat logs that disappear on reload.",
      harnix: "Every decision, tool result, and policy verdict is an immutable, secret-redacted database row.",
      highlight: false,
    },
  ];

  return (
    <section id="comparison" className="py-24 relative bg-grid-pattern">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-xs font-mono text-blue-400 mb-4">
            <ShieldCheckIcon className="w-3.5 h-3.5" />
            <span>Architectural Contrast</span>
          </div>
          <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            Why naive AI agents fail in production.
          </h2>
          <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
            The difference between an AI demo and an engineering platform is what happens when the model hallucinates.
          </p>
        </div>

        {/* Comparison Table */}
        <div className="glass-panel rounded-3xl overflow-hidden border border-white/10 shadow-2xl max-w-5xl mx-auto">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs sm:text-sm">
              <thead>
                <tr className="border-b border-white/10 bg-[#0E121B]">
                  <th className="py-4 px-6 font-mono text-xs uppercase tracking-wider text-slate-400 font-bold w-1/4">
                    Capability
                  </th>
                  <th className="py-4 px-6 font-mono text-xs uppercase tracking-wider text-slate-400 font-bold w-3/8">
                    Naive Agent Wrappers
                  </th>
                  <th className="py-4 px-6 font-mono text-xs uppercase tracking-wider text-blue-400 font-bold w-3/8 bg-blue-950/20">
                    Harnix Platform
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 font-normal">
                {comparisons.map((row, idx) => (
                  <tr
                    key={idx}
                    className={`transition-colors hover:bg-white/[0.02] ${
                      row.highlight ? "bg-white/[0.01]" : ""
                    }`}
                  >
                    <td className="py-5 px-6 font-mono font-semibold text-white">
                      {row.feature}
                    </td>
                    <td className="py-5 px-6 text-slate-400 leading-relaxed">
                      <div className="flex items-start gap-2">
                        <AlertTriangleIcon className="w-4 h-4 text-amber-400/80 flex-shrink-0 mt-0.5" />
                        <span>{row.naive}</span>
                      </div>
                    </td>
                    <td className="py-5 px-6 text-slate-200 leading-relaxed bg-blue-950/10 border-l border-r border-blue-500/10">
                      <div className="flex items-start gap-2">
                        <CheckIcon className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
                        <span className="font-medium text-white">{row.harnix}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

