"use client";

import React, { useState, useEffect } from "react";
import Image from "next/image";
import {
  ShieldCheck,
  GitBranch,
  TerminalWindow,
  ArrowRight,
  CheckCircle,
  LockKey,
  ArrowsClockwise,
  CodeBlock,
  Cpu,
} from "@phosphor-icons/react";

function ScaleRuler({ className = "" }: { className?: string }) {
  return (
    <div
      className={`w-full flex items-center justify-between font-mono text-[9px] text-neutral-400 dark:text-neutral-500 select-none border-y border-panel-border bg-panel/80 px-3 py-1.5 ${className}`}
      aria-hidden="true"
    >
      <span className="flex items-center gap-1.5 shrink-0">
        <span className="text-emerald-500 font-bold text-xs">+</span>
        <span className="tracking-wider">00:00</span>
      </span>

      <div className="hidden sm:flex items-center gap-2.5 overflow-hidden px-4 opacity-60">
        {Array.from({ length: 28 }).map((_, i) => (
          <span
            key={i}
            className={`inline-block w-px ${
              i % 4 === 0
                ? "h-2.5 bg-neutral-400 dark:bg-neutral-600"
                : "h-1.5 bg-neutral-300 dark:bg-neutral-800"
            }`}
          />
        ))}
      </div>

      <span className="font-mono font-medium text-neutral-500 dark:text-neutral-400 uppercase tracking-widest text-[9px] shrink-0 hidden md:inline">
        HARNIX VERIFIED CONTAINER &bull; SCALE 1.0X &bull; DETERMINISTIC TEST PROOF
      </span>

      <div className="hidden sm:flex items-center gap-2.5 overflow-hidden px-4 opacity-60">
        {Array.from({ length: 28 }).map((_, i) => (
          <span
            key={i}
            className={`inline-block w-px ${
              i % 4 === 0
                ? "h-2.5 bg-neutral-400 dark:bg-neutral-600"
                : "h-1.5 bg-neutral-300 dark:bg-neutral-800"
            }`}
          />
        ))}
      </div>

      <span className="flex items-center gap-1.5 shrink-0">
        <span className="tracking-wider">01:00</span>
        <span className="text-emerald-500 font-bold text-xs">+</span>
      </span>
    </div>
  );
}

function CornerCrosshair({ position }: { position: "tl" | "tr" | "bl" | "br" }) {
  const posClass = {
    tl: "-top-2.5 -left-2.5",
    tr: "-top-2.5 -right-2.5",
    bl: "-bottom-2.5 -left-2.5",
    br: "-bottom-2.5 -right-2.5",
  }[position];

  return (
    <div
      className={`absolute ${posClass} w-5 h-5 flex items-center justify-center font-mono text-emerald-500 font-bold text-sm select-none pointer-events-none z-20`}
      aria-hidden="true"
    >
      +
    </div>
  );
}

export function HeroSection() {
  const [activeTab, setActiveTab] = useState<"terminal" | "ast" | "diff">("terminal");
  const [activeStep, setActiveStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [simNonce, setSimNonce] = useState(0);

  const steps = [
    {
      label: "1. Ground the Spec",
      icon: GitBranch,
      desc: "Connects the task to your code symbols and requirements so the AI knows exactly what to build.",
      status: "context loaded",
      color: "text-purple-600 dark:text-purple-400",
      bg: "bg-purple-500/10 border-purple-500/20",
    },
    {
      label: "2. Guarded Sandbox",
      icon: LockKey,
      desc: "Agent writes code inside an isolated container. Dangerous commands and network leaks are blocked.",
      status: "isolated & safe",
      color: "text-cyan-600 dark:text-cyan-400",
      bg: "bg-cyan-500/10 border-cyan-500/20",
    },
    {
      label: "3. Independent Verification",
      icon: TerminalWindow,
      desc: "Harnix doesn't trust the AI's words. It runs your real test suite. Zero exit-code required.",
      status: "exit code: 0",
      color: "text-amber-600 dark:text-amber-400",
      bg: "bg-amber-500/10 border-amber-500/20",
    },
    {
      label: "4. Verified Git Patch",
      icon: CheckCircle,
      desc: "Once verified, you get a clean git diff. Your working files stay 100% safe until you approve.",
      status: "ready to apply",
      color: "text-emerald-600 dark:text-emerald-400",
      bg: "bg-emerald-500/10 border-emerald-500/20",
    },
  ];

  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setActiveStep((prev) => (prev + 1) % steps.length);
    }, 2800);
    return () => clearInterval(interval);
  }, [isPlaying, steps.length]);

  const handleRerun = () => {
    setSimNonce((n) => n + 1);
  };

  return (
    <section className="relative pt-20 sm:pt-24 pb-16 sm:pb-20 overflow-hidden tech-grid border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl 2xl:max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">

        {/* HERO CARD WITH SHADOW AND SCALES (Cinematic Devtool Sandbox Architecture) */}
        <div className="relative my-4 rounded-2xl md:rounded-3xl border border-panel-border bg-panel shadow-[0_25px_60px_-15px_rgba(0,0,0,0.14)] dark:shadow-[0_30px_90px_-20px_rgba(0,0,0,0.85)] ring-1 ring-neutral-900/5 dark:ring-white/5 overflow-hidden">
          
          {/* Corner Precision Crosshairs */}
          <CornerCrosshair position="tl" />
          <CornerCrosshair position="tr" />
          <CornerCrosshair position="bl" />
          <CornerCrosshair position="br" />

          {/* Top Scale Ruler */}
          <ScaleRuler />

          {/* Visual Viewport with Relevant Harnix Sandbox Architecture (Bespoke & Blurred) */}
          <div className="relative min-h-[500px] sm:min-h-[540px] md:min-h-[580px] lg:min-h-[620px] flex flex-col justify-end p-6 sm:p-10 md:p-14 lg:p-16 overflow-hidden">
            
            {/* Background Image: Bespoke Technical AI Sandbox Architecture with Cinematic Blur */}
            <div className="absolute inset-0 z-0 select-none overflow-hidden">
              <Image
                src="/hero-harness-visual.jpg"
                alt="Harnix deterministic AI coding harness visual with isolated sandbox and verification conduits"
                fill
                priority
                className="object-cover object-center filter contrast-110 brightness-90 blur-[2px] sm:blur-[3px] scale-105"
                sizes="(max-width: 640px) 100vw, (max-width: 1280px) 95vw, 1400px"
              />
              {/* High-contrast dark overlay to guarantee razor-sharp text readability with glowing background conduits */}
              <div className="absolute inset-0 bg-neutral-950/60 sm:bg-neutral-950/50" />
            </div>

            {/* Editorial Content Overlaid on the Cinematic Frame */}
            <div className="relative z-10 max-w-5xl space-y-4 sm:space-y-6">
              
              <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md border border-white/20 bg-black/60 backdrop-blur-md text-[10px] sm:text-[11px] font-mono text-neutral-200 w-fit">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span>HARNIX VERIFICATION ENGINE 2.0</span>
                <span>&bull;</span>
                <span className="text-emerald-400 font-semibold">CONTAINER SANDBOXED</span>
              </div>

              <h1 className="text-3xl sm:text-5xl md:text-6xl lg:text-7xl font-extrabold tracking-tight text-white leading-[1.1] max-w-4xl drop-shadow-sm">
                The only AI harness that <br className="hidden sm:inline" />
                <span className="text-emerald-400">
                  independently proves
                </span>{" "}
                every code change.
              </h1>

              <p className="text-base sm:text-lg md:text-xl text-neutral-200 max-w-5xl leading-relaxed drop-shadow-sm font-normal">
                AI coding agents hallucinate success and break existing tests. Harnix sandboxes execution in an isolated Docker container, captures AST modifications, and independently executes your real test suite before any code touches your repository.
              </p>

              {/* Call to Action Buttons */}
              <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-4 pt-2">
                <a
                  href="#quickstart"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl font-mono text-xs sm:text-sm font-semibold bg-emerald-500 hover:bg-emerald-400 text-black shadow-lg hover:shadow-xl transition-all duration-200 cursor-pointer text-center"
                >
                  <TerminalWindow size={17} weight="bold" />
                  <span>Try for free today</span>
                  <ArrowRight size={15} weight="bold" />
                </a>
                <a
                  href="#how-it-works"
                  className="inline-flex items-center justify-center gap-2 px-5 py-3.5 rounded-xl font-mono text-xs sm:text-sm font-medium text-white hover:text-emerald-300 transition-colors cursor-pointer text-center group"
                >
                  <span>Read documentation</span>
                  <ArrowRight size={14} weight="bold" className="group-hover:translate-x-1 transition-transform" />
                </a>
              </div>

            </div>

          </div>

          {/* Bottom Scale Ruler */}
          <ScaleRuler />

        </div>

        {/* LIVE INTERACTIVE VERIFICATION STUDIO (Integrated below the Hero Card) */}
        <div className="mt-8 rounded-2xl border border-panel-border bg-panel-subtle shadow-lg overflow-hidden">
          
          {/* Studio Header Bar */}
          <div className="p-3 sm:px-4 sm:py-3 bg-panel border-b border-panel-border flex flex-col md:flex-row md:items-center justify-between gap-3">
            
            {/* Left: Container Metadata */}
            <div className="flex items-center gap-2.5 sm:gap-3">
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="w-2.5 h-2.5 sm:w-3 sm:h-3 rounded-full bg-red-500/80" />
                <span className="w-2.5 h-2.5 sm:w-3 sm:h-3 rounded-full bg-amber-500/80" />
                <span className="w-2.5 h-2.5 sm:w-3 sm:h-3 rounded-full bg-emerald-500/80" />
              </div>
              <div className="h-4 w-px bg-panel-border hidden sm:block" />
              <div className="flex items-center gap-1.5 sm:gap-2 font-mono text-xs text-neutral-600 dark:text-neutral-400">
                <Cpu size={15} className="text-emerald-500 shrink-0" />
                <span className="font-semibold text-foreground">SANDBOX:</span>
                <span className="text-emerald-600 dark:text-emerald-400 truncate">hrnx_ephemeral_8f2a</span>
                <span className="hidden lg:inline text-neutral-400">(Python 3.12, Linux 6.6)</span>
              </div>
            </div>

            {/* Center: Interactive View Tabs (Scrollable on small screens) */}
            <div className="flex items-center gap-1 p-0.5 rounded-lg border border-panel-border bg-panel font-mono text-xs overflow-x-auto max-w-full scrollbar-none">
              <button
                onClick={() => setActiveTab("terminal")}
                className={`px-2.5 sm:px-3 py-1 rounded-md transition-all cursor-pointer whitespace-nowrap ${
                  activeTab === "terminal"
                    ? "bg-foreground text-background font-bold shadow-xs"
                    : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
                }`}
              >
                Terminal Stream
              </button>
              <button
                onClick={() => setActiveTab("ast")}
                className={`px-2.5 sm:px-3 py-1 rounded-md transition-all cursor-pointer whitespace-nowrap ${
                  activeTab === "ast"
                    ? "bg-foreground text-background font-bold shadow-xs"
                    : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
                }`}
              >
                AST Security Guard
              </button>
              <button
                onClick={() => setActiveTab("diff")}
                className={`px-2.5 sm:px-3 py-1 rounded-md transition-all cursor-pointer whitespace-nowrap ${
                  activeTab === "diff"
                    ? "bg-foreground text-background font-bold shadow-xs"
                    : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
                }`}
              >
                Verified Patch
              </button>
            </div>

            {/* Right: Status & Re-run */}
            <div className="flex items-center justify-between md:justify-end gap-3 w-full md:w-auto">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 font-mono text-[11px] font-bold text-emerald-600 dark:text-emerald-400">
                <CheckCircle size={14} weight="bold" />
                <span>EXIT 0 (PASSED)</span>
              </span>
              <button
                onClick={handleRerun}
                title="Re-run verification test simulation"
                className="p-1.5 rounded-md border border-panel-border bg-panel hover:bg-panel-subtle text-foreground cursor-pointer transition-colors"
              >
                <ArrowsClockwise size={15} />
              </button>
            </div>

          </div>

          {/* Studio Body: Tabbed Panels */}
          <div className="p-4 sm:p-6 font-mono text-xs leading-relaxed bg-[#0C0F17] text-[#EDEDF2] overflow-x-auto min-h-[250px]">
            
            {activeTab === "terminal" && (
              <div key={simNonce} className="space-y-1.5 animate-fadeIn">
                <div className="flex items-center gap-2 text-neutral-400 pb-2 border-b border-neutral-800 flex-wrap">
                  <span className="text-emerald-400">$</span>
                  <span className="text-white font-semibold break-all">
                    harnix &quot;fix calculate_total tax rounding precision&quot; --verify &quot;pytest tests/&quot;
                  </span>
                </div>
                <div className="text-neutral-400">
                  <span className="text-neutral-500">[15:19:01]</span> <span className="text-cyan-400">[SANDBOX]</span> Initializing ephemeral Docker container (id: hrnx_8f2a, image: python:3.12-slim)
                </div>
                <div className="text-neutral-400">
                  <span className="text-neutral-500">[15:19:02]</span> <span className="text-purple-400">[SECURITY]</span> Network: <span className="text-emerald-400 font-bold">BLOCKED (0 egress)</span> | Host mount: <span className="text-emerald-400 font-bold">READ-ONLY</span>
                </div>
                <div className="text-neutral-400">
                  <span className="text-neutral-500">[15:19:03]</span> <span className="text-blue-400">[AST_PARSER]</span> Target symbols located: <span className="text-yellow-300">src/billing/tax.py:calculate_tax()</span>
                </div>
                <div className="text-neutral-400">
                  <span className="text-neutral-500">[15:19:04]</span> <span className="text-amber-400">[AGENT_EXEC]</span> Agent proposed code modification (+4 lines, -1 line)
                </div>
                <div className="text-neutral-400">
                  <span className="text-neutral-500">[15:19:05]</span> <span className="text-cyan-400">[VERIFY_INDEPENDENT]</span> Running test runner inside container: pytest tests/test_tax.py
                </div>
                <div className="pl-2 sm:pl-4 text-emerald-400 space-y-0.5 pt-1 overflow-x-auto">
                  <div>tests/test_tax.py::test_vat_precision ..................... <span className="font-bold text-emerald-300">PASSED [ 33%]</span></div>
                  <div>tests/test_tax.py::test_rounding_invariance ............ <span className="font-bold text-emerald-300">PASSED [ 66%]</span></div>
                  <div>tests/test_tax.py::test_zero_division .................... <span className="font-bold text-emerald-300">PASSED [100%]</span></div>
                </div>
                <div className="pt-2 border-t border-neutral-800 text-emerald-400 font-semibold flex flex-col sm:flex-row sm:items-center justify-between gap-1">
                  <span>[15:19:06] VERIFICATION SUCCESS: 3 passed in 0.28s (Exit Code: 0)</span>
                  <span className="text-neutral-400 text-[11px]">Patch: .harnix/patches/fix-tax.patch</span>
                </div>
              </div>
            )}

            {activeTab === "ast" && (
              <div className="space-y-3 animate-fadeIn">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-2 border-b border-neutral-800 text-neutral-400 gap-1">
                  <span className="text-white font-semibold flex items-center gap-2">
                    <CodeBlock size={16} className="text-purple-400 shrink-0" />
                    AST Policy &amp; Syscall Verification Tree
                  </span>
                  <span className="text-emerald-400 font-bold text-[11px] sm:text-xs">STATUS: STRICT_ALLOW</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[11px]">
                  <div className="p-3 rounded-lg bg-neutral-900 border border-neutral-800 space-y-1.5">
                    <div className="text-neutral-400 font-semibold uppercase text-[10px]">Symbol Boundary Check</div>
                    <div className="text-neutral-300">Function: <span className="text-purple-300">calculate_tax(amount, rate)</span></div>
                    <div className="text-neutral-300">Mutated AST Nodes: <span className="text-cyan-300 break-words">BinaryOp(Mult) &rarr; Call(Decimal.quantize)</span></div>
                    <div className="text-emerald-400">Target Scope: Authorized within module</div>
                  </div>
                  <div className="p-3 rounded-lg bg-neutral-900 border border-neutral-800 space-y-1.5">
                    <div className="text-neutral-400 font-semibold uppercase text-[10px]">Security Invariants</div>
                    <div className="text-neutral-300">Socket / Network imports: <span className="text-emerald-400">0 (Blocked)</span></div>
                    <div className="text-neutral-300">Subprocess / System execution: <span className="text-emerald-400">0 (Blocked)</span></div>
                    <div className="text-neutral-300">File overwrite outside workspace: <span className="text-emerald-400">0 (Blocked)</span></div>
                  </div>
                </div>
                <div className="text-xs text-neutral-400 pt-1">
                  Verdict: AST modifications adhere 100% to declared task boundaries. No destructive operations detected.
                </div>
              </div>
            )}

            {activeTab === "diff" && (
              <div className="space-y-2 animate-fadeIn">
                <div className="flex items-center justify-between pb-2 border-b border-neutral-800 text-neutral-400">
                  <span className="text-white font-semibold truncate">
                    .harnix/patches/verified_fix_tax.patch
                  </span>
                  <span className="text-neutral-400 text-[11px] shrink-0">Unified Diff</span>
                </div>
                <pre className="text-neutral-300 text-[11px] leading-relaxed overflow-x-auto">
                  <span className="text-neutral-500">--- a/src/billing/tax.py</span><br />
                  <span className="text-neutral-500">+++ b/src/billing/tax.py</span><br />
                  <span className="text-cyan-400">@@ -18,4 +18,7 @@ def calculate_tax(amount: float, rate: float) -&gt; Decimal:</span><br />
                  <span className="text-red-400 bg-red-950/40 inline-block w-full">-    return round(amount * rate, 2)</span><br />
                  <span className="text-emerald-400 bg-emerald-950/40 inline-block w-full">+    dec_amount = Decimal(str(amount))</span><br />
                  <span className="text-emerald-400 bg-emerald-950/40 inline-block w-full">+    dec_rate = Decimal(str(rate))</span><br />
                  <span className="text-emerald-400 bg-emerald-950/40 inline-block w-full">+    return (dec_amount * dec_rate).quantize(Decimal(&quot;0.01&quot;), rounding=ROUND_HALF_UP)</span>
                </pre>
              </div>
            )}

          </div>

          {/* Studio Telemetry Footer */}
          <div className="p-3 sm:px-4 sm:py-2.5 bg-panel border-t border-panel-border flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-[10px] sm:text-[11px] font-mono text-neutral-600 dark:text-neutral-400">
            <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                SANDBOX: SECURE &bull; 0 EGRESS
              </span>
              <span>&bull;</span>
              <span>AST POLICY: STRICT_ALLOW</span>
            </div>
            <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
              <span>INDEPENDENT VERIFICATION</span>
              <span>&bull;</span>
              <span className="text-foreground font-bold">100% REPRODUCIBLE EXIT 0</span>
            </div>
          </div>

        </div>

        {/* 4-Step Verification Stepper Panel (Integrated cleanly below) */}
        <div className="mt-8 sys-panel p-5 sm:p-7 md:p-8 max-w-5xl 2xl:max-w-[1200px] mx-auto border border-panel-border shadow-md rounded-2xl bg-panel">
          
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-5 mb-5 sm:pb-6 sm:mb-6 border-b border-panel-border gap-3">
            <div>
              <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                <ShieldCheck size={16} weight="duotone" className="text-emerald-500" />
                <span>The 4-Step Verification Cycle</span>
              </div>
              <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
                How Harnix converts unpredictable AI suggestions into guaranteed, working software.
              </p>
            </div>

            <button
              onClick={() => setIsPlaying(!isPlaying)}
              type="button"
              className="inline-flex items-center gap-1.5 self-start sm:self-auto px-2.5 py-1 rounded-md border border-panel-border bg-panel-subtle text-[11px] font-mono text-neutral-600 dark:text-neutral-400 hover:text-foreground cursor-pointer transition-colors"
            >
              <span className={`w-1.5 h-1.5 rounded-full ${isPlaying ? "bg-emerald-500 animate-ping" : "bg-neutral-400"}`} />
              <span>{isPlaying ? "Auto Cycling" : "Paused"}</span>
            </button>
          </div>

          {/* Stepper Cards: 1 col on mobile, 2 on sm/md, 4 on lg */}
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
                      STEP 0{idx + 1}
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

          {/* Clean Step Summary Bar */}
          <div className="mt-6 p-3.5 sm:p-4 rounded-xl bg-neutral-950 border border-neutral-800 font-mono text-xs text-neutral-300 flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
            <div className="flex items-center gap-2 overflow-x-auto scrollbar-none">
              <span className="text-emerald-400 select-none">&rarr;</span>
              <span className="text-neutral-400 shrink-0">Live Status:</span>
              <span className="text-emerald-300 font-medium">
                {activeStep === 0 && "Requirements identified: 4 test targets linked from spec"}
                {activeStep === 1 && "Security guard active: zero outbound network access, host files protected"}
                {activeStep === 2 && "Running tests in disposable container: 8 passed, 0 failed"}
                {activeStep === 3 && "Verification complete: verified patch generated in .harnix/patches/"}
              </span>
            </div>
            <div className="text-[11px] text-neutral-500 shrink-0">
              Verdict: Guaranteed Exit-0
            </div>
          </div>

        </div>

      </div>
    </section>
  );
}
