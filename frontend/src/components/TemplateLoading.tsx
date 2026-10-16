export function TemplateLoading({ message }: { message: string }) {
  return <div className="template-loading" role="status" aria-live="polite">
    <div className="template-loading-art" aria-hidden="true">
      <span className="template-loading-sheet"><i /><i /><i /></span>
      <span className="template-loading-orbit" />
    </div>
    <strong>テンプレートを準備しています</strong>
    <span>{message}</span>
    <div className="template-loading-track" aria-hidden="true"><i /></div>
  </div>;
}
