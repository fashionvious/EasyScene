import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap text-base font-semibold transition-all disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive wise-button-hover",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground rounded-[9999px] px-4 py-[5px] hover:scale-105 active:scale-95",
        destructive:
          "bg-destructive text-white rounded-[9999px] px-4 py-[5px] hover:scale-105 active:scale-95 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40",
        outline:
          "border bg-background rounded-[9999px] px-4 py-[5px] wise-ring-shadow hover:scale-105 active:scale-95 dark:bg-input/30 dark:border-input",
        secondary:
          "bg-secondary text-secondary-foreground rounded-[9999px] px-4 py-2 hover:scale-105 active:scale-95",
        ghost:
          "rounded-[9999px] hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50 hover:scale-105 active:scale-95",
        link: "text-primary underline-offset-4 hover:underline",
        wise: "bg-[#9fe870] text-[#163300] rounded-[9999px] px-4 py-[5px] hover:scale-105 active:scale-95 font-semibold",
        "wise-secondary": "bg-[rgba(22,51,0,0.08)] text-[#0e0f0c] dark:text-white rounded-[9999px] px-4 py-2 hover:scale-105 active:scale-95 font-semibold",
      },
      size: {
        default: "h-auto",
        sm: "h-auto text-sm px-3 py-1.5 gap-1.5",
        lg: "h-auto text-lg px-6 py-2.5",
        icon: "size-9 rounded-full",
        "icon-sm": "size-8 rounded-full",
        "icon-lg": "size-10 rounded-full",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot : "button"

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
