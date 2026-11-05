"use client";

import React, { useState } from "react";
import { ShieldCheckIcon, TerminalIcon, CheckIcon, CopyIcon, ArrowRightIcon } from "./Icons";

export function HeroSection() {
  const [copied, setCopied] = useState(false);
  const command = "harnix \"fix failing test in rangesum.py\" -v \"pytest tests/test_rangesum.py\"";

  const handleCopy = () => {
    navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="relative pt-32 pb-20 md:pt-40 md:pb-28 overflow-hidden bg-grid-pattern">
      {/* Background radial gradients */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[400px] bg-gradient-to-tr from-blue-600/15 via-indigo-500/10 to-transparent blur-3xl pointer-events-none -z-10 rounded-full" />
      <div className="absolute top-20 right-10 w-[350px] h-[350px] bg-cyan-500/10 blur-3xl pointer-events-none -z-10 rounded-full" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
        {/* Pill banner */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 text-xs font-mono text-blue-300 mb-8 animate-fade-in shadow-inner">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>Independent Deterministic Verification • Zero Blind Trust</span>
        </div>

        {/* Headline */}
        <h1 className="text-4xl sm:text-6xl lg:text-7xl font-extrabold tracking-tight text-white max-w-5xl mx-auto leading-[1.1] mb-6">
          The AI coding harness that never takes{" "}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-indigo-300 to-cyan-300">
            &ldquo;I fixed it&rdquo;
          </span>{" "}
          for an answer.
        </h1>

        {/* Subtitle */}
        <p className="text-lg sm:text-xl text-slate-300 max-w-3xl mx-auto mb-10 leading-relaxed font-normal">
          Harnix is an autonomous, self-hostable agent harness for software engineering.
          It gives LLMs policy-controlled tools, isolates execution in Docker sandboxes,
          and <strong className="text-white font-medium">independently verifies every fix</strong> using your real test suites before touching repository files.
        </p>

        {/* Quick CLI command runner bar */}
        <div className="max-w-2xl mx-auto mb-12">
          <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-[#0E121B] border border-white/10 shadow-2xl font-mono text-xs sm:text-sm text-left">
            <div className="flex items-center gap-3 overflow-x-auto text-slate-300 py-1">
              <span className="text-blue-400 select-none">$</span>
              <span className="text-white whitespace-nowrap">{command}</span>
            </div>
            <button
              onClick={handleCopy}
              className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 hover:text-white border border-white/10 transition-colors"
              title="Copy command"
            >
              {copied ? (
                <>
                  <CheckIcon className="w-4 h-4 text-emerald-400" />
                  <span className="text-xs text-emerald-400">Copied</span>
                </>
              ) : (
                <>
                  <CopyIcon className="w-4 h-4" />
                  <span className="text-xs">Copy</span>
                </>
              )}
            </button>
          </div>
          <p className="text-xs text-slate-400 mt-2 text-center font-mono">
            Requires zero API keys to test — run the instant 60s mock harness demo.
          </p>
        </div>

        {/* CTAs */}
        <div className="flex flex-wrap items-center justify-center gap-4 mb-16">
          <a
            href="#simulator"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm shadow-xl shadow-blue-600/25 transition-all hover:scale-[1.02]"
          >
            <span>Explore Interactive Simulator</span>
            <ArrowRightIcon className="w-4 h-4" />
          </a>
          <a
            href="#quickstart"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-slate-900/80 hover:bg-slate-800 text-slate-200 border border-white/10 font-medium text-sm transition-all hover:scale-[1.02]"
          >
            <TerminalIcon className="w-4 h-4 text-slate-400" />
            <span>Quickstart Guide</span>
          </a>
        </div>

        {/* Metrics Grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-5xl mx-auto">
          <div className="glass-panel p-5 rounded-2xl text-left border border-white/5">
            <div className="text-3xl font-mono font-bold text-emerald-400 mb-1">
              0
            </div>
            <div className="text-xs font-medium text-white mb-0.5">
              False Success Claims
            </div>
            <div className="text-[11px] text-slate-400">
              Model promises are never trusted without machine proof
            </div>
          </div>

          <div className="glass-panel p-5 rounded-2xl text-left border border-white/5">
            <div className="text-3xl font-mono font-bold text-blue-400 mb-1">
              100%
            </div>
            <div className="text-xs font-medium text-white mb-0.5">
              Independent Verification
            </div>
            <div className="text-[11px] text-slate-400">
              Deterministic test suites run in clean container sandboxes
            </div>
          </div>

          <div className="glass-panel p-5 rounded-2xl text-left border border-white/5">
            <div className="text-3xl font-mono font-bold text-indigo-400 mb-1">
              $0.0028
            </div>
            <div className="text-xs font-medium text-white mb-0.5">
              Benchmark Cost / Fix
            </div>
            <div className="text-[11px] text-slate-400">
              Achieved on gpt-6-luna across real test repair cases
            </div>
          </div>

          <div className="glass-panel p-5 rounded-2xl text-left border border-white/5">
            <div className="text-3xl font-mono font-bold text-cyan-400 mb-1">
              3 Modes
            </div>
            <div className="text-xs font-medium text-white mb-0.5">
              Web, CLI &amp; VS Code
            </div>
            <div className="text-[11px] text-slate-400">
              Unified SSE streaming architecture across all interfaces
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
