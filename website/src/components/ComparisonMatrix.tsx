import React from "react";

export function ComparisonMatrix() {
  const threats = [
    {
      hazard: "Hallucinated Task Completion",
      unconstrained: "Assumes completion as soon as LLM stops tool calling or emits 'Fixed!'. Uncaught regressions ship to git.",
      harnix: "Model assertions are ignored. Task completion strictly requires deterministic exit code 0 from test suite.",
      severity: "CRITICAL",
    },
    {
      hazard: "Shell Injection via Prompt Hijack",
      unconstrained: "Executes unconstrained /bin/sh or cmd.exe. Injected prompts can invoke curl, bash pipes, or background daemons.",
      harnix: "POSIX argv tokenization. Arbitrary shell operators (|, &, ;, >, `) are rejected prior to execution.",
      severity: "HIGH",
    },
    {
      hazard: "Host Working Tree Corruption",
      unconstrained: "Edits host repository directly. In-progress unstaged changes can be overwritten, mutated, or deleted.",
      harnix: "Disposable clone inside ephemeral Docker container. Real repository files remain untouched until explicit apply-back.",
      severity: "HIGH",
    },
    {
      hazard: "Secret & Credential Exfiltration",
      unconstrained: "Process inherits host environment variables (AWS keys, GitHub tokens, database passwords).",
      harnix: "Environment scrubbed. Docker sandbox runs with --network none. Zero outbound sockets permitted.",
      severity: "CRITICAL",
    },
    {
      hazard: "Runaway Token / Cost Loops",
      unconstrained: "Unbounded retries against hallucinations rapidly exhaust token allocations and budget quotas.",
      harnix: "Per-session step caps, token cost accounting, and immediate circuit-breaking on repeated policy violations.",
      severity: "MEDIUM",
    },
    {
      hazard: "Business Spec Hallucination",
      unconstrained: "Agent guesses product logic from sparse prompts or raw grep snippets.",
      harnix: "Neo4j Knowledge Graph traces code symbols upstream to exact Jira user stories and acceptance criteria.",
      severity: "MEDIUM",
    },
  ];

  return (
    <section id="policy" className="py-20 bg-[#0E1017] border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>02 // Threat Model &amp; Policy Bounds</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-white">
              Failure modes and containment mitigations.
            </h2>
          </div>
          <p className="text-neutral-400 font-mono text-xs max-w-md">
            Comparing how unconstrained AI developer tools behave versus Harnix&apos;s hardened execution boundary.
          </p>
        </div>

        {/* Matrix Table */}
        <div className="sys-panel overflow-hidden border border-neutral-700/80">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse font-mono text-xs">
              <thead>
                <tr className="border-b border-white/10 bg-neutral-900 text-neutral-400 text-[11px]">
                  <th className="py-3 px-5 font-bold uppercase w-1/4">
                    Failure Mode / Threat
                  </th>
                  <th className="py-3 px-5 font-bold uppercase w-3/8 text-amber-400/90">
                    Unconstrained AI Agent
                  </th>
                  <th className="py-3 px-5 font-bold uppercase w-3/8 text-emerald-400 bg-emerald-950/20">
                    Harnix Hardened Harness
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 font-sans">
                {threats.map((row, idx) => (
                  <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                    <td className="py-4 px-5 font-mono text-xs text-white align-top">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold">{row.hazard}</span>
                      </div>
                      <span className="inline-block mt-1 text-[10px] font-mono text-neutral-400 px-1.5 py-0.5 rounded bg-neutral-800">
                        RISK: {row.severity}
                      </span>
                    </td>
                    <td className="py-4 px-5 text-neutral-300 text-xs leading-relaxed align-top">
                      <div className="flex items-start gap-2">
                        <span className="text-amber-400 font-mono font-bold select-none">&times;</span>
                        <span>{row.unconstrained}</span>
                      </div>
                    </td>
                    <td className="py-4 px-5 text-neutral-200 text-xs leading-relaxed align-top bg-emerald-950/10 border-l border-white/5">
                      <div className="flex items-start gap-2">
                        <span className="text-emerald-400 font-mono font-bold select-none">&radic;</span>
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
