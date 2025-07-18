import React, { useState } from "react";
import { toast } from "@/components/ui/use-toast";

interface NewsletterProps {
  onContactClick: () => void;
}

const Newsletter: React.FC<NewsletterProps> = ({ onContactClick }) => {
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      toast({
        title: "Please enter your email address",
        variant: "destructive"
      });
      return;
    }
    setIsSubmitting(true);

    // Simulate API call
    setTimeout(() => {
      toast({
        title: "Thank you for subscribing!",
        description: "You'll receive AI insights and updates soon."
      });
      setEmail("");
      setIsSubmitting(false);
    }, 1000);
  };
  return <section id="newsletter" className="bg-white py-0">
      <div className="section-container opacity-0 animate-on-scroll">
        <div className="max-w-6xl mx-auto">
          <div className="flex items-center gap-4 mb-6">
            <div className="pulse-chip">
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-pulse-500 text-white mr-2">05</span>
              <span>AI Insights</span>
            </div>
          </div>
          
          <h2 className="text-5xl font-display font-bold mb-4 text-left">Get the Latest in AI Innovation</h2>
          <p className="text-xl text-gray-700 mb-10 text-left">
            Stay ahead with AI trends, breakthrough techniques, and exclusive project insights
          </p>
          
          <div className="flex justify-center">
            <button
              onClick={onContactClick}
              className="px-8 py-4 bg-gradient-to-r from-[#FF6B35] to-[#FF8C42] text-white rounded-full hover:from-[#FF8C42] hover:to-[#FFA055] transition-all duration-300 font-semibold text-lg shadow-lg hover:shadow-xl transform hover:translate-y-[-2px]"
            >
              Contact Us
            </button>
          </div>
        </div>
      </div>
    </section>;
};
export default Newsletter;