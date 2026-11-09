"use client";

import React, { useState } from "react";
import {
  FileCode,
  ShieldCheck,
  CheckCircle,
  WarningOctagon,
  Cpu
} from "@phosphor-icons/react";

export function HowItWorksSection() {
  const [viewMode, setViewMode] = useState<"standard" | "comparison">("standard");

  return (
    <section id="how-it-works" className="py-20 sm:py-24 bg-panel-subtle border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl 2xl:max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-10 sm:mb-12 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>How Harnix Works</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Simple for everyone. Bulletproof under the hood.
            </h2>
          </div>
          
          {/* View Mode Toggle */}
          <div className="flex items-center gap-1.5 p-1 rounded-lg bg-panel border border-panel-border text-xs font-mono self-start md:self-auto w-full sm:w-auto">
            <button
              onClick={() => setViewMode("standard")}
              className={`flex-1 sm:flex-initial px-3 py-1.5 rounded-md transition-all cursor-pointer whitespace-nowrap text-center ${
                viewMode === "standard"
                  ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white font-semibold shadow-xs"
                  : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
              }`}
            >
              The 3-Step Flow
            </button>
            <button
              onClick={() => setViewMode("comparison")}
              className={`flex-1 sm:flex-initial px-3 py-1.5 rounded-md transition-all cursor-pointer whitespace-nowrap text-center ${
                viewMode === "comparison"
                  ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white font-semibold shadow-xs"
                  : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
              }`}
            >
              With vs. Without Harnix
            </button>
          </div>
        </div>

        {/* 3 Step Flow View */}
        {viewMode === "standard" && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            
            <div className="sys-panel p-6 sm:p-7 border border-panel-border rounded-2xl bg-panel shadow-sm flex flex-col justify-between">
              <div>
                <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-600 dark:text-purple-400 flex items-center justify-center mb-5 font-bold font-mono text-sm">
                  01
                </div>
                <h3 className="text-lg font-bold text-foreground mb-2">
                  1. You Assign a Task
                </h3>
                <p className="text-sm text-neutral-600 dark:text-neutral-400 leading-relaxed mb-4">
                  Give Harnix a ticket, bug report, or simple instruction. Harnix maps the problem directly to the relevant code files and existing unit tests.
                </p>
              </div>
              <div className="pt-4 border-t border-panel-border font-mono text-xs text-neutral-500 dark:text-neutral-400 flex items-center gap-1.5">
                <FileCode size={15} />
                <span>Zero manual context pasting</span>
              </div>
            </div>

            <div className="sys-panel p-6 sm:p-7 border border-panel-border rounded-2xl bg-panel shadow-sm flex flex-col justify-between">
              <div>
                <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-cyan-600 dark:text-cyan-400 flex items-center justify-center mb-5 font-bold font-mono text-sm">
                  02
                </div>
                <h3 className="text-lg font-bold text-foreground mb-2">
                  2. AI Works in a Safe Bubble
                </h3>
                <p className="text-sm text-neutral-600 dark:text-neutral-400 leading-relaxed mb-4">
                  The AI codes inside a disposable, ephemeral sandbox. It cannot touch your real files, leak API keys, or run risky commands on your computer.
                </p>
              </div>
              <div className="pt-4 border-t border-panel-border font-mono text-xs text-neutral-500 dark:text-neutral-400 flex items-center gap-1.5">
                <Cpu size={15} />
                <span>Host files &amp; secrets 100% safe</span>
              </div>
            </div>

            <div className="sys-panel p-6 sm:p-7 border border-panel-border rounded-2xl bg-panel shadow-sm flex flex-col justify-between">
              <div>
                <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 flex items-center justify-center mb-5 font-bold font-mono text-sm">
                  03
                </div>
                <h3 className="text-lg font-bold text-foreground mb-2">
                  3. Independent Test Proof
                </h3>
                <p className="text-sm text-neutral-600 dark:text-neutral-400 leading-relaxed mb-4">
                  Harnix ignores the AI when it says &quot;I fixed it!&quot;. It actually runs your tests. If they pass (exit code 0), you get a verified git patch ready to review.
                </p>
              </div>
              <div className="pt-4 border-t border-panel-border font-mono text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5 font-semibold">
                <ShieldCheck size={15} />
                <span>Guaranteed working patch</span>
              </div>
            </div>

          </div>
        )}

        {/* Side by Side Plain English Comparison View */}
        {viewMode === "comparison" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            
            <div className="sys-panel p-6 sm:p-8 border border-red-500/20 rounded-2xl bg-panel shadow-sm">
              <div className="flex items-center gap-2 mb-4 text-red-600 dark:text-red-400 font-mono font-bold text-xs uppercase tracking-wide">
                <WarningOctagon size={18} weight="fill" />
                <span>Standard Unconstrained AI Coding</span>
              </div>

              <div className="space-y-4 text-sm text-neutral-600 dark:text-neutral-400">
                <div className="p-3.5 rounded-xl bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Hallucinates Completion</div>
                  <div>The model stops talking and says &quot;Done!&quot;, but untested errors crash CI/CD or break production.</div>
                </div>

                <div className="p-3.5 rounded-xl bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Host Exposure</div>
                  <div>Runs scripts directly on your machine. Injected prompts can delete files or expose environment variables.</div>
                </div>

                <div className="p-3.5 rounded-xl bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Overwrites Your In-Progress Work</div>
                  <div>Directly edits your workspace before you have a chance to review what it did.</div>
                </div>
              </div>
            </div>

            <div className="sys-panel p-6 sm:p-8 border border-emerald-500/30 rounded-2xl bg-panel shadow-md ring-1 ring-emerald-500/20">
              <div className="flex items-center gap-2 mb-4 text-emerald-600 dark:text-emerald-400 font-mono font-bold text-xs uppercase tracking-wide">
                <CheckCircle size={18} weight="fill" />
                <span>Harnix Safety Harness</span>
              </div>

              <div className="space-y-4 text-sm text-neutral-700 dark:text-neutral-300">
                <div className="p-3.5 rounded-xl bg-emerald-500/[0.04] border border-emerald-500/20">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Deterministic Test Requirement</div>
                  <div>Model claims are ignored. The harness requires deterministic exit code 0 from your test runner.</div>
                </div>

                <div className="p-3.5 rounded-xl bg-emerald-500/[0.04] border border-emerald-500/20">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Sandboxed &amp; Zero Network Egress</div>
                  <div>Runs in a disposable Docker container with all outbound networking scrubbed. Host keys stay safe.</div>
                </div>

                <div className="p-3.5 rounded-xl bg-emerald-500/[0.04] border border-emerald-500/20">
                  <div className="font-semibold text-neutral-900 dark:text-white mb-1">Clean Git Patches Only</div>
                  <div>Your files remain untouched. Harnix outputs a standard git diff patch that you inspect and apply with 1 click.</div>
                </div>
              </div>
            </div>

          </div>
        )}

      </div>
    </section>
  );
}
