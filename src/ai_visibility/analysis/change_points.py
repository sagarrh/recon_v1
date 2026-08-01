from __future__ import annotations

from typing import Any

import numpy as np
import ruptures as rpt


def detect_change_points(values: list[float]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        if abs(delta) >= 0.2:
            points.append(
                {
                    "index": index,
                    "method": "material_adjacent_delta",
                    "delta": delta,
                }
            )
    if len(values) >= 8 and float(np.std(values)) > 1e-9:
        series = np.asarray(values, dtype=float).reshape(-1, 1)
        detected = (
            rpt.Pelt(model="l2", min_size=2)
            .fit(series)
            .predict(pen=max(0.05, float(np.var(series)) * 2))
        )
        known = {int(item["index"]) for item in points}
        for index in detected[:-1]:
            if index not in known:
                points.append({"index": int(index), "method": "ruptures_pelt", "delta": None})
    return sorted(points, key=lambda item: int(item["index"]))
