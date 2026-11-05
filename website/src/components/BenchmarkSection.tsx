import React from "react";
import { CpuIcon, CheckCircleIcon } from "./Icons";

export function BenchmarkSection() {
  const models = [
    {
      name: "gpt-6-luna",
      badge: "Shipped Default",
      isDefault: true,
      priceIn: "$0.10",
      priceOut: "$0.50",
      completed: "4 / 4",
      verified: "4 / 4",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "12.8s",
      totalCost: "$0.0028",
      recommendation: "Cheapest & most reliable with function tools. Zero tool failures.",
    },
    {
      name: "gpt-5.6-luna",
      badge: "Fast Reasoning",
      isDefault: false,
      priceIn: "$0.20",
      priceOut: "$1.20",
      completed: "4 / 4",
      verified: "4 / 4",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "11.6s",
      totalCost: "$0.0055",
      recommendation: "Low latency, reliable tool handling across repair benchmarks.",
    },
    {
      name: "gpt-5.4-nano",
      badge: "Budget Baseline",
      isDefault: false,
      priceIn: "$0.20",
      priceOut: "$1.25",
      completed: "4 / 4",
      verified: "4 / 4",
      falseClaims: "0",
      toolFailures: "3",
      meanTime: "16.0s",
      totalCost: "$0.0077",
      recommendation: "Higher tool retry rate; requires recovery loops to pass.",
    },
    {
      name: "gpt-5.4-mini",
      badge: "Complex Tasks",
      isDefault: false,
      priceIn: "$0.75",
      priceOut: "$4.50",
      completed: "4 / 4",
      verified: "4 / 4",
      falseClaims: "0",
      toolFailures: "0",
      meanTime: "9.8s",
      totalCost: "$0.0214",
      recommendation: "Best for harder, multi-step code refactors and larger repos.",
    },
  ];

  return (
    <section id="benchmarks" className="py-24 relative bg-[#07080C] border-t border-b border-white/5">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-xs font-mono text-emerald-400 mb-4">
            <CpuIcon className="w-3.5 h-3.5" />
            <span>Measured Model Benchmarks</span>
          </div>
          <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            Tested for real in container sandboxes.
          </h2>
          <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
            All benchmarks run against real repository bug test suites through isolated Docker containers with budget accounting.
          </p>
        </div>

        {/* Benchmarks Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 max-w-6xl mx-auto">
          {models.map((m, idx) => (
            <div
              key={idx}
              className={`glass-panel p-6 rounded-2xl flex flex-col justify-between border ${
                m.isDefault
                  ? "border-blue-500/40 bg-blue-950/20 ring-1 ring-blue-500/20"
                  : "border-white/10"
              }`}
            >
              <div>
                <div className="flex items-center justify-between mb-4">
                  <span className="font-mono font-bold text-white text-base">
                    {m.name}
                  </span>
                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded ${
                      m.isDefault
                        ? "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                        : "bg-white/5 text-slate-400 border border-white/5"
                    }`}
                  >
                    {m.badge}
                  </span>
                </div>

                <div className="space-y-2.5 py-4 border-t border-b border-white/5 font-mono text-xs">
                  <div className="flex justify-between text-slate-400">
                    <span>Verified Rate:</span>
                    <span className="text-emerald-400 font-bold">{m.verified}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>False Claims:</span>
                    <span className="text-white font-bold">{m.falseClaims}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>Tool Failures:</span>
                    <span className={m.toolFailures === "0" ? "text-slate-300" : "text-yellow-400 font-bold"}>
                      {m.toolFailures}
                    </span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>Mean Task Time:</span>
                    <span className="text-white">{m.meanTime}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>Input / Output (1M):</span>
                    <span className="text-slate-300">{m.priceIn} / {m.priceOut}</span>
                  </div>
                </div>

                <div className="pt-4">
                  <div className="text-[11px] text-slate-400 mb-1">Total Benchmark Cost (4 tasks):</div>
                  <div className="text-2xl font-mono font-extrabold text-white">
                    {m.totalCost}
                  </div>
                </div>
              </div>

              <div className="mt-6 pt-3 border-t border-white/5 text-[11px] text-slate-400">
                {m.recommendation}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
