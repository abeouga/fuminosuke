import numpy as np
from numpy.typing import NDArray

from app.segmentation.base import HandwritingSegmenter


class CombinedHandwritingSegmenter:
    def __init__(
        self,
        heuristic: HandwritingSegmenter,
        model: HandwritingSegmenter | None = None,
        *,
        model_weight: float = 0.6,
    ) -> None:
        self._heuristic = heuristic
        self._model = model
        self._model_weight = model_weight

    def predict(self, image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
        heuristic_probability = self._heuristic.predict(image_rgb)
        if self._model is None:
            return heuristic_probability
        model_probability = self._model.predict(image_rgb)
        return np.clip(
            heuristic_probability * (1.0 - self._model_weight)
            + model_probability * self._model_weight,
            0,
            1,
        ).astype(np.float32)

