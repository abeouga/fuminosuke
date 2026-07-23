from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class HandwritingSegmenter(Protocol):
    def predict(self, image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]: ...

