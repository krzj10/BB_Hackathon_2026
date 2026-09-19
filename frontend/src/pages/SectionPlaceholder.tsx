import type { LucideIcon } from "lucide-react";
import { Badge } from "../components/ui/badge";

type SectionPlaceholderProps = {
  title: string;
  description: string;
  icon: LucideIcon;
};

/*
 * Quiet holding surface for the five sections whose product screens
 * arrive with B02B. Consistent tokens, centered, no fake content.
 */
export function SectionPlaceholder({ title, description, icon: Icon }: SectionPlaceholderProps) {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col items-center px-6 py-24 text-center">
      <span className="grid size-12 place-items-center rounded-xl border border-border/70 bg-surface text-muted-foreground">
        <Icon className="size-5" strokeWidth={1.5} aria-hidden />
      </span>
      <h2 className="mt-5 text-[17px] font-semibold tracking-tight">{title}</h2>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
        {description}
      </p>
      <Badge variant="outline" className="mt-5">
        Arrives with B02B
      </Badge>
    </div>
  );
}
