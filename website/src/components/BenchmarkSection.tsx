import React from "react";

export function BenchmarkSection() {
  const models = [
    {
      name: "gpt-6-luna",
      status: "SHIPPED DEFAULT",
      statusColor: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
      verifiedRate: "4 / 4 (100%)",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "12.8s",
      meanCost: "$0.0028",
      analysis: "Most cost-efficient model. Handled structured file edit tool calls with zero syntactic retries.",
    },
    {
      name: "gpt-5.6-luna",
      status: "FAST PROFILE",
      statusColor: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
      verifiedRate: "4 / 4 (100%)",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "11.6s",
      meanCost: "$0.0055",
      analysis: "Fastest end-to-end turnaround. Reliable function calling on unit test debugging tasks.",
    },
    {
      name: "gpt-5.4-nano",
      status: "BUDGET BASELINE",
      statusColor: "bg-neutral-800 text-neutral-300 border-neutral-700",
      verifiedRate: "4 / 4 (100%)",
      falseClaims: "0",
      toolFailures: "3",
      meanTime: "16.0s",
      meanCost: "$0.0077",
      analysis: "Lower reasoning budget led to 3 tool retry loops before succeeding. Harness error feedback corrected it.",
    },
    {
      name: "gpt-5.4-mini",
      status: "COMPLEX REFACTOR",
      statusColor: "bg-purple-500/10 text-purple-400 border-purple-500/20",
      verifiedRate: "4 / 4 (100%)",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "9.8s",
      meanCost: "$0.0214",
      analysis: "High reasoning capacity. Best suited for multi-file refactors spanning complex AST dependencies.",
    },
  ];

  return (
    <section id="benchmarks" className="py-20 bg-[#0E1017] border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>04 // Empirical Benchmarks</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-white">
              Measured model economics &amp; reliability.
            </h2>
          </div>
          <p className="text-neutral-400 font-mono text-xs max-w-md">
            All benchmarks executed against real repository bug test suites in isolated Docker containers with automated token and cost tracking.
          </p>
        </div>

        {/* Benchmark Table */}
        <div className="sys-panel overflow-hidden border border-neutral-700/80 mb-6">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse font-mono text-xs">
              <thead>
                <tr className="border-b border-white/10 bg-neutral-900 text-neutral-400 text-[11px]">
                  <th className="py-3 px-5 font-bold uppercase">Model Name</th>
                  <th className="py-3 px-5 font-bold uppercase">Verified Rate</th>
                  <th className="py-3 px-5 font-bold uppercase">False Claims</th>
                  <th className="py-3 px-5 font-bold uppercase">Tool Retries</th>
                  <th className="py-3 px-5 font-bold uppercase">Mean Time</th>
                  <th className="py-3 px-5 font-bold uppercase">Cost / Fix</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {models.map((m, idx) => (
                  <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                    <td className="py-4 px-5">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-white text-sm">{m.name}</span>
                        <span className={`sys-badge text-[10px] ${m.statusColor}`}>
                          {m.status}
                        </span>
                      </div>
                      <div className="text-[11px] text-neutral-400 font-sans mt-1 max-w-md leading-relaxed">
                        {m.analysis}
                      </div>
                    </td>
                    <td className="py-4 px-5 text-emerald-400 font-semibold align-top">
                      {m.verifiedRate}
                    </td>
                    <td className="py-4 px-5 text-neutral-300 font-semibold align-top">
                      {m.falseClaims}
                    </td>
                    <td className="py-4 px-5 text-neutral-300 align-top">
                      {m.toolFailures}
                    </td>
                    <td className="py-4 px-5 text-neutral-300 align-top">
                      {m.meanTime}
                    </td>
                    <td className="py-4 px-5 text-white font-bold align-top">
                      {m.meanCost}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Footnote / Systems Note */}
        <div className="p-4 rounded bg-neutral-900/60 border border-white/5 font-mono text-xs text-neutral-400 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <span className="text-white font-semibold">Key Finding:</span> False claim rate is 0 across all runs because Harnix never queries model opinion on completion.
          </div>
          <div className="text-[11px] text-neutral-400">
            Hardware: 8 vCPU &bull; Docker Engine 27.x &bull; Python 3.12
          </div>
        </div>

      </div>
    </section>
  );
}
