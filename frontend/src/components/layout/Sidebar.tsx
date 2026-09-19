import { NavLink } from "react-router-dom";
import { NAV_SECTIONS, NAV_SETTINGS, type NavItem } from "./nav-items";
import { Badge } from "../ui/badge";
import { cn } from "../../lib/utils";

function NavLinkItem({ item }: { item: NavItem }) {
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 rounded-control px-2.5 py-[7px] text-[13.5px] transition-colors duration-150 ease-smooth",
          isActive
            ? "bg-accent font-medium text-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
        )
      }
    >
      <item.icon className="size-4 shrink-0 opacity-80" strokeWidth={1.75} aria-hidden />
      {item.label}
    </NavLink>
  );
}

export function Sidebar() {
  return (
    <aside aria-label="Primary" className="hidden h-svh w-60 shrink-0 flex-col border-r border-border/60 bg-surface md:flex">
      <div className="flex h-14 shrink-0 items-center gap-2.5 px-5">
        <span
          aria-hidden
          className="size-5 rounded-full bg-orb shadow-[inset_0_1px_3px_oklch(1_0_0/0.3)] ring-1 ring-inset ring-black/10"
        />
        <span className="text-[15px] font-semibold tracking-tight">EVA</span>
        <Badge variant="outline" className="ml-auto">
          Preview
        </Badge>
      </div>

      <nav aria-label="Primary sections" className="flex-1 overflow-y-auto px-3 py-2">
        {NAV_SECTIONS.map((section) => (
          <div key={section.heading} className="mb-5">
            <p className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
              {section.heading}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavLinkItem key={item.to} item={item} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-border/60 p-3">
        <NavLinkItem item={NAV_SETTINGS} />
        <p className="px-2.5 pt-2 text-[11px] text-subtle-foreground">
          B02A · visual foundation
        </p>
      </div>
    </aside>
  );
}
