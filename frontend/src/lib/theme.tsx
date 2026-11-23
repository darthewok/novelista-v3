import React, { createContext, useContext, useEffect, useState } from "react";

export type ThemeId = "paper" | "ink" | "daylight";

interface ThemeCtx {
  theme: ThemeId;
  setTheme: (t: ThemeId) => void;
  proseSize: number;
  setProseSize: (sz: number) => void;
}

const Ctx = createContext<ThemeCtx>({
  theme: "paper",
  setTheme: () => {},
  proseSize: 17,
  setProseSize: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    try {
      const saved = localStorage.getItem("novelista3.theme") as ThemeId | null;
      return saved && ["paper", "ink", "daylight"].includes(saved) ? saved : "paper";
    } catch {
      return "paper";
    }
  });
  const [proseSize, setProseSizeState] = useState<number>(() => {
    try {
      const s = Number(localStorage.getItem("novelista3.proseSize"));
      return s >= 14 && s <= 22 ? s : 17;
    } catch {
      return 17;
    }
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("novelista3.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const setTheme = (t: ThemeId) => setThemeState(t);
  const setProseSize = (sz: number) => {
    const clamped = Math.max(14, Math.min(22, sz));
    setProseSizeState(clamped);
    try {
      localStorage.setItem("novelista3.proseSize", String(clamped));
    } catch {
      /* ignore */
    }
  };

  return (
    <Ctx.Provider value={{ theme, setTheme, proseSize, setProseSize }}>
      {children}
    </Ctx.Provider>
  );
}

export function useTheme() {
  return useContext(Ctx);
}
