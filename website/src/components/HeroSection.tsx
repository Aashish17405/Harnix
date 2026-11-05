"use client";

import React, { useState } from "react";
import { TerminalIcon, CheckIcon, CopyIcon, ArrowRightIcon } from "./Icons";

export function HeroSection() {
  const [copied, setCopied] = useState(false);
  const command = "harnix \"fix off-by-one in rangesum.py\" --verify \"pytest tests/\" --sandbox docker";

  const handleCopy = () => {
    navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="relative pt-28 pb-16 md:pt-36 md:pb-24 tech-grid border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 items-start">
          
          {/* Left Column: Thesis & Concrete Positioning */}
          <div className="lg:col-span-7 space-y-6">
            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded bg-neutral-900 border border-neutral-700/80 font-mono text-[11px] text-neutral-300">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>DETERMINISTIC VERIFICATION HARNESS</span>
            </div>

            <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-white leading-[1.15]">
              AI coding agents claim they fixed the bug.{" "}
              <span className="text-emerald-400">Harnix verifies it with machine proof.</span>
            </h1>

            <p className="text-neutral-300 text-sm sm:text-base leading-relaxed max-w-2xl font-normal">
              Most LLM tools trust the model when it says &ldquo;I&apos;m done&rdquo; or run unchecked shell commands directly on your host machine. 
              Harnix isolates execution in a disposable container, verifies the initial bug reproduces, runs the fix against your test suite, and only presents a patch when your tests actually pass.
            </p>

            {/* CLI Execution Bar */}
            <div className="sys-panel p-3.5 space-y-2 max-w-xl font-mono text-xs">
              <div className="flex items-center justify-between text-neutral-400 pb-1 text-[11px] border-b border-white/5">
                <span>TERMINAL INVOCATION</span>
                <span>BASH / POWERSHELL</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5 overflow-x-auto text-neutral-200 py-1">
                  <span className="text-emerald-400 font-bold select-none">$</span>
                  <span className="whitespace-nowrap font-mono">{command}</span>
                </div>
                <button
                  onClick={handleCopy}
                  className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white border border-neutral-700 text-xs transition-colors"
                  title="Copy command"
                >
                  {copied ? (
                    <>
                      <CheckIcon className="w-3.5 h-3.5 text-emerald-400" />
                      <span className="text-emerald-400 text-[11px]">Copied</span>
                    </>
                  ) : (
                    <>
                      <CopyIcon className="w-3.5 h-3.5" />
                      <span className="text-[11px]">Copy</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* CTAs */}
            <div className="flex flex-wrap items-center gap-3 pt-2">
              <a
                href="#inspector"
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded bg-emerald-500 hover:bg-emerald-400 text-black font-mono font-bold text-xs transition-colors shadow-sm"
              >
                <span>Inspect Live Execution</span>
                <ArrowRightIcon className="w-3.5 h-3.5" />
              </a>
              <a
                href="#quickstart"
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded bg-neutral-900 hover:bg-neutral-800 text-neutral-200 border border-neutral-700 font-mono text-xs transition-colors"
              >
                <TerminalIcon className="w-3.5 h-3.5 text-neutral-400" />
                <span>60-Second Mock Demo (No API Key)</span>
              </a>
            </div>

            {/* Invariant status indicators */}
            <div className="grid grid-cols-3 gap-3 pt-4 border-t border-white/10 max-w-xl font-mono text-[11px]">
              <div>
                <div className="text-neutral-400">BASELINE CHECK</div>
                <div className="text-white font-semibold">Test must fail first</div>
              </div>
              <div>
                <div className="text-neutral-400">SHELL INJECTION</div>
                <div className="text-emerald-400 font-semibold">Argv token allowlist</div>
              </div>
              <div>
                <div className="text-neutral-400">HOST INTEGRITY</div>
                <div className="text-white font-semibold">Isolated container copy</div>
              </div>
            </div>
          </div>

          {/* Right Column: Grounded State Machine Lifecycle */}
          <div className="lg:col-span-5 sys-panel p-5 space-y-4">
            <div className="flex items-center justify-between border-b border-white/10 pb-3">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="font-mono text-xs font-bold text-white uppercase tracking-wider">
                  Verification Lifecycle Engine
                </span>
              </div>
              <span className="font-mono text-[10px] text-neutral-400">
                STATE MACHINE
              </span>
            </div>

            <div className="space-y-2.5 font-mono text-xs">
              {/* Step 1 */}
              <div className="p-3 rounded bg-neutral-900/90 border border-white/5 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-neutral-400 text-[10px]">STEP 01 // INVARIANT SETUP</span>
                  <span className="sys-badge bg-amber-500/10 text-amber-300 border border-amber-500/20 text-[10px]">
                    BASELINE FAILED
                  </span>
                </div>
                <p className="text-white font-medium text-xs">pytest tests/test_rangesum.py</p>
                <p className="text-neutral-400 text-[11px]">
                  Confirms bug exists before model touches code. Avoids false positive &quot;fixes&quot; on already-passing suites.
                </p>
              </div>

              {/* Step 2 */}
              <div className="p-3 rounded bg-neutral-900/90 border border-white/5 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-neutral-400 text-[10px]">STEP 02 // POLICY BOUNDARY</span>
                  <span className="sys-badge bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px]">
                    ARGV CHECKED
                  </span>
                </div>
                <p className="text-white font-medium text-xs">Tokenized policy verification</p>
                <p className="text-neutral-400 text-[11px]">
                  Validates file path strictly inside workspace root. Rejects raw shell operators (&amp;, |, ;) and env exfiltration.
                </p>
              </div>

              {/* Step 3 */}
              <div className="p-3 rounded bg-neutral-900/90 border border-white/5 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-neutral-400 text-[10px]">STEP 03 // DISPOSABLE RUN</span>
                  <span className="sys-badge bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 text-[10px]">
                    CONTAINER SANDBOX
                  </span>
                </div>
                <p className="text-white font-medium text-xs">Execution in Docker (--network none)</p>
                <p className="text-neutral-400 text-[11px]">
                  Patches applied to ephemeral directory. Real host repository and unstaged developer files are untouched.
                </p>
              </div>

              {/* Step 4 */}
              <div className="p-3 rounded bg-neutral-900/90 border border-emerald-500/30 space-y-1 bg-emerald-950/10">
                <div className="flex items-center justify-between">
                  <span className="text-emerald-400 text-[10px]">STEP 04 // MACHINE PROOF</span>
                  <span className="sys-badge bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px]">
                    TESTS PASSED (2/2)
                  </span>
                </div>
                <p className="text-white font-medium text-xs">Deterministic Verification Passed</p>
                <p className="text-neutral-300 text-[11px]">
                  Harnix ignores the model&apos;s natural language assertion and only accepts exit code 0 from pytest.
                </p>
              </div>
            </div>

            <div className="pt-2 border-t border-white/5 flex items-center justify-between font-mono text-[10px] text-neutral-400">
              <span>OUTPUT: CLEAN GIT DIFF</span>
              <span className="text-neutral-300">READY FOR DEVELOPER REVIEW</span>
            </div>
          </div>

        </div>
      </div>
    </section>
  );
}
