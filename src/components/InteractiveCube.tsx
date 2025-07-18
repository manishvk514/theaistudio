import React, { useState, useEffect } from 'react';

const InteractiveCube = () => {
  const [isHovered, setIsHovered] = useState(false);
  const [isClicked, setIsClicked] = useState(false);
  const [rotation, setRotation] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const interval = setInterval(() => {
      setRotation(prev => ({
        x: prev.x + 1,
        y: prev.y + 1
      }));
    }, 50);

    return () => clearInterval(interval);
  }, []);

  const handleClick = () => {
    setIsClicked(true);
    setTimeout(() => setIsClicked(false), 300);
  };

  return (
    <div className="w-full h-full min-h-[500px] flex items-center justify-center relative">
      <div 
        className="relative w-80 h-80 cursor-pointer transition-all duration-500 ease-out"
        style={{
          transform: `rotateX(${rotation.x}deg) rotateY(${rotation.y}deg) ${isHovered ? 'scale(1.15)' : 'scale(1)'} ${isClicked ? 'scale(1.2)' : ''}`,
          transformStyle: 'preserve-3d'
        }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onClick={handleClick}
      >
        {/* Cube faces */}
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #ff6b35, #f97316, #ea580c)' 
              : 'linear-gradient(135deg, #3b82f6, #2563eb, #1d4ed8)',
            transform: 'translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(255,107,53,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(59,130,246,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #8b5cf6, #7c3aed, #6d28d9)' 
              : 'linear-gradient(135deg, #06b6d4, #0891b2, #0e7490)',
            transform: 'rotateY(180deg) translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(139,92,246,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(6,182,212,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #f59e0b, #d97706, #b45309)' 
              : 'linear-gradient(135deg, #8b5cf6, #7c3aed, #6d28d9)',
            transform: 'rotateY(90deg) translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(245,158,11,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(139,92,246,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #06b6d4, #0891b2, #0e7490)' 
              : 'linear-gradient(135deg, #f59e0b, #d97706, #b45309)',
            transform: 'rotateY(-90deg) translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(6,182,212,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(245,158,11,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #ef4444, #dc2626, #b91c1c)' 
              : 'linear-gradient(135deg, #10b981, #059669, #047857)',
            transform: 'rotateX(90deg) translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(239,68,68,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(16,185,129,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
        <div 
          className="absolute inset-0 transition-all duration-300"
          style={{
            background: isHovered 
              ? 'linear-gradient(135deg, #10b981, #059669, #047857)' 
              : 'linear-gradient(135deg, #ef4444, #dc2626, #b91c1c)',
            transform: 'rotateX(-90deg) translateZ(120px)',
            opacity: isClicked ? 0.9 : 0.8,
            backdropFilter: 'blur(10px)',
            border: '2px solid rgba(255,255,255,0.3)',
            borderRadius: '12px',
            boxShadow: isHovered 
              ? '0 0 30px rgba(16,185,129,0.4), inset 0 0 20px rgba(255,255,255,0.1)' 
              : '0 0 20px rgba(239,68,68,0.3), inset 0 0 15px rgba(255,255,255,0.05)'
          }}
        />
      </div>
      
      {/* Enhanced ambient glow effect */}
      <div 
        className="absolute inset-0 transition-all duration-500 pointer-events-none"
        style={{
          background: isHovered 
            ? 'radial-gradient(circle, rgba(255,107,53,0.4) 0%, transparent 70%)'
            : 'radial-gradient(circle, rgba(59,130,246,0.4) 0%, transparent 70%)',
          filter: 'blur(25px)',
          transform: 'scale(1.8)'
        }}
      />
      
      {/* Additional particle effects */}
      <div 
        className="absolute inset-0 transition-all duration-700 pointer-events-none"
        style={{
          background: isClicked 
            ? 'radial-gradient(circle, rgba(255,255,255,0.2) 0%, transparent 50%)'
            : 'transparent',
          filter: 'blur(15px)',
          transform: 'scale(2)',
          animation: isClicked ? 'pulse 0.3s ease-out' : 'none'
        }}
      />
    </div>
  );
};

export default InteractiveCube;