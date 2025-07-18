
import React, { useEffect, useRef } from "react";
import { ArrowRight } from "lucide-react";

const CTA = () => {
  const ctaRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("animate-fade-in");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1 }
    );
    
    if (ctaRef.current) {
      observer.observe(ctaRef.current);
    }
    
    return () => {
      if (ctaRef.current) {
        observer.unobserve(ctaRef.current);
      }
    };
  }, []);
  
  return (
    <section className="py-12 sm:py-16 md:py-20 bg-white relative" id="get-access" ref={ctaRef}>
      <div className="section-container relative z-10 opacity-0 px-4 sm:px-6">
        <div className="max-w-4xl mx-auto overflow-hidden relative">
          <div className="rounded-2xl sm:rounded-3xl overflow-hidden bg-gradient-to-br from-blue-600 via-purple-600 to-blue-800 p-6 sm:p-8 md:p-10 lg:p-14 text-center relative">
            {/* Decorative circles */}
            <div className="absolute top-0 right-0 w-32 sm:w-40 h-32 sm:h-40 bg-white/20 rounded-full translate-x-1/2 -translate-y-1/2 blur-2xl"></div>
            <div className="absolute bottom-0 left-0 w-24 sm:w-32 h-24 sm:h-32 bg-white/10 rounded-full -translate-x-1/2 translate-y-1/2 blur-2xl"></div>
            
            <div className="inline-block bg-white/20 backdrop-blur-sm px-4 py-2 rounded-full text-white text-sm font-medium mb-4 sm:mb-6">
              <span>Contact Us</span>
            </div>
            
            <h2 className="text-2xl sm:text-3xl md:text-4xl lg:text-5xl font-bold mb-4 sm:mb-6 text-white">
              Ready to Transform Your Vision?
            </h2>
            
            <p className="text-base sm:text-lg text-white/90 mb-6 sm:mb-8 max-w-2xl mx-auto">
              Tell us about your AI project and we'll show you what's possible.
            </p>
            
            <div className="flex flex-col sm:flex-row justify-center gap-4">
              <a href="#contact" className="bg-orange-500 hover:bg-orange-600 text-white font-semibold py-3 px-8 rounded-lg transition-colors group flex items-center justify-center w-full sm:w-auto">
                Contact Us Today
                <ArrowRight className="ml-2 w-4 h-4 transition-transform group-hover:translate-x-1" />
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default CTA;
