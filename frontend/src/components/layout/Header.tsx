import { useLocation } from "react-router-dom";
import { resolveNavTitle } from "./nav-items";
import { ThemeToggle } from "../theme/ThemeToggle";

const warshawDate = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  timeZone: "Europe/Warsaw",
});

export function Header() {
  const { pathname } = useLocation();
  const title = resolveNavTitle(pathname);
  const date = warshawDate.format(new Date());

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-background/80 px-5 backdrop-blur lg:px-8">
      <h1 className="text-[15px] font-semibold tracking-tight">{title}</h1>
      <div className="flex items-center gap-3">
        <span className="hidden text-[13px] text-muted-foreground sm:inline">
          {date} · Warsaw
        </span>
        <span aria-hidden className="hidden h-4 w-px bg-border sm:block" />
        <ThemeToggle />
      </div>
    </header>
  );
}
