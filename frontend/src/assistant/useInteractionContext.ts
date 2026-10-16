import { useRef, useState } from "react";

/** Epochs also distinguish A -> B -> A and edit -> undo, not just equal values. */
export function useInteractionContext(selectionKey: string, editKey: string) {
  const [viewInstanceId] = useState(() => crypto.randomUUID());
  const previous = useRef({ selectionKey, editKey, selectionEpoch: 0, localEditEpoch: 0 });
  if (previous.current.selectionKey !== selectionKey) {
    previous.current.selectionKey = selectionKey;
    previous.current.selectionEpoch += 1;
  }
  if (previous.current.editKey !== editKey) {
    previous.current.editKey = editKey;
    previous.current.localEditEpoch += 1;
  }
  return { viewInstanceId, selectionEpoch: previous.current.selectionEpoch, localEditEpoch: previous.current.localEditEpoch };
}
