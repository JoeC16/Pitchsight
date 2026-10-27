from .interfaces import Detector


def get_detector(name: str) -> Detector:
    if name == "stub":
        from .stub import StubDetector

        return StubDetector()
    if name == "real":
        from .real import TrackingDetector

        return TrackingDetector()
    raise ValueError(f"Unknown detector '{name}' (expected 'stub' or 'real')")
