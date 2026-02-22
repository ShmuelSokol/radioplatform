from timezonefinder import TimezoneFinder

_tf = None


def get_timezone_for_coords(latitude: float, longitude: float) -> str:
    global _tf
    if _tf is None:
        _tf = TimezoneFinder()
    return _tf.timezone_at(lat=latitude, lng=longitude) or "UTC"
