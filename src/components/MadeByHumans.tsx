
import React from "react";
const MadeByHumans = () => {
  return <section id="made-by-humans" className="w-full bg-white py-8 sm:py-12">
      <div className="container px-4 sm:px-6 lg:px-8 mx-auto">
        <div className="w-full rounded-2xl sm:rounded-3xl overflow-hidden relative animate-on-scroll">
          <div className="bg-no-repeat bg-cover bg-center p-6 sm:p-8 lg:p-12 min-h-[400px] sm:min-h-[500px] flex flex-col justify-center items-center" style={{
          backgroundImage: "url('/background-section3.png')"
        }}>
            <div className="flex items-center justify-center text-white mb-6">
              <span className="text-white text-2xl sm:text-3xl font-medium">
                AI STUDIO
              </span>
            </div>
            
            <div className="text-center">
              <h2 className="text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-playfair text-white italic font-thin leading-tight">
                Powered by AI, Crafted by Humans
              </h2>
            </div>
          </div>
        </div>
      </div>
    </section>;
};
export default MadeByHumans;
