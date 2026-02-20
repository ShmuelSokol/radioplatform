from pydantic import BaseModel


class MixRequest(BaseModel):
    backtrack_asset_id: str
    overlay_asset_id: str
    output_title: str
    output_asset_type: str = "spot"
    bt_trim_start: float = 0.0
    bt_trim_end: float = 0.0
    bt_target_dur: float = 0.0
    bt_volume: float = 0.2
    ov_volume: float = 1.0
    bt_fade_in: float = 0.0
    bt_fade_out: float = 2.0
    bt_fade_out_start: float = 0.0
    ov_fade_in: float = 0.0
    ov_fade_out: float = 0.0
    ov_fade_out_start: float = 0.0
