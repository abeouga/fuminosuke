import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { OcrPageBindings, type PageCatalog } from "./OcrPageBindings";

vi.mock("../api", () => ({ api: { getOcrPages: vi.fn(), createOcrPageJob: vi.fn(), referenceQualification: vi.fn() },
  createIdempotencyKey: () => "test-key" }));
const catalog: PageCatalog = { templateVersionId: "v1", templateRevision: "a".repeat(64),
  available: false, reason: "PRINT_REFERENCE_NOT_VALIDATED", pages: [
    { pageId: "p1", pageNumber: 1, range: "A1:D30", referenceUrl: "/p1.png" },
    { pageId: "p2", pageNumber: 2, range: "A31:D60", referenceUrl: "/p2.png" },
  ] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOcrPages).mockResolvedValue(catalog);
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "UNVERIFIED", qualified: false });
  vi.stubGlobal("URL", { createObjectURL: () => "blob:test", revokeObjectURL: vi.fn() });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("retains an accepted page job for recovery after leaving its input screen", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  vi.stubGlobal("crypto", { randomUUID: () => "input", subtle: { digest: async () => new ArrayBuffer(32) } });
  let finish!: (value: { jobId: string }) => void;
  vi.mocked(api.createOcrPageJob).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const onAccepted = vi.fn();
  const onJobCreated = vi.fn();
  const view = render(<OcrPageBindings versionId="v1" onAccepted={onAccepted} onJobCreated={onJobCreated} />);
  for (const number of [1, 2]) {
    const file = new File(["image"], `${number}.png`, { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new ArrayBuffer(1) });
    fireEvent.change(await screen.findByLabelText(`Page ${number} の画像を選択`), {
      target: { files: [file] },
    });
  }
  fireEvent.click(screen.getByRole("button", { name: "全ページを読み取る" }));
  await waitFor(() => expect(api.createOcrPageJob).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => { finish({ jobId: "job-pages" }); });
  expect(onJobCreated).toHaveBeenCalledExactlyOnceWith("job-pages");
  expect(onAccepted).not.toHaveBeenCalled();
});

it("advances to the missing input, follows resize, ignores replacements and finishes at submit", async () => {
  const original = HTMLElement.prototype.scrollIntoView;
  const targets: HTMLElement[] = [];
  HTMLElement.prototype.scrollIntoView = function () { targets.push(this); };
  try {
    render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
    await screen.findByText("Page 2");
    const first = screen.getByLabelText("Page 1 の画像を選択", { exact: false });
    const second = screen.getByLabelText("Page 2 の画像を選択", { exact: false });
    const choose = (input: HTMLElement, name: string) => fireEvent.change(input, {
      target: { files: [new File(["image"], name, { type: "image/png" })] },
    });
    fireEvent.drop(first.closest("label")!, {
      dataTransfer: { files: [new File(["text"], "memo.txt", { type: "text/plain" })] },
    });
    expect(targets).toHaveLength(0);
    choose(first, "one.png");
    await waitFor(() => expect(targets.at(-1)).toBe(second.closest(".ocr-page-image-picker")));
    const count = targets.length;
    fireEvent(window, new Event("resize"));
    expect(targets).toHaveLength(count + 1);
    expect(targets.at(-1)).toBe(second.closest(".ocr-page-image-picker"));
    choose(first, "replacement.png");
    fireEvent(window, new Event("resize"));
    expect(targets).toHaveLength(count + 1);
    fireEvent.drop(second.closest("label")!, {
      dataTransfer: { files: [new File(["image"], "two.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(targets.at(-1)).toBe(screen.getByRole("button", { name: "全ページを読み取る" }).closest("footer")));
    expect(api.createOcrPageJob).not.toHaveBeenCalled();
  } finally {
    cleanup();
    if (original) HTMLElement.prototype.scrollIntoView = original;
    else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  }
});

it("uses the template name as the page-input heading and provides icon-only back navigation", async () => {
  const onBack = vi.fn();
  render(<OcrPageBindings versionId="v1" templateName="キャリア・プラン作成補助シート（学生用）"
    versionNumber={4} onAccepted={vi.fn()} onBack={onBack} />);
  await screen.findByText("Page 2");

  expect(screen.getByRole("heading", { name: "キャリア・プラン作成補助シート（学生用）" })).toBeInTheDocument();
  expect(screen.queryByText("STEP 2 · PAGE INPUT")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "ページごとに記入済み画像を指定" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "別のテンプレートを選ぶ" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "テンプレート選択画面に戻る" }));
  expect(onBack).toHaveBeenCalledOnce();
});

it("renames the multi-page template from its heading without opening a separate form", async () => {
  const onRenameTemplate = vi.fn().mockResolvedValue("更新後のテンプレート名");
  render(<OcrPageBindings versionId="v1" templateId="template-1" templateName="変更前の名前"
    onRenameTemplate={onRenameTemplate} onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");

  fireEvent.click(screen.getByRole("button", { name: "テンプレート名を変更" }));
  const input = screen.getByRole("textbox", { name: "テンプレート名" });
  fireEvent.change(input, { target: { value: "更新後のテンプレート名" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(await screen.findByRole("heading", { name: "更新後のテンプレート名" })).toBeInTheDocument();
  expect(onRenameTemplate).toHaveBeenCalledOnce();
  expect(onRenameTemplate).toHaveBeenCalledWith("template-1", "更新後のテンプレート名");
});

it("opens each page reference in the shared large preview and closes it with Escape", async () => {
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");

  const trigger = screen.getByRole("button", { name: "Page 2 基準画像を拡大表示" });
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "Page 2 基準画像" });
  expect(dialog).toBeInTheDocument();
  expect(within(dialog).getByRole("img", { name: "Page 2 基準画像" })).toHaveAttribute("src", "/p2.png");
  await waitFor(() => expect(document.body.style.overflow).toBe("hidden"));
  const close = within(dialog).getByRole("button", { name: "プレビューを閉じる" });
  for (const shiftKey of [false, true]) {
    const event = new KeyboardEvent("keydown", { key: "Tab", shiftKey, bubbles: true, cancelable: true });
    close.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(close).toHaveFocus();
  }

  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(document.body.style.overflow).toBe("");
  expect(trigger).toHaveFocus();
});

it("shows page numbers without workbook ranges and blocks unqualified reference even with all inputs", async () => {
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");
  expect(screen.queryByText("A31:D60")).not.toBeInTheDocument();
  expect(screen.queryByText(/読み取り結果は確認前の候補です/)).not.toBeInTheDocument();
  expect(screen.queryByText(/筆画の欠落・隣欄の混入は自動検証できない/)).not.toBeInTheDocument();
  expect(screen.queryByText("基準画像", { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByText("入力画像が未指定です")).not.toBeInTheDocument();
  for (const number of [2, 1]) {
    fireEvent.change(screen.getByLabelText(`Page ${number} の画像を選択`, { exact: false }), {
      target: { files: [new File(["image"], `${number}.png`, { type: "image/png" })] },
    });
  }
  expect(screen.queryByText("入力画像を指定済み")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});

it("does not start a qualified fixture when page 2 is missing", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 1");
  fireEvent.change(screen.getByLabelText("Page 1 の画像を選択", { exact: false }), {
    target: { files: [new File(["image"], "1.png", { type: "image/png" })] },
  });
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
});

it("accepts one Explorer-style dropped image for the target page only", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");

  const dropped = new File(["page-one"], "page-one.png", { type: "image/png" });
  fireEvent.drop(screen.getByLabelText("Page 1 の画像を選択"), {
    dataTransfer: { files: [dropped] },
  });

  expect(screen.getByLabelText("画像選択 1 / 2 ページ")).toBeInTheDocument();
  expect(screen.getByAltText("Page 1 選択画像")).toBeInTheDocument();
  expect(screen.queryByText("入力画像を指定済み")).not.toBeInTheDocument();
  expect(screen.queryByText("入力画像が未指定です")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
});

it("rejects multiple or unsupported files without changing the page binding", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");
  const picker = screen.getByLabelText("Page 1 の画像を選択");

  fireEvent.drop(picker, { dataTransfer: { files: [
    new File(["one"], "one.png", { type: "image/png" }),
    new File(["two"], "two.png", { type: "image/png" }),
  ] } });
  expect(screen.getByRole("alert")).toHaveTextContent("1ページにつき画像を1ファイル");
  expect(screen.getByLabelText("画像選択 0 / 2 ページ")).toBeInTheDocument();

  fireEvent.drop(picker, { dataTransfer: { files: [new File(["text"], "memo.txt", { type: "text/plain" })] } });
  expect(screen.getByRole("alert")).toHaveTextContent("PNG / JPEG / WebP / TIFF");
  expect(screen.getByLabelText("画像選択 0 / 2 ページ")).toBeInTheDocument();
});

it("rejects an image larger than 20 MB", async () => {
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");
  const oversized = new File(["image"], "large.tiff", { type: "image/tiff" });
  Object.defineProperty(oversized, "size", { value: 20 * 1024 * 1024 + 1 });

  fireEvent.drop(screen.getByLabelText("Page 1 の画像を選択"), {
    dataTransfer: { files: [oversized] },
  });

  expect(screen.getByRole("alert")).toHaveTextContent("20 MB以下");
  expect(screen.getByLabelText("画像選択 0 / 2 ページ")).toBeInTheDocument();
});

it("reports catalog failure without offering a legacy page 1 fallback", async () => {
  vi.mocked(api.getOcrPages).mockRejectedValue(new Error("ページ構成の取得失敗"));
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("ページ構成の取得失敗");
  expect(screen.queryByRole("button", { name: "全ページを読み取る" })).not.toBeInTheDocument();
});

it("keeps admission closed even when the formal qualification is PASS", async () => {
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "PASS", qualified: true,
    qualificationIssued: true, environmentQualified: true, geometryQualified: true,
    expiresAt: Date.now() / 1000 + 3600 });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  expect(await screen.findByText(/現在読み取りを開始できません/)).toHaveTextContent("PRINT_REFERENCE_NOT_VALIDATED");
  expect(api.referenceQualification).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});

it.each([true, false])("uses only the catalog availability=%s without a second qualification request", async (available) => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available });
  vi.mocked(api.referenceQualification).mockResolvedValue({
    status: "CANDIDATE_READY", qualificationPolicy: "paged-candidate-readiness-v2",
    qualified: false, qualificationIssued: false, reviewRequired: true, geometryQualified: true,
    validUntilChange: true, issuedAt: Date.now() / 1000 - 1, expiresAt: null,
    codes: [], unresolvedBlockers: [], productionBlockers: [],
  });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  expect(api.referenceQualification).not.toHaveBeenCalled();
  expect(screen.queryByText(/この版の正式資格を確認できません/)).not.toBeInTheDocument();
  await screen.findByText("Page 2");
  for (const number of [1, 2]) {
    fireEvent.change(screen.getByLabelText(`Page ${number} の画像を選択`, { exact: false }), {
      target: { files: [new File(["image"], `${number}.png`, { type: "image/png" })] },
    });
  }
  const submit = screen.getByRole("button", { name: "全ページを読み取る" });
  if (available) expect(submit).toBeEnabled();
  else expect(submit).toBeDisabled();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});

it("rejects a catalog belonging to a different version and allows read-only retry", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValueOnce({ ...catalog, templateVersionId: "other" });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("ページ構成の版が一致しません");
  expect(screen.queryByText("Page 1")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "ページ構成を再取得" }));
  expect(await screen.findByText("Page 1")).toBeInTheDocument();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});

it("submits explicit page bindings once and keeps a successful submission locked", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValueOnce("input-2").mockReturnValueOnce("input-1"),
    subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array([1, 2]).buffer) } });
  const accepted = vi.fn();
  const back = vi.fn();
  let resolve!: (value: { jobId: string }) => void;
  vi.mocked(api.createOcrPageJob).mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<OcrPageBindings versionId="v1" onAccepted={accepted} onBack={back} />);
  await screen.findByText("Page 2");
  for (const number of [2, 1]) {
    const file = new File([`image-${number}`], `${number}.png`, { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new ArrayBuffer(1) });
    fireEvent.change(screen.getByLabelText(`Page ${number} の画像を選択`, { exact: false }), { target: { files: [file] } });
  }
  fireEvent.click(screen.getByRole("button", { name: "全ページを読み取る" }));
  await waitFor(() => expect(api.createOcrPageJob).toHaveBeenCalledOnce());
  expect(screen.getByRole("button", { name: "テンプレート選択画面に戻る" })).toBeDisabled();
  const form = vi.mocked(api.createOcrPageJob).mock.calls[0][0];
  const manifest = JSON.parse(String(form.get("manifest")));
  expect(manifest).toMatchObject({ templateVersionId: "v1", templateRevision: catalog.templateRevision,
    uploadIds: ["input-1", "input-2"], bindings: [
      { templatePageId: "p1", inputId: "input-1", inputRevision: 1 },
      { templatePageId: "p2", inputId: "input-2", inputRevision: 1 },
    ] });
  resolve({ jobId: "accepted-job" });
  await waitFor(() => expect(accepted).toHaveBeenCalledWith("accepted-job"));
  expect(screen.getByRole("button", { name: "全ページの読み取りを受け付けました" })).toBeDisabled();
  expect(api.createOcrPageJob).toHaveBeenCalledOnce();
});

it("does not send the old version when selection changes during image hashing", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValueOnce({ ...catalog, available: true })
    .mockResolvedValueOnce({ ...catalog, templateVersionId: "v2", available: false });
  let finishHash!: (value: ArrayBuffer) => void;
  const digest = vi.fn().mockReturnValue(new Promise<ArrayBuffer>((done) => { finishHash = done; }));
  vi.stubGlobal("crypto", { randomUUID: () => "input", subtle: { digest } });
  const accepted = vi.fn();
  const { rerender } = render(<OcrPageBindings versionId="v1" onAccepted={accepted} />);
  await screen.findByText("Page 2");
  for (const number of [1, 2]) {
    const file = new File(["image"], `${number}.png`, { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new ArrayBuffer(1) });
    fireEvent.change(screen.getByLabelText(`Page ${number} の画像を選択`, { exact: false }), { target: { files: [file] } });
  }
  fireEvent.click(screen.getByRole("button", { name: "全ページを読み取る" }));
  await waitFor(() => expect(digest).toHaveBeenCalledTimes(2));
  rerender(<OcrPageBindings versionId="v2" onAccepted={accepted} />);
  finishHash(new ArrayBuffer(32));
  await screen.findByText(/現在読み取りを開始できません/);
  expect(screen.queryByText("入力画像が未指定です")).not.toBeInTheDocument();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
  expect(accepted).not.toHaveBeenCalled();
});

it("does not interpret an expired or unavailable qualification as an admission result", async () => {
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "PASS", qualified: true,
    expiresAt: Date.now() / 1000 - 1 });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2");
  expect(screen.queryByText(/資格/)).not.toBeInTheDocument();
  expect(api.referenceQualification).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});
