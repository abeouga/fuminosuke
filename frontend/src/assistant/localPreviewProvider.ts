import { CATEGORY_LABEL, type AssistantCategory, type AssistantPreviewProvider } from "./contracts";

/** Deterministic typed previews for phase 2-3. No LLM, HTTP, or persistence. */
export const localPreviewProvider: AssistantPreviewProvider = {
  async preview(request, snapshot, signal) {
    signal.throwIfAborted();
    if (!snapshot.target || snapshot.blocker || snapshot.busy || snapshot.dirty || snapshot.saveError
      || !snapshot.operations.includes(request.operation)) {
      throw new Error("選択対象または編集状態を確認してください。");
    }
    const category = request.operation === "template.change_category";
    if (category && !(request.value && Object.hasOwn(CATEGORY_LABEL, request.value))) throw new Error("欄の種類を選択してください。");
    if (!category && request.value !== null && !request.value.trim()) throw new Error("文字列を入力してください。空欄にする場合は「空欄」を選択してください。");
    if (request.value === null && request.operation !== "ocr.set_value") throw new Error("名称を空欄にはできません。");
    const maxLength = request.operation === "ocr.set_value" ? 500 : request.operation === "template.rename_choice" ? 100 : 300;
    if (request.value !== null && request.value.length > maxLength) throw new Error(`${maxLength}文字以内で入力してください。`);
    const before = category ? snapshot.target.category ?? null : snapshot.target.currentValue;
    const after = category ? CATEGORY_LABEL[request.value as AssistantCategory] : request.value;
    if (before === after) throw new Error("変更前と同じ内容です。");
    return {
      id: crypto.randomUUID(), origin: "local-preview", context: structuredClone(request.context),
      operation: request.operation, target: structuredClone(snapshot.target), before, after,
      ...(request.operation === "template.rename_choice" ? {
        beforeStoredValue: snapshot.target.choiceValue,
        afterStoredValue: snapshot.target.choiceTracksLabel ? request.value! : snapshot.target.choiceValue,
      } : {}),
    };
  },
};
