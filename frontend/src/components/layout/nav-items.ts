import {
  Bell,
  Calendar,
  CalendarDays,
  FileText,
  Library,
  ListChecks,
  Settings,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
};

export const NAV_SECTIONS: { heading: string; items: NavItem[] }[] = [
  {
    heading: "Workspace",
    items: [
      { to: "/", label: "Today", icon: CalendarDays },
      { to: "/calendar", label: "Calendar", icon: Calendar },
    ],
  },
  {
    heading: "Intelligence",
    items: [
      { to: "/briefings", label: "Briefings", icon: FileText },
      { to: "/attention", label: "Attention", icon: Bell },
      { to: "/decisions", label: "Decisions", icon: ListChecks },
    ],
  },
  {
    heading: "Library",
    items: [{ to: "/knowledge", label: "Knowledge", icon: Library }],
  },
];

export const NAV_SETTINGS: NavItem = {
  to: "/settings",
  label: "Settings",
  icon: Settings,
};

export const NAV_ALL: NavItem[] = [
  ...NAV_SECTIONS.flatMap((section) => section.items),
  NAV_SETTINGS,
];

export function resolveNavTitle(pathname: string): string {
  const match = NAV_ALL.find((item) =>
    item.to === "/" ? pathname === "/" : pathname.startsWith(item.to)
  );
  return match?.label ?? "EVA";
}
