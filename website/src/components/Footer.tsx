import React from "react";

export function Footer() {
  return (
    <footer className="py-12 border-t border-white/5 bg-[#06080B] text-slate-400 font-mono text-xs">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center font-bold text-white text-sm">
            H
          </div>
          <span className="font-bold text-white tracking-wider text-sm">HARNIX</span>
          <span className="text-slate-400">&bull;</span>
          <span>Self-Verifying Autonomous AI Coding Harness</span>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-slate-400">
          <a href="#simulator" className="hover:text-white transition-colors">
            Simulator
          </a>
          <a href="#pillars" className="hover:text-white transition-colors">
            Architecture
          </a>
          <a href="#interfaces" className="hover:text-white transition-colors">
            CLI &amp; IDE
          </a>
          <a href="#benchmarks" className="hover:text-white transition-colors">
            Benchmarks
          </a>
          <a href="#quickstart" className="hover:text-white transition-colors">
            Quickstart
          </a>
        </div>

        <div className="text-slate-400">
          Licensed under Apache 2.0 &bull; Built for Hackathon Selection
        </div>
      </div>
    </footer>
  );
}
