import { useEffect, useRef, useState } from "react";

export function CameraCapture({
  documentAspect,
  onCapture,
  onClose,
}: {
  documentAspect: number;
  onCapture: (file: File) => void;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const previousFrame = useRef<Uint8Array | null>(null);
  const stableCount = useRef(0);
  const capturing = useRef(false);
  const [status, setStatus] = useState("カメラを準備しています…");
  const [ready, setReady] = useState(false);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState(false);

  const stop = () => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const capture = async () => {
    if (capturing.current) return;
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) {
      setStatus("カメラ映像を準備中です。映像が表示されてから撮影してください。");
      return;
    }
    capturing.current = true;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.94));
    if (blob) {
      const file = new File(
        [blob],
        `camera_${new Date().toISOString().replace(/[:.]/g, "-")}.jpg`,
        { type: "image/jpeg" },
      );
      onCapture(file);
      stop();
      onClose();
    } else {
      capturing.current = false;
      setStatus("撮影画像を作成できませんでした。手動でもう一度お試しください。");
    }
  };

  useEffect(() => {
    let cancelled = false;
    const start = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        const video = videoRef.current;
        if (!video) {
          stop();
          return;
        }
        video.srcObject = stream;
        await video.play();
        if (cancelled) {
          stop();
          return;
        }
        setCameraActive(true);
        setCameraError(false);
        setStatus("ガイド枠へ帳票の四辺を合わせ、端末を静止してください。");
        const analysisCanvas = document.createElement("canvas");
        analysisCanvas.width = 180;
        analysisCanvas.height = 130;
        const context = analysisCanvas.getContext("2d", { willReadFrequently: true });
        timerRef.current = window.setInterval(() => {
          if (!context || !video.videoWidth || capturing.current) return;
          context.drawImage(video, 0, 0, analysisCanvas.width, analysisCanvas.height);
          const pixels = context.getImageData(0, 0, analysisCanvas.width, analysisCanvas.height).data;
          const gray = new Uint8Array(analysisCanvas.width * analysisCanvas.height);
          for (let index = 0, pixel = 0; index < pixels.length; index += 4, pixel += 1) {
            gray[pixel] = Math.round(pixels[index] * 0.299 + pixels[index + 1] * 0.587 + pixels[index + 2] * 0.114);
          }
          const previous = previousFrame.current;
          previousFrame.current = gray;
          if (!previous) return;
          let movement = 0;
          for (let index = 0; index < gray.length; index += 8) {
            movement += Math.abs(gray[index] - previous[index]);
          }
          movement /= gray.length / 8;
          const edgeScore = guideEdgeScore(gray, analysisCanvas.width, analysisCanvas.height, documentAspect);
          const aligned = movement < 5.5 && edgeScore > 9;
          stableCount.current = aligned ? stableCount.current + 1 : 0;
          setReady(aligned);
          if (stableCount.current >= 5) {
            setStatus("位置が合いました。自動撮影します。");
            void capture();
          } else if (aligned) {
            setStatus(`位置を確認中… ${stableCount.current}/5`);
          }
        }, 350);
      } catch (error) {
        stop();
        setCameraActive(false);
        setCameraError(true);
        setStatus(cameraErrorMessage(error));
      }
    };
    void start();
    return () => {
      cancelled = true;
      stop();
    };
  // The camera lifecycle is intentionally bound to this mounted modal.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="camera-dialog" role="dialog" aria-modal="true" aria-label="帳票を撮影">
      <div className="camera-window">
        <div className="camera-titlebar">
          <strong>帳票カメラ</strong>
          <button type="button" onClick={() => { stop(); onClose(); }}>閉じる</button>
        </div>
        <div className="camera-viewport">
          <video ref={videoRef} playsInline muted />
          <div
            className={`camera-guide ${ready ? "ready" : ""}`}
            style={{ aspectRatio: String(documentAspect || 1.414) }}
          >
            <i /><i /><i /><i />
          </div>
          <div className="camera-fade camera-fade-top" />
          <div className="camera-fade camera-fade-bottom" />
        </div>
        <div className="camera-status" role={cameraError ? "alert" : "status"} aria-live="polite">
          <span className={ready ? "camera-ready-light ready" : "camera-ready-light"} />
          <p>{status}</p>
          <button type="button" className="primary" disabled={!cameraActive} onClick={() => void capture()}>手動シャッター</button>
        </div>
      </div>
    </div>
  );
}

function cameraErrorMessage(error: unknown) {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "カメラの使用が許可されていません。ブラウザの設定で許可するか、「画像を選択」をご利用ください。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "利用できるカメラが見つかりません。「画像を選択」から帳票画像を追加してください。";
  }
  return "カメラを開始できません。ブラウザのカメラ設定を確認するか、「画像を選択」をご利用ください。";
}

function guideEdgeScore(
  gray: Uint8Array,
  width: number,
  height: number,
  aspect: number,
) {
  const maxWidth = width * 0.78;
  const maxHeight = height * 0.78;
  let guideWidth = maxWidth;
  let guideHeight = guideWidth / Math.max(0.4, aspect);
  if (guideHeight > maxHeight) {
    guideHeight = maxHeight;
    guideWidth = guideHeight * aspect;
  }
  const left = Math.round((width - guideWidth) / 2);
  const right = Math.round(left + guideWidth);
  const top = Math.round((height - guideHeight) / 2);
  const bottom = Math.round(top + guideHeight);
  const sample = (x: number, y: number) => gray[Math.max(0, Math.min(height - 1, y)) * width + Math.max(0, Math.min(width - 1, x))];
  let total = 0;
  let count = 0;
  for (let x = left; x <= right; x += 5) {
    total += Math.abs(sample(x, top - 2) - sample(x, top + 2));
    total += Math.abs(sample(x, bottom - 2) - sample(x, bottom + 2));
    count += 2;
  }
  for (let y = top; y <= bottom; y += 5) {
    total += Math.abs(sample(left - 2, y) - sample(left + 2, y));
    total += Math.abs(sample(right - 2, y) - sample(right + 2, y));
    count += 2;
  }
  return count ? total / count : 0;
}
