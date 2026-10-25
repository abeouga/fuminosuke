import type { SVGProps } from "react";

// 自作のファイルの印（外部のアイコン集を使わない）。紙の形に、表のマス又は文の行を抜いて見せる。
const SHEET = "M3.5 1H10l3.5 3.5V14a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1zM10 1v3.5h3.5z";

function FileIcon({ holes, ...props }: SVGProps<SVGSVGElement> & { holes: string }) {
  return <svg viewBox="0 0 16 16" width="1em" height="1em" fill="currentColor" focusable="false" {...props}>
    <path fillRule="evenodd" d={SHEET + holes} />
  </svg>;
}

// 表のマス（2列×3行）
export function XlsxFileIcon(props: SVGProps<SVGSVGElement>) {
  return <FileIcon holes="M4 6.5h3V8H4zM8 6.5h3V8H8zM4 9h3v1.5H4zM8 9h3v1.5H8zM4 11.5h3V13H4zM8 11.5h3V13H8z" {...props} />;
}

// 文の行（3行、最後は短い）
export function PdfFileIcon(props: SVGProps<SVGSVGElement>) {
  return <FileIcon holes="M4 6.5h7v1.2H4zM4 9h7v1.2H4zM4 11.5h4.5v1.2H4z" {...props} />;
}
