"use client";

import React, { useState, useEffect } from "react";
import { TerminalIcon } from "./Icons";

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 15);
    };
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-200 border-b ${
        scrolled
          ? "bg-[#0B0D12]/95 backdrop-blur-md border-white/10 shadow-lg py-2.5"
          : "bg-[#0B0D12]/70 backdrop-blur-sm border-white/5 py-3"
      }`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between">
          {/* Logo & Subsystem Badge */}
          <div className="flex items-center gap-3">
            <a href="#" className="flex items-center gap-2.5 group">
              <div className="w-7 h-7 rounded bg-white text-black flex items-center justify-center font-mono font-bold text-sm tracking-tighter">
                HX
              </div>
              <div className="flex flex-col">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold tracking-tight text-sm text-white">
                    HARNIX
                  </span>
                  <span className="sys-badge bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px]">
                    v0.5.0
                  </span>
                </div>
                <span className="text-[10px] font-mono text-neutral-400 hidden sm:block">
                  Autonomous Test Harness &amp; Execution Sandbox
                </span>
              </div>
            </a>
          </div>

          {/* Nav links */}
          <nav className="hidden md:flex items-center gap-6 font-mono text-xs text-neutral-300">
            <a
              href="#architecture"
              className="hover:text-white transition-colors"
            >
              01 // Architecture
            </a>
            <a
              href="#policy"
              className="hover:text-white transition-colors"
            >
              02 // Policy Sandbox
            </a>
            <a
              href="#inspector"
              className="hover:text-white transition-colors"
            >
              03 // Live Inspector
            </a>
            <a
              href="#benchmarks"
              className="hover:text-white transition-colors"
            >
              04 // Benchmarks
            </a>
            <a
              href="#quickstart"
              className="hover:text-white transition-colors"
            >
              05 // Run Locally
            </a>
          </nav>

          {/* Action button */}
          <div className="flex items-center gap-3">
            <a
              href="#quickstart"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded font-mono text-xs font-medium bg-neutral-800 hover:bg-neutral-700 text-neutral-100 border border-neutral-700 transition-colors"
            >
              <TerminalIcon className="w-3.5 h-3.5 text-emerald-400" />
              <span>Install CLI</span>
            </a>
          </div>
        </div>
      </div>
    </header>
  );
}
