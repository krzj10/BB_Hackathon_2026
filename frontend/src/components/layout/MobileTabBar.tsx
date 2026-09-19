import { NavLink } from "react-router-dom";
import { NAV_ALL } from "./nav-items";
import { cn } from "../../lib/utils";

export function MobileTabBar() {
  return (
    <nav
      aria-label="Primary mobile"
      className="fixed inset-x-0 bottom-0 z-40 flex h-14 items-stretch justify-around border-t border-border/60 bg-surface/95 backdrop-blur md:hidden"
    >
      {NAV_ALL.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) =>
            cn(
              "flex flex-1 flex-col items-center justify-center gap-1 transition-colors duration-150 ease-smooth",
              isActive ? "text-primary" : "text-muted-foreground"
            )
          }
        >
          <item.icon className="size-5" strokeWidth={1.75} aria-hidden />
          <span className="text-[10px] font-medium leading-none">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
