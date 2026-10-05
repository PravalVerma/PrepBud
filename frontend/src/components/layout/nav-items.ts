export interface NavItem {
  href: string;
  label: string;
  icon: string;
  /** Phase in which the page ships; items without a phase are live. */
  comingInPhase?: number;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "◧" },
  { href: "/upload", label: "Upload material", icon: "⇪" },
  { href: "/concepts", label: "Concepts", icon: "◈" },
  { href: "/session", label: "Study session", icon: "▶" },
  { href: "/goals", label: "Goals", icon: "◎" },
  { href: "/review", label: "Review", icon: "↻" },
  { href: "/progress", label: "Progress", icon: "▤" },
  { href: "/profile", label: "Profile", icon: "◉" },
];
