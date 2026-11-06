"use client";

import React, { useState, useEffect } from "react";
import {
  ShieldCheck,
  GitBranch,
  TerminalWindow,
  Play,
  ArrowRight,
  CheckCircle,
  LockKey
} from "@phosphor-icons/react";

export function HeroSection() {
  const [activeStep, setActiveStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);

  const steps = [
    {
      label: "SPEC GROUNDING",
      icon: GitBranch,
      desc: "Queries Neo4j Knowledge Graph. Extracts Jira story & AST symbols.",
      status: "3 nodes linked",
      color: "text-purple-600 dark:text-purple-400",
      bg: "bg-purple-500/10 border-purple-500/20",
    },
    {
      label: "POLICY SANDBOX",
      icon: LockKey,
      desc: "POSIX argv parsed. Shell injection and network access blocked.",
      status: "0 violations",
      color: "text-cyan-600 dark:text-cyan-400",
      bg: "bg-cyan-500/10 border-cyan-500/20",
    },
    {
      label: "EPHEMERAL DOCKER",
      icon: TerminalWindow,
      desc: "Disposable container test suite executed. Exit code verified.",
      status: "exit code: 0",
      color: "text-amber-600 dark:text-amber-400",
      bg: "bg-amber-500/10 border-amber-500/20",
    },
    {
      label: "VERIFIED PATCH",
      icon: CheckCircle,
      desc: "Deterministic success. Git patch written to .harnix/patches/.",
      status: "100% verified",
      color: "text-emerald-600 dark:text-emerald-400",
      bg: "bg-emerald-500/10 border-emerald-500/20",
    },
  ];

  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setActiveStep((prev) => (prev + 1) % steps.length);
    }, 2400);
    return () => clearInterval(interval);
  }, [isPlaying, steps.length]);

  return (
    <section className="relative pt-32 pb-20 overflow-hidden tech-grid border-b border-panel-border transition-colors duration-200">
      
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Top Announcement Pill */}
        <div className="flex items-center justify-center mb-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-panel-border bg-panel shadow-xs text-xs font-mono">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-neutral-600 dark:text-neutral-400">Open-Source Autonomous Test Harness</span>
            <span className="text-neutral-300 dark:text-neutral-700">&bull;</span>
            <span className="font-semibold text-emerald-600 dark:text-emerald-400">Deterministic Exit-0</span>
          </div>
        </div>

        {/* Headline */}
        <div className="text-center max-w-4xl mx-auto space-y-4">
          <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight text-foreground leading-[1.1]">
            Stop trusting unverified agent outputs. <br className="hidden sm:inline" />
            <span className="text-emerald-600 dark:text-emerald-400">
              Harness, constrain, and verify them.
            </span>
          </h1>
          <p className="text-base sm:text-lg text-neutral-600 dark:text-neutral-400 max-w-2xl mx-auto leading-relaxed">
            Harnix surrounds LLMs with an independent deterministic verification sandbox, policy containment, and spec-to-code knowledge graph.
          </p>
        </div>

        {/* CTA Buttons */}
        <div className="flex flex-wrap items-center justify-center gap-3.5 mt-8 mb-16">
          <a
            href="#quickstart"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-mono text-xs font-semibold bg-neutral-900 text-white hover:bg-neutral-800 dark:bg-emerald-500 dark:hover:bg-emerald-400 dark:text-black shadow-md transition-all duration-200"
          >
            <TerminalWindow size={16} weight="bold" />
            <span>Install CLI in 60s</span>
            <ArrowRight size={14} weight="bold" />
          </a>
          <a
            href="#simulator"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-mono text-xs font-medium border border-panel-border bg-panel hover:bg-panel-subtle text-foreground shadow-xs transition-all duration-200"
          >
            <Play size={15} weight="fill" className="text-emerald-500" />
            <span>Interactive Simulator</span>
          </a>
        </div>

        {/* Visual Animatory Explanation: Autonomous Verification Loop */}
        <div className="sys-panel p-6 sm:p-8 max-w-5xl mx-auto border border-panel-border shadow-xl rounded-2xl bg-panel">
          
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 mb-6 border-b border-panel-border gap-3">
            <div>
              <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                <ShieldCheck size={16} weight="duotone" className="text-emerald-500" />
                <span>Deterministic Verification Loop // Real-Time State Machine</span>
              </div>
              <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
                Every agent proposal passes through 4 continuous containment checkpoints before touching disk.
              </p>
            </div>

            <button
              onClick={() => setIsPlaying(!isPlaying)}
              className="inline-flex items-center gap-1.5 self-start sm:self-auto px-2.5 py-1 rounded-md border border-panel-border bg-panel-subtle text-[11px] font-mono text-neutral-600 dark:text-neutral-400 hover:text-foreground cursor-pointer transition-colors"
            >
              <span className={`w-1.5 h-1.5 rounded-full ${isPlaying ? "bg-emerald-500 animate-ping" : "bg-neutral-400"}`} />
              <span>{isPlaying ? "Live Cycle" : "Paused"}</span>
            </button>
          </div>

          {/* Stepper Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {steps.map((st, idx) => {
              const Icon = st.icon;
              const isActive = activeStep === idx;
              return (
                <div
                  key={st.label}
                  onClick={() => {
                    setActiveStep(idx);
                    setIsPlaying(false);
                  }}
                  className={`p-4 rounded-xl border transition-all duration-300 cursor-pointer ${
                    isActive
                      ? "border-emerald-500/50 bg-emerald-500/[0.04] shadow-md dark:shadow-emerald-950/20 ring-1 ring-emerald-500/20"
                      : "border-panel-border bg-panel-subtle/50 hover:bg-panel-subtle hover:border-neutral-400 dark:hover:border-neutral-700"
                  }`}
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono text-[10px] text-neutral-500 dark:text-neutral-400">
                      PHASE 0{idx + 1}
                    </span>
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-md border ${st.bg} ${st.color}`}>
                      {st.status}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 mb-2">
                    <Icon size={18} weight="duotone" className={isActive ? "text-emerald-500" : "text-neutral-500 dark:text-neutral-400"} />
                    <span className="font-mono font-bold text-xs text-foreground tracking-tight">
                      {st.label}
                    </span>
                  </div>

                  <p className="text-[11px] text-neutral-600 dark:text-neutral-400 leading-snug">
                    {st.desc}
                  </p>
                </div>
              );
            })}
          </div>

          {/* Micro Terminal Trace for the active step */}
          <div className="mt-6 p-4 rounded-xl bg-neutral-950 border border-neutral-800 font-mono text-xs text-neutral-300 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2 overflow-x-auto">
              <span className="text-emerald-400 select-none">$</span>
              <span className="text-neutral-400">harnix verify:</span>
              <span className="text-emerald-300 font-semibold">
                {activeStep === 0 && "spec_graph.query(issue_id='HAR-104') -> matched 4 ast nodes"}
                {activeStep === 1 && "policy.intercept(argv=['python', 'calc.py']) -> ALLOWED (sandbox isolated)"}
                {activeStep === 2 && "docker.run(image='harnix/runtime:py311') -> pytest exit code 0"}
                {activeStep === 3 && "git.export_patch(target='.harnix/patches/8f9a.patch') -> APPROVED"}
              </span>
            </div>
            <div className="text-[11px] text-neutral-500 shrink-0">
              Latency: 0.14s
            </div>
          </div>

        </div>

      </div>
    </section>
  );
}
