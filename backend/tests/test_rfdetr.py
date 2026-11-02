"""RF-DETR adapter. Skipped unless requirements-ml.txt is installed (not in CI: torch is several GB).

This only proves the adapter loads the model and maps its output; how well COCO weights find
players and the ball in Veo panoramas is unknown until there's footage.
"""
import numpy as np
import pytest

rfdetr = pytest.importorskip("rfdetr")

from app.pipeline.detection import Detection, DetectorUnavailable, RFDETRDetector  # noqa: E402


@pytest.fixture(scope="module")
def detector():
    return RFDETRDetector("small")


def test_runs_on_a_frame_and_returns_detections(detector):
    frame = np.zeros((400, 960, 3), np.uint8)
    frame[:] = (34, 110, 52)  # an empty pitch: nothing to find
    out = detector.detect(frame)
    assert isinstance(out, list) and all(isinstance(d, Detection) and d.cls in ("player", "ball") for d in out)
    assert sum(d.cls == "ball" for d in out) <= 1


def test_maps_coco_classes_and_keeps_one_ball(detector, monkeypatch):
    class Fake:  # what model.predict returns: supervision Detections-like
        xyxy = np.array([[10, 10, 20, 40], [50, 50, 54, 54], [60, 60, 63, 63], [0, 0, 5, 5]], float)
        class_id = np.array([1, 37, 37, 3])            # person, sports ball, sports ball, car
        confidence = np.array([0.9, 0.2, 0.6, 0.99])
    monkeypatch.setattr(detector.model, "predict", lambda img, threshold: Fake())
    out = detector.detect(np.zeros((100, 100, 3), np.uint8))
    assert [(d.cls, d.score) for d in out] == [("player", 0.9), ("ball", 0.6)]


def test_rejects_unknown_size():
    with pytest.raises(DetectorUnavailable, match="SIZE"):
        RFDETRDetector("enormous")
