import React from "react";
import { CheckCircle, Lightning, CurrencyDollar } from "@phosphor-icons/react/dist/ssr";

export function BenchmarkSection() {
  const models = [
    {
      name: "gpt-6-luna",
      tag: "DEFAULT BALANCED",
      verifiedRate: 100,
      turnaround: "12.8s",
      cost: "$0.0028",
      highlight: "Zero tool retries",
      barColor: "bg-emerald-500",
    },
    {
      name: "gpt-5.6-luna",
      tag: "FAST PROFILE",
      verifiedRate: 100,
      turnaround: "11.6s",
      cost: "$0.0055",
      highlight: "Fastest turnaround",
      barColor: "bg-cyan-500",
    },
    {
      name: "gpt-5.4-nano",
      tag: "BUDGET BASELINE",
      verifiedRate: 100,
      turnaround: "16.0s",
      cost: "$0.0077",
      highlight: "Self-corrected retries",
      barColor: "bg-amber-500",
    },
    {
      name: "gpt-5.4-mini",
      tag: "REASONING TIER",
      verifiedRate: 100,
      turnaround: "9.8s",
      cost: "$0.0214",
      highlight: "Deep multi-file AST",
      barColor: "bg-purple-500",
    },
  ];

  return (
    <section id="benchmarks" className="py-24 bg-background border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>04 // Empirical Benchmarks</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Production evaluation matrix.
            </h2>
          </div>
          <p className="text-neutral-600 dark:text-neutral-400 font-mono text-xs max-w-md">
            Verified rate on Docker sandbox runtime across LLM reasoning tiers.
          </p>
        </div>

        {/* Visual Benchmark Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {models.map((m) => (
            <div
              key={m.name}
              className="sys-panel p-5 border border-panel-border rounded-2xl bg-panel shadow-sm flex flex-col justify-between hover:border-neutral-400 dark:hover:border-neutral-700 transition-all duration-200"
            >
              <div>
                <div className="flex items-center justify-between mb-3">
                  <span className="font-mono font-bold text-sm text-foreground">{m.name}</span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-md border border-panel-border bg-panel-subtle text-neutral-600 dark:text-neutral-400">
                    {m.tag}
                  </span>
                </div>

                {/* Progress Visualizer */}
                <div className="my-4">
                  <div className="flex items-center justify-between text-xs font-mono mb-1.5">
                    <span className="text-neutral-500 dark:text-neutral-400">Verified Rate</span>
                    <span className="font-bold text-emerald-600 dark:text-emerald-400">{m.verifiedRate}%</span>
                  </div>
                  <div className="w-full h-2 rounded-full bg-neutral-200 dark:bg-neutral-800 overflow-hidden">
                    <div className={`h-full rounded-full ${m.barColor}`} style={{ width: `${m.verifiedRate}%` }} />
                  </div>
                </div>

                <div className="space-y-2 py-2 border-t border-panel-border font-mono text-xs">
                  <div className="flex justify-between items-center">
                    <span className="text-neutral-500 dark:text-neutral-400 flex items-center gap-1.5">
                      <Lightning size={14} className="text-amber-500" /> Turnaround
                    </span>
                    <span className="font-semibold text-foreground">{m.turnaround}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-neutral-500 dark:text-neutral-400 flex items-center gap-1.5">
                      <CurrencyDollar size={14} className="text-emerald-500" /> Cost / Task
                    </span>
                    <span className="font-semibold text-foreground">{m.cost}</span>
                  </div>
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-panel-border flex items-center gap-1.5 text-[11px] font-mono text-emerald-600 dark:text-emerald-400">
                <CheckCircle size={14} weight="duotone" />
                <span>{m.highlight}</span>
              </div>
            </div>
          ))}
        </div>

      </div>
    </section>
  );
}
