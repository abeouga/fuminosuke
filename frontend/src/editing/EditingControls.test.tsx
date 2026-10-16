import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EditingControls } from "./EditingControls";
import type { WorkbenchStore } from "./workbenchStore";
import { v2client } from "./v2client";

const audio = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn() }));
vi.mock("./voiceCapture", () => ({ VoiceCapture: class {
  snapshot = { mode: "stopped", message: "", caption: "" };
  subscribe = () => () => {}; getSnapshot = () => this.snapshot;
  start = audio.start; stop = audio.stop;
} }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });
function setup() {
  const snapshot = { state: "ready", pending: 0, canUndo: true, message: "", question: null,
    context: { catalog: { targets: [] }, uiBindings: [], scopeContext: { selectedTableId: null } } };
  const store = { resourceKind: "template", subscribe: () => () => {}, getSnapshot: () => snapshot,
    connectEvents: () => () => {}, onSaved: () => () => {}, subscribeInteraction: () => () => {}, setInteraction: vi.fn(), selectionChanged: vi.fn().mockResolvedValue(undefined) } as unknown as WorkbenchStore;
  const props = { store, pageScope: { mode: "all" as const, pageId: null }, selectedEntities: [] as string[], selectionEpoch: 0, manualActive: false, onSaved: vi.fn() };
  return { ...render(<EditingControls {...props} />), props };
}
it("explains unavailable voice and AI while leaving manual undo available", async () => {
  vi.spyOn(v2client, "status").mockResolvedValue({ enabled: true, plannerConnected: false, voiceConnected: false });
  setup();
  expect(await screen.findByText("音声入力は現在利用できません。手動で編集・保存できます。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "音声入力" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "元に戻す" })).toBeEnabled();
  fireEvent.click(screen.getByText("文字で指示"));
  expect(screen.getByRole("textbox", { name: "編集指示" })).toBeDisabled();
  expect(audio.start).not.toHaveBeenCalled();
});
it("allows continuous input without a selected cell and retains local recording for one cell", async () => {
  vi.spyOn(v2client, "status").mockResolvedValue({ enabled: true, plannerConnected: true, voiceConnected: true });
  audio.start.mockResolvedValue(undefined);
  const view = setup();
  await screen.findByText(/セルを選ばずに話せます/);
  expect(screen.getByRole("button", { name: "音声入力" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "選択欄を録音" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "音声入力" }));
  expect(audio.start).toHaveBeenCalledWith("continuous", view.props.pageScope, [], null);
  view.rerender(<EditingControls {...view.props} selectedEntities={["cell"]} selectionEpoch={1} />);
  expect(screen.getByRole("button", { name: "選択欄を録音" })).toBeEnabled();
});
