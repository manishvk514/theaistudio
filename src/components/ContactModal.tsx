import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';

interface ContactModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface FormData {
  projectType: string;
  companyInfo: string;
  contactName: string;
  email: string;
  phone: string;
  companySize: string;
  projectDescription: string;
  timeline: string;
  budgetRange: string;
}

const ContactModal: React.FC<ContactModalProps> = ({ isOpen, onClose }) => {
  const [formData, setFormData] = useState<FormData>({
    projectType: '',
    companyInfo: '',
    contactName: '',
    email: '',
    phone: '',
    companySize: '',
    projectDescription: '',
    timeline: '',
    budgetRange: ''
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [errors, setErrors] = useState<Partial<FormData>>({});

  // Prevent background scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  // Close modal on ESC key
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [isOpen, onClose]);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
    
    // Clear error when user starts typing
    if (errors[name as keyof FormData]) {
      setErrors(prev => ({ ...prev, [name]: '' }));
    }
  };

  const validateForm = (): boolean => {
    const newErrors: Partial<FormData> = {};
    
    if (!formData.projectType) newErrors.projectType = 'Please select a project type';
    if (!formData.companyInfo.trim()) newErrors.companyInfo = 'Company information is required';
    if (!formData.contactName.trim()) newErrors.contactName = 'Your name is required';
    if (!formData.email.trim()) newErrors.email = 'Email address is required';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) newErrors.email = 'Please enter a valid email address';
    if (!formData.projectDescription.trim()) newErrors.projectDescription = 'Project description is required';
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!validateForm()) {
      return;
    }
    
    setIsSubmitting(true);
    
    try {
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000));
      
      setSubmitStatus('success');
      
      // Auto-close after success
      setTimeout(() => {
        onClose();
        resetForm();
      }, 2500);
      
    } catch (error) {
      setSubmitStatus('error');
    } finally {
      setIsSubmitting(false);
    }
  };

  const resetForm = () => {
    setFormData({
      projectType: '',
      companyInfo: '',
      contactName: '',
      email: '',
      phone: '',
      companySize: '',
      projectDescription: '',
      timeline: '',
      budgetRange: ''
    });
    setErrors({});
    setSubmitStatus('idle');
    setIsSubmitting(false);
  };

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div 
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4"
      style={{
        background: 'rgba(0, 0, 0, 0.6)',
        backdropFilter: 'blur(12px)',
        animation: 'fadeIn 0.3s ease-in-out'
      }}
      onClick={handleOverlayClick}
    >
      <div 
        className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto"
        style={{
          background: 'rgba(255, 255, 255, 0.15)',
          backdropFilter: 'blur(25px)',
          border: '1px solid rgba(255, 255, 255, 0.2)',
          borderRadius: '24px',
          boxShadow: '0 25px 50px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(255, 255, 255, 0.05) inset',
          padding: '40px 36px',
          animation: 'scaleIn 0.4s ease-out'
        }}
      >
        {/* Header */}
        <div className="flex justify-between items-start mb-8">
          <div>
            <h2 className="text-2xl md:text-3xl font-bold text-white mb-3">
              Let's Build Something Amazing Together
            </h2>
            <p className="text-white/80 text-lg">
              Tell us about your AI vision and we'll show you what's possible.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-white/60 hover:text-white transition-colors p-2 hover:bg-white/10 rounded-full"
            aria-label="Close modal"
          >
            <X size={24} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Project Type */}
          <div>
            <label className="block text-white/90 text-sm font-semibold mb-2">
              What type of AI solution do you need? <span className="text-[#FF6B35]">*</span>
            </label>
            <select
              name="projectType"
              value={formData.projectType}
              onChange={handleInputChange}
              className={`w-full px-5 py-4 rounded-xl text-white transition-all duration-300 appearance-none bg-white/10 border-2 focus:outline-none ${
                errors.projectType 
                  ? 'border-red-500 bg-red-500/10' 
                  : 'border-white/20 focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)]'
              }`}
              style={{
                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%23FF6B35' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                backgroundPosition: 'right 1rem center',
                backgroundRepeat: 'no-repeat',
                backgroundSize: '1.5em 1.5em'
              }}
            >
              <option value="">Select project type</option>
              <option value="creative-ai">Creative AI (Images, Videos, Content)</option>
              <option value="automation">Business Automation (Chatbots, Workflows)</option>
              <option value="custom-apps">Custom AI Applications</option>
              <option value="full-transformation">Complete AI Transformation</option>
              <option value="consultation">Not sure - need consultation</option>
            </select>
            {errors.projectType && <p className="text-red-400 text-sm mt-1">{errors.projectType}</p>}
          </div>

          {/* Company Information */}
          <div>
            <label className="block text-white/90 text-sm font-semibold mb-2">
              Tell us about your company <span className="text-[#FF6B35]">*</span>
            </label>
            <input
              type="text"
              name="companyInfo"
              value={formData.companyInfo}
              onChange={handleInputChange}
              placeholder="Company name and industry"
              className={`w-full px-5 py-4 rounded-xl text-white placeholder-white/60 transition-all duration-300 bg-white/10 border-2 focus:outline-none ${
                errors.companyInfo 
                  ? 'border-red-500 bg-red-500/10' 
                  : 'border-white/20 focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)] focus:translate-y-[-1px]'
              }`}
            />
            {errors.companyInfo && <p className="text-red-400 text-sm mt-1">{errors.companyInfo}</p>}
          </div>

          {/* Contact Details Row */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-white/90 text-sm font-semibold mb-2">
                Your Name <span className="text-[#FF6B35]">*</span>
              </label>
              <input
                type="text"
                name="contactName"
                value={formData.contactName}
                onChange={handleInputChange}
                placeholder="Full name"
                className={`w-full px-5 py-4 rounded-xl text-white placeholder-white/60 transition-all duration-300 bg-white/10 border-2 focus:outline-none ${
                  errors.contactName 
                    ? 'border-red-500 bg-red-500/10' 
                    : 'border-white/20 focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)] focus:translate-y-[-1px]'
                }`}
              />
              {errors.contactName && <p className="text-red-400 text-sm mt-1">{errors.contactName}</p>}
            </div>
            <div>
              <label className="block text-white/90 text-sm font-semibold mb-2">
                Email Address <span className="text-[#FF6B35]">*</span>
              </label>
              <input
                type="email"
                name="email"
                value={formData.email}
                onChange={handleInputChange}
                placeholder="you@company.com"
                className={`w-full px-5 py-4 rounded-xl text-white placeholder-white/60 transition-all duration-300 bg-white/10 border-2 focus:outline-none ${
                  errors.email 
                    ? 'border-red-500 bg-red-500/10' 
                    : 'border-white/20 focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)] focus:translate-y-[-1px]'
                }`}
              />
              {errors.email && <p className="text-red-400 text-sm mt-1">{errors.email}</p>}
            </div>
          </div>

          {/* Additional Info Row */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-white/90 text-sm font-semibold mb-2">
                Phone Number
              </label>
              <input
                type="tel"
                name="phone"
                value={formData.phone}
                onChange={handleInputChange}
                placeholder="+1 (555) 123-4567"
                className="w-full px-5 py-4 rounded-xl text-white placeholder-white/60 transition-all duration-300 bg-white/10 border-2 border-white/20 focus:outline-none focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)] focus:translate-y-[-1px]"
              />
            </div>
            <div>
              <label className="block text-white/90 text-sm font-semibold mb-2">
                Company Size
              </label>
              <select
                name="companySize"
                value={formData.companySize}
                onChange={handleInputChange}
                className="w-full px-5 py-4 rounded-xl text-white transition-all duration-300 appearance-none bg-white/10 border-2 border-white/20 focus:outline-none focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)]"
                style={{
                  backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%23FF6B35' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                  backgroundPosition: 'right 1rem center',
                  backgroundRepeat: 'no-repeat',
                  backgroundSize: '1.5em 1.5em'
                }}
              >
                <option value="">Select size</option>
                <option value="startup">Startup (1-10)</option>
                <option value="small">Small (11-50)</option>
                <option value="medium">Medium (51-200)</option>
                <option value="large">Large (200+)</option>
              </select>
            </div>
          </div>

          {/* Project Description */}
          <div>
            <label className="block text-white/90 text-sm font-semibold mb-2">
              Describe your AI project vision <span className="text-[#FF6B35]">*</span>
            </label>
            <textarea
              name="projectDescription"
              value={formData.projectDescription}
              onChange={handleInputChange}
              rows={4}
              placeholder="What problem are you solving? What's your ideal outcome?"
              className={`w-full px-5 py-4 rounded-xl text-white placeholder-white/60 transition-all duration-300 resize-none bg-white/10 border-2 focus:outline-none ${
                errors.projectDescription 
                  ? 'border-red-500 bg-red-500/10' 
                  : 'border-white/20 focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)] focus:translate-y-[-1px]'
              }`}
            />
            {errors.projectDescription && <p className="text-red-400 text-sm mt-1">{errors.projectDescription}</p>}
          </div>

          {/* Timeline */}
          <div>
            <label className="block text-white/90 text-sm font-semibold mb-2">
              When do you want to start?
            </label>
            <select
              name="timeline"
              value={formData.timeline}
              onChange={handleInputChange}
              className="w-full px-5 py-4 rounded-xl text-white transition-all duration-300 appearance-none bg-white/10 border-2 border-white/20 focus:outline-none focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)]"
              style={{
                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%23FF6B35' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                backgroundPosition: 'right 1rem center',
                backgroundRepeat: 'no-repeat',
                backgroundSize: '1.5em 1.5em'
              }}
            >
              <option value="">Select timeline</option>
              <option value="immediately">Immediately</option>
              <option value="1-month">Within 1 month</option>
              <option value="3-months">Within 3 months</option>
              <option value="6-months">Within 6 months</option>
              <option value="exploring">Just exploring options</option>
            </select>
          </div>

          {/* Budget Range */}
          <div>
            <label className="block text-white/90 text-sm font-semibold mb-2">
              Investment range (Optional)
            </label>
            <select
              name="budgetRange"
              value={formData.budgetRange}
              onChange={handleInputChange}
              className="w-full px-5 py-4 rounded-xl text-white transition-all duration-300 appearance-none bg-white/10 border-2 border-white/20 focus:outline-none focus:border-[#FF6B35] focus:bg-white/15 focus:shadow-[0_0_0_3px_rgba(255,107,53,0.2)]"
              style={{
                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%23FF6B35' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                backgroundPosition: 'right 1rem center',
                backgroundRepeat: 'no-repeat',
                backgroundSize: '1.5em 1.5em'
              }}
            >
              <option value="">Prefer not to say</option>
              <option value="under-10k">Under $10K</option>
              <option value="10k-50k">$10K - $50K</option>
              <option value="50k-100k">$50K - $100K</option>
              <option value="100k-plus">$100K+</option>
              <option value="discuss">Prefer to discuss</option>
            </select>
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            disabled={isSubmitting}
            className={`w-full px-8 py-4 rounded-xl font-semibold text-white transition-all duration-300 mt-8 ${
              submitStatus === 'success'
                ? 'bg-gradient-to-r from-green-500 to-green-400'
                : submitStatus === 'error'
                ? 'bg-gradient-to-r from-red-500 to-red-400'
                : 'bg-gradient-to-r from-[#FF6B35] to-[#FF8C42] hover:from-[#FF8C42] hover:to-[#FFA055] hover:translate-y-[-2px] hover:scale-[1.02] hover:shadow-[0_12px_35px_rgba(255,107,53,0.4)]'
            } ${
              isSubmitting || submitStatus === 'success' 
                ? 'opacity-80 cursor-not-allowed' 
                : 'cursor-pointer'
            }`}
          >
            {submitStatus === 'success' 
              ? 'Message Sent Successfully! ✓' 
              : submitStatus === 'error'
              ? 'Error - Please Try Again'
              : isSubmitting 
              ? 'Sending...' 
              : 'Send My Project Details'
            }
          </button>
        </form>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        
        @keyframes scaleIn {
          from { 
            opacity: 0; 
            transform: scale(0.9); 
          }
          to { 
            opacity: 1; 
            transform: scale(1); 
          }
        }

        /* Custom scrollbar */
        div::-webkit-scrollbar {
          width: 6px;
        }

        div::-webkit-scrollbar-track {
          background: transparent;
        }

        div::-webkit-scrollbar-thumb {
          background: linear-gradient(180deg, #FF6B35 0%, #FF8C42 100%);
          border-radius: 6px;
          border: 1px solid rgba(255, 255, 255, 0.1);
        }

        div::-webkit-scrollbar-thumb:hover {
          background: linear-gradient(180deg, #FF8C42 0%, #FFA055 100%);
        }

        /* Select dropdown options styling */
        select option {
          background: rgba(30, 30, 30, 0.95);
          color: #ffffff;
        }

        select option:hover {
          background: rgba(255, 107, 53, 0.2);
        }
      `}</style>
    </div>
  );
};

export default ContactModal;