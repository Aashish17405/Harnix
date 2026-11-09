import { Navbar } from "@/components/Navbar";
import { HeroSection } from "@/components/HeroSection";
import { HowItWorksSection } from "@/components/HowItWorksSection";
import { ComparisonMatrix } from "@/components/ComparisonMatrix";
import { InteractiveSimulator } from "@/components/InteractiveSimulator";
import { PillarsSection } from "@/components/PillarsSection";
import { BenchmarkSection } from "@/components/BenchmarkSection";
import { QuickstartSection } from "@/components/QuickstartSection";
import { Footer } from "@/components/Footer";
import { ScrollProgress } from "@/components/ScrollProgress";

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground transition-colors duration-200">
      <ScrollProgress />
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <HowItWorksSection />
        <ComparisonMatrix />
        <InteractiveSimulator />
        <PillarsSection />
        <BenchmarkSection />
        <QuickstartSection />
      </main>
      <Footer />
    </div>
  );
}
