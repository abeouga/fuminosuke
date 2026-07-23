from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.core.errors import AppError


class OnnxHandwritingSegmenter:
    """Interface boundary; model loading is deliberately opt-in and never downloads a model."""

    def __init__(self, model_path: Path | None) -> None:
        self._model_path = model_path

    def predict(self, image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
        del image_rgb
        if self._model_path is None or not self._model_path.is_file():
            raise AppError(
                status=503,
                code="MODEL_NOT_CONFIGURED",
                title="Handwriting model is not configured",
                detail="ライセンス確認済みONNXモデルが設定されていません。",
            )
        raise AppError(
            status=501,
            code="MODEL_NOT_CONFIGURED",
            title="ONNX runtime is not enabled",
            detail="このローカル構成ではONNX推論を有効化していません。",
        )

