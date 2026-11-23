import { useEffect, useRef, useState } from "react";

export function useEditable(serverText: string | undefined, resetKey?: string | number) {
  const [text, setText] = useState(serverText ?? "");
  const [dirty, setDirty] = useState(false);
  const lastKeyRef = useRef<string | number | undefined>(resetKey);
  const lastServerRef = useRef<string | undefined>(serverText);

  useEffect(() => {
    // Hard reset when switching chapters or books — prevents stale chapter prose leaking into next chapter!
    if (resetKey !== lastKeyRef.current) {
      lastKeyRef.current = resetKey;
      lastServerRef.current = serverText;
      setText(serverText ?? "");
      setDirty(false);
      return;
    }
    if (serverText === undefined) return;
    const prevServer = lastServerRef.current;
    lastServerRef.current = serverText;
    // Only overwrite local edits if user has NOT dirtied the editor, or server matched before
    if (!dirty || text === prevServer) {
      setText(serverText);
      setDirty(false);
    }
  }, [serverText, resetKey, dirty, text]);

  const setLocal = (v: string) => {
    setText(v);
    setDirty(v !== (lastServerRef.current ?? ""));
  };

  const acceptExternal = (v: string) => {
    lastServerRef.current = v;
    setText(v);
    setDirty(false);
  };

  const markSaved = (v?: string) => {
    const val = v ?? text;
    lastServerRef.current = val;
    setText(val);
    setDirty(false);
  };

  return { text, dirty, setLocal, acceptExternal, markSaved };
}

export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}
