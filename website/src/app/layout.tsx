import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Harnix — The Self-Verifying Autonomous AI Coding Harness",
  description:
    "An autonomous coding agent harness with independent deterministic verification, policy-guarded Docker sandboxes, and spec-to-code knowledge graph retrieval.",
  keywords: [
    "AI Agent",
    "Coding Harness",
    "Self-Verifying",
    "Devin alternative",
    "MCP",
    "Model Context Protocol",
    "Neo4j",
    "Autonomous Software Engineering",
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-[#08090D] text-slate-100 selection:bg-blue-600/30 selection:text-blue-200">
        {children}
      </body>
    </html>
  );
}
