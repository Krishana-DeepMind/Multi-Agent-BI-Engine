import React from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StepperProps {
  steps: string[];
  activeStep: number;
  className?: string;
}

export function Stepper({ steps, activeStep, className }: StepperProps) {
  return (
    <div className={cn('flex items-center w-full', className)}>
      {steps.map((step, index) => {
        const isCompleted = index < activeStep;
        const isActive = index === activeStep;
        const isPending = index > activeStep;

        return (
          <React.Fragment key={step}>
            <div className="flex flex-col items-center relative">
              <div
                className={cn(
                  'flex items-center justify-center w-10 h-10 rounded-full border-2 transition-all duration-300',
                  isCompleted
                    ? 'bg-primary border-primary text-primary-foreground'
                    : isActive
                    ? 'bg-background border-primary text-primary ring-4 ring-primary/20 animate-pulse'
                    : 'bg-muted border-muted text-muted-foreground'
                )}
              >
                {isCompleted ? <Check className="w-5 h-5" /> : <span>{index + 1}</span>}
              </div>
              <span
                className={cn(
                  'absolute -bottom-6 text-xs font-medium whitespace-nowrap transition-colors duration-300',
                  isCompleted || isActive ? 'text-foreground' : 'text-muted-foreground'
                )}
              >
                {step}
              </span>
            </div>
            {index < steps.length - 1 && (
              <div
                className={cn(
                  'flex-1 h-[2px] mx-4 transition-colors duration-300',
                  isCompleted ? 'bg-primary' : 'bg-muted'
                )}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
