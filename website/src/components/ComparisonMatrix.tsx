"use client";

import React, { useState } from "react";
import {
  ShieldCheck,
  WarningOctagon,
  Bug,
  LockKey,
  Skull,
  XCircle
} from "@phosphor-icons/react";

export function ComparisonMatrix() {
  const [selectedScenario, setSelectedScenario] = useState(0);

  const scenarios = [
    {
      title: "Fake 'Fixed!' Claims",
      threat: "The AI stops and claims: 'All tests passed and bug resolved!', but tests still fail.",
      icon: Bug,
      unconstrained: {
        behavior: "Takes the AI's word for it without re-running the test suite.",
        result: "SILENT OUTAGE: Broken code gets merged directly into production.",
        badge: "FALSE POSITIVE MERGED",
      },
      harnix: {
        behavior: "Model claims are ignored. Harnix independently executes the test suite. Exit code 0 required.",
        result: "PREVENTED: Harnix caught the failing test. Patch refused automatically.",
        badge: "REGRESSION STOPPED",
      },
    },
    {
      title: "Dangerous Shell Commands",
      threat: "A prompt injection or bad prompt tries to run destructive commands (rm -rf, curl | bash).",
      icon: Skull,
      unconstrained: {
        behavior: "Executes unconstrained commands directly on your computer terminal.",
        result: "SYSTEM COMPROMISE: Files deleted or unauthorized scripts executed.",
        badge: "HOST EXPOSED",
      },
      harnix: {
        behavior: "POSIX command parsing blocks dangerous subshells, pipes, and unauthorized binaries.",
        result: "CONTAINED: Dangerous command blocked before execution. Zero host risk.",
        badge: "100% BLOCKED",
      },
    },
    {
      title: "Secret Key & Token Leaks",
      threat: "Malicious prompt or dependency attempts to read $AWS_SECRET or $GITHUB_TOKEN.",
      icon: LockKey,
      unconstrained: {
        behavior: "Agent inherits your laptop's environment variables with unmetered internet access.",
        result: "CREDENTIAL THEFT: Private API keys exfiltrated to external servers.",
        badge: "SECRETS STOLEN",
      },
      harnix: {
        behavior: "Environment variables scrubbed. Disposable container runs with zero outbound internet access.",
        result: "PROTECTED: Outbound network blocked. Private keys stay completely secure.",
        badge: "ZERO NETWORK ACCESS",
      },
    },
    {
      title: "Overwriting Your Work",
      threat: "AI makes mistakes and directly mutates your uncommitted local changes.",
      icon: WarningOctagon,
      unconstrained: {
        behavior: "Edits your current working directory directly without rollback snapshots.",
        result: "WORK DESTROYED: Your in-progress code and git stash are overwritten.",
        badge: "LOCAL WORK LOST",
      },
      harnix: {
        behavior: "AI works inside an isolated, temporary clone. Your local files are 100% untouched.",
        result: "SAFE: Only verified diff patches are generated for your 1-click review.",
        badge: "LOCAL FILES UNTOUCHED",
      },
    },
  ];

  const current = scenarios[selectedScenario];

  return (
    <section id="containment" className="py-24 bg-background border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-10 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-cyan-500" />
              <span>02 // Threat Model &amp; Policy Containment</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Interactive Attack Simulation.
            </h2>
          </div>
          <p className="text-neutral-600 dark:text-neutral-400 font-mono text-xs max-w-md">
            Click an attack vector below to see how unconstrained tools fail versus Harnix&apos;s containment boundary.
          </p>
        </div>

        {/* Attack Vector Selectors */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-8">
          {scenarios.map((sc, idx) => {
            const ScIcon = sc.icon;
            const isSelected = selectedScenario === idx;
            return (
              <button
                key={sc.title}
                onClick={() => setSelectedScenario(idx)}
                className={`p-3.5 rounded-xl border text-left transition-all duration-200 cursor-pointer ${
                  isSelected
                    ? "border-emerald-500/50 bg-panel shadow-sm ring-1 ring-emerald-500/20"
                    : "border-panel-border bg-panel-subtle/40 hover:bg-panel hover:border-neutral-400 dark:hover:border-neutral-700"
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <ScIcon size={16} weight="duotone" className={isSelected ? "text-emerald-500" : "text-neutral-500 dark:text-neutral-400"} />
                  <span className="font-mono text-[10px] text-neutral-500 dark:text-neutral-400">VECTOR 0{idx + 1}</span>
                </div>
                <div className="font-mono font-bold text-xs text-foreground truncate">
                  {sc.title}
                </div>
              </button>
            );
          })}
        </div>

        {/* Interactive Comparison Stage */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          
          {/* Left: Unconstrained AI */}
          <div className="sys-panel p-6 border border-red-500/20 bg-panel rounded-2xl flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 mb-4 border-b border-panel-border">
                <div className="flex items-center gap-2">
                  <XCircle size={18} weight="fill" className="text-red-500" />
                  <span className="font-mono font-bold text-xs text-foreground uppercase">
                    Unconstrained AI Agent
                  </span>
                </div>
                <span className="sys-badge bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20 text-[10px]">
                  {current.unconstrained.badge}
                </span>
              </div>

              <div className="p-3.5 rounded-lg bg-neutral-950 border border-neutral-800 font-mono text-xs text-neutral-300 mb-4">
                <div className="text-neutral-500 text-[10px] mb-1">INCOMING ATTACK PAYLOAD:</div>
                <div className="text-amber-400 break-words">{current.threat}</div>
              </div>

              <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed mb-4">
                {current.unconstrained.behavior}
              </p>
            </div>

            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-700 dark:text-red-400 font-mono text-xs font-semibold">
              {current.unconstrained.result}
            </div>
          </div>

          {/* Right: Harnix Hardened Harness */}
          <div className="sys-panel p-6 border border-emerald-500/30 bg-panel rounded-2xl shadow-md flex flex-col justify-between ring-1 ring-emerald-500/20">
            <div>
              <div className="flex items-center justify-between pb-3 mb-4 border-b border-panel-border">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={18} weight="fill" className="text-emerald-500" />
                  <span className="font-mono font-bold text-xs text-foreground uppercase">
                    Harnix Hardened Sandbox
                  </span>
                </div>
                <span className="sys-badge bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 text-[10px]">
                  {current.harnix.badge}
                </span>
              </div>

              <div className="p-3.5 rounded-lg bg-neutral-950 border border-neutral-800 font-mono text-xs text-neutral-300 mb-4">
                <div className="text-neutral-500 text-[10px] mb-1">POLICY ENGINE INTERCEPTION:</div>
                <div className="text-emerald-400 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  <span>policy.validate_argv() &rarr; BLOCKED (Security Invariant)</span>
                </div>
              </div>

              <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed mb-4">
                {current.harnix.behavior}
              </p>
            </div>

            <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-700 dark:text-emerald-300 font-mono text-xs font-semibold">
              {current.harnix.result}
            </div>
          </div>

        </div>

      </div>
    </section>
  );
}
