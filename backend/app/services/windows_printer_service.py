from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from app.core.config import PrinterSettings, get_settings
from app.core.errors import AppError
from app.core.libreoffice import configured_libreoffice_path


class WindowsPrinterService:
    def __init__(self, printer: PrinterSettings | None = None) -> None:
        self._office_path = Path(
            configured_libreoffice_path()
            or r"C:\Program Files\LibreOffice\program\soffice.exe"
        )
        self._name_pattern = (printer or get_settings().printer).name_pattern

    def status(self) -> dict[str, Any]:
        script = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$printer = Get-CimInstance Win32_Printer |
  Where-Object { $_.Name -like $env:LLM_OCR_PRINTER_NAME_PATTERN } |
  Sort-Object @{Expression={$_.Default};Descending=$true}, Name |
  Select-Object -First 1
if ($null -eq $printer) {
  [PSCustomObject]@{found=$false} | ConvertTo-Json -Compress
} else {
  [PSCustomObject]@{
    found=$true
    printerName=$printer.Name
    isDefault=[bool]$printer.Default
    workOffline=[bool]$printer.WorkOffline
    statusCode=[int]$printer.PrinterStatus
    portName=$printer.PortName
  } | ConvertTo-Json -Compress
}
"""
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                # The pattern goes in as data, never as part of the script text.
                env={**os.environ, "LLM_OCR_PRINTER_NAME_PATTERN": self._name_pattern},
                capture_output=True,
                check=False,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return self._disconnected("Windowsのプリンター情報を取得できませんでした。")
        if completed.returncode != 0:
            return self._disconnected("Windowsのプリンター情報を取得できませんでした。")
        try:
            payload = json.loads(completed.stdout.strip())
        except (TypeError, ValueError):
            return self._disconnected("EPSONプリンターの状態を判定できませんでした。")
        if not isinstance(payload, dict) or not payload.get("found"):
            return self._disconnected("EPSONプリンターがWindowsに登録されていません。")
        status_code = int(payload.get("statusCode") or 0)
        work_offline = bool(payload.get("workOffline"))
        connected = not work_offline and status_code in {0, 3, 4, 5}
        state = {3: "待機中", 4: "印刷中", 5: "準備中"}.get(
            status_code,
            "接続状態不明" if connected else "オフライン",
        )
        return {
            "printerName": str(payload.get("printerName") or "EPSON"),
            "connected": connected,
            "state": state,
            "detail": (
                "EPSONプリンターへ印刷できます。"
                if connected
                else "EPSONプリンターに接続されていません。"
            ),
            "portName": str(payload.get("portName") or ""),
            "statusCode": status_code,
        }

    def print_bytes(self, content: bytes, *, suffix: str, copies: int) -> dict[str, Any]:
        if copies < 1 or copies > 3:
            raise AppError(
                status=422,
                code="PRINT_COPY_COUNT_INVALID",
                title="Print copy count is invalid",
                detail="印刷部数は1部から3部までにしてください。",
            )
        printer = self.status()
        if not printer["connected"]:
            raise AppError(
                status=409,
                code="EPSON_PRINTER_DISCONNECTED",
                title="EPSON printer is disconnected",
                detail=str(printer["detail"]),
            )
        if not self._office_path.is_file():
            raise AppError(
                status=503,
                code="PRINT_ENGINE_UNAVAILABLE",
                title="Print engine is unavailable",
                detail="印刷エンジンを利用できません。",
            )
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        with tempfile.TemporaryDirectory(prefix="llm-ocr-print-") as directory:
            source = Path(directory) / f"document{normalized_suffix}"
            source.write_bytes(content)
            for _ in range(copies):
                completed = subprocess.run(
                    [
                        str(self._office_path),
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nofirststartwizard",
                        "--pt",
                        str(printer["printerName"]),
                        str(source),
                    ],
                    capture_output=True,
                    check=False,
                    timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode != 0:
                    raise AppError(
                        status=502,
                        code="PRINT_SUBMISSION_FAILED",
                        title="Print submission failed",
                        detail="EPSONプリンターへ印刷データを送信できませんでした。",
                    )
        return {
            "printerName": printer["printerName"],
            "connected": True,
            "copies": copies,
            "submitted": True,
            "detail": f"{printer['printerName']}へ{copies}部の印刷データを送信しました。",
        }

    @staticmethod
    def _disconnected(detail: str) -> dict[str, Any]:
        return {
            "printerName": None,
            "connected": False,
            "state": "未接続",
            "detail": detail,
            "portName": None,
            "statusCode": None,
        }
