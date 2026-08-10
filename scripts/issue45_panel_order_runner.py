from __future__ import annotations

import asyncio

import cv2
import numpy as np

import issue45_panel_order_experiment as experiment


def _line_segments(gray: np.ndarray) -> tuple[tuple[float, float, float, float], ...]:
    detected = cv2.createLineSegmentDetector(0).detect(gray)
    if detected[0] is None:
        return ()
    lines = np.asarray(detected[0]).reshape(-1, 4)
    return tuple(tuple(float(value) for value in line) for line in lines)


experiment._line_segments = _line_segments


if __name__ == "__main__":
    asyncio.run(experiment.main())
