
import React from "react";
import immersiveBrainImage from "../assets/immersive-particle-brain.jpg";

const ImageShowcaseSection = () => {
  return (
    <section className="w-full pt-8 sm:pt-12 lg:pt-16 pb-12 sm:pb-16 lg:pb-20 bg-white" id="showcase">
      <div className="container px-4 sm:px-6 lg:px-8 mx-auto">
        <div className="max-w-3xl mx-auto text-center mb-8 sm:mb-12 animate-on-scroll">
          <h2 className="text-3xl sm:text-4xl font-display font-bold tracking-tight text-gray-900 mb-3 sm:mb-4">
            Experience AI Excellence
          </h2>
          <p className="text-base sm:text-lg text-gray-600">
            Our cutting-edge AI solutions are designed to transform how businesses create, innovate, and scale in the digital age.
          </p>
        </div>
        
        <div className="rounded-2xl sm:rounded-3xl overflow-hidden shadow-elegant mx-auto max-w-4xl animate-on-scroll">
          <div className="w-full">
            <img 
              src={immersiveBrainImage} 
              alt="Immersive glowing particle brain structure with neural networks" 
              className="w-full h-auto object-cover"
            />
          </div>
          <div className="bg-white p-4 sm:p-8">
            <h3 className="text-xl sm:text-2xl font-display font-semibold mb-3 sm:mb-4">Next Generation AI Solutions</h3>
            <p className="text-gray-700 text-sm sm:text-base">
              Built with precision algorithms and sophisticated machine learning, our AI solutions seamlessly integrate into various business environments, from startups to enterprises, providing intelligent automation and enriching digital experiences.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
};

export default ImageShowcaseSection;
