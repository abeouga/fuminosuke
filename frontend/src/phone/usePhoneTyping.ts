import { useEffect, useState } from "react";
import { isTextEntry } from "./phoneKeyboard";

/**
 * True while a text field has the focus on a phone, i.e. the on-screen keyboard is up. The bottom
 * tabs hide meanwhile so the keyboard does not push them over the field being typed into. Closing
 * the keyboard takes the focus away (phoneKeyboard.ts), so this ends with it.
 */
export function usePhoneTyping(enabled: boolean): boolean {
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setTyping(false);
      return;
    }
    const update = () => setTyping(isTextEntry(document.activeElement));
    // focusout fires before the next element takes the focus; look once the move has settled.
    const later = () => window.setTimeout(update, 0);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", later);
    update();
    return () => {
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", later);
    };
  }, [enabled]);
  return typing;
}
