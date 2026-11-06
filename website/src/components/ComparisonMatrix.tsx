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
      title: "Shell Injection Exploit",
      threat: "Prompt injection commands: `pytest; rm -rf /` or piped bash curl",
      icon: Skull,
      unconstrained: {
        behavior: "Executes unconstrained /bin/sh or cmd.exe. Shell metacharacters trigger host execution.",
        result: "CRITICAL COMPROMISE: Host filesystem corrupted or backdoor installed.",
        badge: "EXPLOIT SUCCEEDED",
      },
      harnix: {
        behavior: "POSIX argv tokenization. Metacharacters (|, &, ;, `, >) rejected at API gateway prior to execution.",
        result: "CONTAINED: Policy denial emitted. Zero host exposure.",
        badge: "100% INTERCEPTED",
      },
    },
    {
      title: "Hallucinated Bug Fix",
      threat: "LLM claims: 'I resolved all test failures and refactored the module!'",
      icon: Bug,
      unconstrained: {
        behavior: "Assumes completion as soon as model stops tool calling. No test suite rerun.",
        result: "SILENT REGRESSION: Broken code shipped to production git branch.",
        badge: "FALSE POSITIVE SHIPPED",
      },
      harnix: {
        behavior: "Model assertions ignored. Deterministic verification requires verified exit-code 0 from test runner.",
        result: "REJECTED: Harness detected failing test in Docker sandbox. Patch refused.",
        badge: "REGRESSION PREVENTED",
      },
    },
    {
      title: "Credential Exfiltration",
      threat: "Malicious prompt tries to read $GITHUB_TOKEN or $AWS_SECRET_ACCESS_KEY",
      icon: LockKey,
      unconstrained: {
        behavior: "Agent inherits full parent process environment variables and unmetered network sockets.",
        result: "DATA LEAK: Secret keys exfiltrated to external command-and-control server.",
        badge: "SECRETS STOLEN",
      },
      harnix: {
        behavior: "Environment scrubbed. Docker sandbox executes with `--network none`. Zero outbound sockets.",
        result: "CONTAINED: Network socket refused (EPERM). Host secrets remain hidden.",
        badge: "ZERO OUTBOUND EGRESS",
      },
    },
    {
      title: "Host Repository Corruption",
      threat: "Unchecked file edit overwrites in-progress unstaged local work",
      icon: WarningOctagon,
      unconstrained: {
        behavior: "Mutates developer's working directory directly without rollback snapshots.",
        result: "WORK DESTROYED: Unstaged stash and local edits irrevocably overwritten.",
        badge: "HOST DIR DAMAGED",
      },
      harnix: {
        behavior: "All changes applied inside disposable git clone in ephemeral container. Host untouched.",
        result: "SAFE: Clean git patch generated. Developer retains full review authority.",
        badge: "HOST ZERO RISK",
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
