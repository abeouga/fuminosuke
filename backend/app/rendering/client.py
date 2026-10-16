"""Streaming Unix-socket client with a revocable connection lease."""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any

from app.rendering.contract import MAX_FILE, MAX_HEADER, digest, encode, validate_evidence


def receive_line(stream: Any, limit: int = MAX_HEADER) -> dict[str, Any]:
    line = stream.readline(limit + 1)
    if not line.endswith(b"\n") or len(line) > limit:
        raise ValueError("Invalid renderer response header")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("Invalid renderer response")
    return value


def render(workbook: bytes, mode: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    if not 0 < len(workbook) <= MAX_FILE:
        raise ValueError("Invalid renderer input size")
    path = os.environ.get("RENDERER_SOCKET", "/run/llmocr-renderer/renderer.sock")
    image = os.environ.get("RENDERER_IMAGE_ID", "")
    stopped = threading.Event()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(90)
        connection.connect(path)
        connection.sendall(
            encode({"mode": mode, "size": len(workbook), "sha256": digest(workbook)})
            + b"\n"
            + workbook
        )

        def heartbeat() -> None:
            while not stopped.wait(0.25):
                try:
                    connection.sendall(b".")
                except OSError:
                    break

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            with connection.makefile("rb") as stream:
                header = receive_line(stream)
                if header.get("status") != "PASS":
                    raise RuntimeError("Isolated renderer failed")
                entries = header.get("files")
                if not isinstance(entries, list) or not 1 <= len(entries) <= 53:
                    raise ValueError("Invalid renderer file inventory")
                result: dict[str, bytes] = {}
                total = 0
                for item in entries:
                    name, size = item["name"], item["size"]
                    if (
                        not isinstance(name, str)
                        or name in result
                        or type(size) is not int
                        or not 0 < size <= MAX_FILE
                    ):
                        raise ValueError("Invalid renderer file")
                    total += size
                    if total > MAX_FILE:
                        raise ValueError("Renderer output exceeds budget")
                    payload = stream.read(size)
                    if len(payload) != size or digest(payload) != item["sha256"]:
                        raise ValueError("Renderer output identity changed")
                    result[name] = payload
                report = json.loads(result["report.json"])
                validate_evidence(report)
                if (
                    report["sourceSha256"] != digest(workbook)
                    or report["format"] != mode
                    or report["execution"]["imageId"] != image
                ):
                    raise ValueError("Renderer input or runtime changed")
                expected = {"report.json"}
                if mode != "fods":
                    expected.add("page.pdf")
                    if digest(result["page.pdf"]) != report["pdfSha256"]:
                        raise ValueError("Renderer PDF changed")
                if mode in {"fods", "preview"}:
                    expected.add("page.fods")
                    if digest(result["page.fods"]) != report["fodsSha256"]:
                        raise ValueError("Renderer dimensions changed")
                if mode in {"reference", "preview"}:
                    expected.update(f"page-{n}.png" for n in range(1, report["pageCount"] + 1))
                    if [
                        digest(result[f"page-{n}.png"]) for n in range(1, report["pageCount"] + 1)
                    ] != report["imageSha256"]:
                        raise ValueError("Renderer pages changed")
                if set(result) != expected:
                    raise ValueError("Unexpected renderer output")
                return result, report
        finally:
            stopped.set()
            thread.join(timeout=1)
