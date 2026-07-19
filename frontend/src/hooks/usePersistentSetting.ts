import { useEffect, useState } from "react";

export function usePersistentSetting<T extends string>(key: string, initialValue: T) {
  const [value, setValue] = useState<T>(() => (localStorage.getItem(key) as T | null) ?? initialValue);
  useEffect(() => { localStorage.setItem(key, value); }, [key, value]);
  return [value, setValue] as const;
}

